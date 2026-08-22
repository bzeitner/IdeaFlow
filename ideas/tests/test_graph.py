import json

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ideas.graph.projection import graph_context, graph_projection, neighborhood
from ideas.graph.revision import current_revision
from ideas.graph.semantic import content_hash, process_idea, semantic_text
from ideas.models import IdeaRelation, IdeaRelationSuggestion, IdeaSemanticState, RelationType, RelationshipCouncilReview, ResearchEntry, SemanticGraphSettings, SemanticStatus, Status, SuggestionStatus

from .helpers import MODEL_BACKEND, make_ai_model, make_idea, make_user

TOKEN = "graph-test-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


class IdeaRelationTests(TestCase):
    def test_rejects_self_relation(self):
        idea = make_idea()
        with self.assertRaises(ValidationError):
            IdeaRelation.objects.create(source=idea, target=idea, relation_type=RelationType.RELATED_TO)

    def test_rejects_dependency_cycle(self):
        first, second, third = make_idea(), make_idea(), make_idea()
        IdeaRelation.objects.create(source=first, target=second, relation_type=RelationType.DEPENDS_ON)
        IdeaRelation.objects.create(source=second, target=third, relation_type=RelationType.DEPENDS_ON)
        with self.assertRaises(ValidationError):
            IdeaRelation.objects.create(source=third, target=first, relation_type=RelationType.DEPENDS_ON)

    def test_symmetric_relation_is_canonicalized(self):
        first, second = make_idea(), make_idea()
        relation = IdeaRelation.objects.create(source=second, target=first, relation_type=RelationType.RELATED_TO)
        self.assertLess(relation.source_id, relation.target_id)

    def test_graph_revision_advances_after_commit(self):
        before = current_revision()
        with self.captureOnCommitCallbacks(execute=True):
            make_idea()
        self.assertGreater(current_revision(), before)

    def test_category_change_advances_revision(self):
        idea = make_idea()
        before = current_revision()
        with self.captureOnCommitCallbacks(execute=True):
            idea.category.color = "#123456"
            idea.category.save()
        self.assertGreater(current_revision(), before)


class GraphProjectionTests(TestCase):
    def test_projects_parent_and_explicit_edges(self):
        parent = make_idea(title="Parent")
        child = make_idea(title="Child", parent=parent)
        other = make_idea(title="Other")
        IdeaRelation.objects.create(source=child, target=other, relation_type=RelationType.DEPENDS_ON)
        types = {edge["type"] for edge in graph_projection()["edges"]}
        self.assertIn("parent_of", types)
        self.assertIn("depends_on", types)

    def test_neighborhood_is_bounded_and_context_is_grouped(self):
        center = make_idea(title="Center")
        dependency = make_idea(title="Dependency")
        IdeaRelation.objects.create(source=center, target=dependency, relation_type=RelationType.DEPENDS_ON)
        self.assertLessEqual(len(neighborhood(center, max_nodes=1)["nodes"]), 1)
        context = graph_context(center)
        self.assertEqual([row["idea_id"] for row in context["dependencies"]], [dependency.pk])

    def test_context_budget_prioritizes_dependencies_for_execution(self):
        center = make_idea(title="Center")
        for number in range(20):
            dependency = make_idea(title=f"Dependency {number}", summary="x" * 500)
            IdeaRelation.objects.create(
                source=center,
                target=dependency,
                relation_type=RelationType.DEPENDS_ON,
            )
        context = graph_context(center, max_nodes=30, token_budget=500, task="execute")
        self.assertLess(context["budget"]["included_relations"], 20)
        self.assertGreater(context["budget"]["omitted_relations"], 0)
        self.assertLessEqual(context["budget"]["estimated_tokens"], 550)
        self.assertEqual(context["task"], "execute")

    def test_repeat_task_prioritizes_related_over_dependencies(self):
        # A recurring task (e.g. a podcast idea) cares about a "supports"
        # relation — which lands in "related" — more than structural
        # dependencies, so under a tight budget "related" should survive
        # while "dependencies" gets squeezed out.
        center = make_idea(title="Center")
        supporter = make_idea(title="Supporter", summary="x" * 500)
        IdeaRelation.objects.create(
            source=supporter, target=center, relation_type=RelationType.SUPPORTS
        )
        for number in range(10):
            dependency = make_idea(title=f"Dependency {number}", summary="x" * 500)
            IdeaRelation.objects.create(
                source=center, target=dependency, relation_type=RelationType.DEPENDS_ON
            )
        context = graph_context(center, max_nodes=30, token_budget=500, task="repeat")
        self.assertEqual(context["task"], "repeat")
        self.assertEqual([row["idea_id"] for row in context["related"]], [supporter.pk])
        self.assertLess(len(context["dependencies"]), 10)


class GraphViewTests(TestCase):
    def test_graph_role_can_open_tab(self):
        make_idea(title="Visible node")
        user = make_user(roles=["role_graph"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:graph"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible node")

    def test_other_role_cannot_open_graph(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:graph"))
        self.assertRedirects(response, reverse("ideas:home"), fetch_redirect_response=False)

    def test_relation_edit_requires_source_status_role(self):
        source, target = make_idea(), make_idea()
        user = make_user(roles=["role_graph"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        self.client.post(reverse("ideas:graph_relation_create"), {"source": source.pk, "target": target.pk, "relation_type": "related_to", "confidence": 5})
        self.assertFalse(IdeaRelation.objects.exists())


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class RelationshipCouncilApiTests(TestCase):
    def setUp(self):
        self.source = make_idea(title="Council source", status=Status.TRACKING)
        self.target = make_idea(title="Council target", status=Status.TRACKING)
        self.suggestion = IdeaRelationSuggestion.objects.create(
            analyzed_idea=self.source,
            source=self.source,
            target=self.target,
            relation_type=RelationType.SUPPORTS,
            description="The source supports the target.",
            evidence="Both cite the same measured result.",
            confidence=0.75,
            source_content_hash="source-hash",
            target_content_hash="target-hash",
            classifier_model="test",
        )
        self.persona_ids = list(
            self.source.idea_personas.filter(active=True, required=True)
            .order_by("persona__name")
            .values_list("persona_id", flat=True)
        )

    def votes(self, decisions, providers=("claude", "codex", "claude")):
        return [
            {
                "persona_id": persona_id,
                "provider": provider,
                "model": f"{provider}-test",
                "decision": decision,
                "rationale": f"Evidence supports {decision}.",
            }
            for persona_id, provider, decision in zip(
                self.persona_ids, providers, decisions, strict=True
            )
        ]

    def submit(self, decisions, providers=("claude", "codex", "claude")):
        return self.client.post(
            f"/api/relationship-council-reviews/{self.suggestion.pk}/",
            data=json.dumps({"votes": self.votes(decisions, providers)}),
            content_type="application/json",
            **AUTH,
        )

    def test_queue_returns_three_required_personas(self):
        response = self.client.get(
            "/api/relationship-council-reviews/?limit=5", **AUTH
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["suggestions"][0]
        self.assertEqual(item["suggestion_id"], self.suggestion.pk)
        self.assertEqual(len(item["personas"]), 3)

    def test_all_three_accept_promotes_relationship(self):
        response = self.submit(("accept", "accept", "accept"))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["outcome"], "accepted")
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, SuggestionStatus.ACCEPTED)
        self.assertTrue(IdeaRelation.objects.filter(source=self.source, target=self.target).exists())

    def test_two_reject_votes_reject_suggestion(self):
        response = self.submit(("reject", "reject", "accept"))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["outcome"], "rejected")
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, SuggestionStatus.REJECTED)

    def test_split_vote_marks_reviewed_without_decision(self):
        response = self.submit(("accept", "reject", "abstain"))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["outcome"], "no_decision")
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, SuggestionStatus.PENDING)
        self.assertEqual(
            self.suggestion.relationship_council_review.outcome,
            RelationshipCouncilReview.Outcome.NO_DECISION,
        )
        user = make_user(roles=["role_graph"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        graph = self.client.get(reverse("ideas:graph"))
        self.assertContains(graph, "Council reviewed · no decision")
        self.assertContains(graph, "Council votes")

    def test_both_claude_and_codex_are_required(self):
        response = self.submit(
            ("accept", "accept", "accept"),
            providers=("claude", "claude", "claude"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Claude", response.json()["error"])


class GraphSuggestionViewTests(TestCase):
    def test_manager_can_create_relation(self):
        source, target = make_idea(status=Status.CURRENT), make_idea()
        user = make_user(roles=["role_graph", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        self.client.post(reverse("ideas:graph_relation_create"), {"source": source.pk, "target": target.pk, "relation_type": "related_to", "confidence": 4})
        self.assertTrue(IdeaRelation.objects.exists())

    def test_manager_can_accept_semantic_suggestion(self):
        source, target = make_idea(status=Status.CURRENT), make_idea()
        suggestion = IdeaRelationSuggestion.objects.create(
            analyzed_idea=source, source=source, target=target, relation_type=RelationType.SUPPORTS,
            description="Shared evidence", evidence="Research overlaps", confidence=0.82,
            source_content_hash="a", target_content_hash="b", classifier_model="test",
        )
        user = make_user(roles=["role_graph", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.post(reverse("ideas:graph_suggestion_review", args=[suggestion.pk, "accept"]))
        self.assertRedirects(response, reverse("ideas:graph"), fetch_redirect_response=False)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, SuggestionStatus.ACCEPTED)
        self.assertEqual(suggestion.accepted_relation.provenance, "agent")

    def test_ajax_accept_returns_edge_without_redirect(self):
        source, target = make_idea(status=Status.CURRENT), make_idea()
        suggestion = IdeaRelationSuggestion.objects.create(
            analyzed_idea=source, source=source, target=target,
            relation_type=RelationType.SUPPORTS, source_content_hash="a",
            target_content_hash="b", classifier_model="test",
        )
        user = make_user(roles=["role_graph", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.post(
            reverse("ideas:graph_suggestion_review", args=[suggestion.pk, "accept"]),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["decision"], "accept")
        self.assertEqual(response.json()["edge"]["type"], RelationType.SUPPORTS)

    def test_ajax_accept_dependency_cycle_returns_reviewable_error(self):
        first = make_idea(status=Status.CURRENT)
        second = make_idea()
        IdeaRelation.objects.create(
            source=second, target=first, relation_type=RelationType.DEPENDS_ON
        )
        suggestion = IdeaRelationSuggestion.objects.create(
            analyzed_idea=first, source=first, target=second,
            relation_type=RelationType.DEPENDS_ON, confidence=0.95,
            source_content_hash="a", target_content_hash="b", classifier_model="test",
        )
        user = make_user(roles=["role_graph", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.post(
            reverse("ideas:graph_suggestion_review", args=[suggestion.pk, "accept"]),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("cycle", response.json()["error"].lower())
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, SuggestionStatus.PENDING)

    def test_rejection_does_not_create_relation(self):
        source, target = make_idea(status=Status.CURRENT), make_idea()
        suggestion = IdeaRelationSuggestion.objects.create(
            analyzed_idea=source, source=source, target=target, relation_type=RelationType.RELATED_TO,
            source_content_hash="a", target_content_hash="b", classifier_model="test",
        )
        user = make_user(roles=["role_graph", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        self.client.post(reverse("ideas:graph_suggestion_review", args=[suggestion.pk, "reject"]))
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, SuggestionStatus.REJECTED)
        self.assertFalse(IdeaRelation.objects.exists())


class FakeSemanticAPI:
    embedding_model = "fake-embedding"
    classifier_model = "fake-classifier"
    confidence = 0.9
    relation_type = "supports"

    def embed(self, text):
        return [1.0] + [0.0] * 1535

    def classify(self, source, candidates):
        return [{
            "candidate_id": candidates[0][0].pk,
            "relation_type": self.relation_type,
            "confidence": self.confidence,
            "description": "The research supports the target.",
            "evidence": "Both discuss the same measured outcome.",
        }]


class SemanticGraphTests(TestCase):
    def ready_target(self, title="Target evidence"):
        target = make_idea(title=title)
        state = target.semantic_state
        state.content_hash = content_hash(semantic_text(target))
        state.embedding = [1.0] + [0.0] * 1535
        state.embedding_model = "fake-embedding"
        state.status = SemanticStatus.READY
        state.processed_at = timezone.now()
        state.save()
        return target

    def test_idea_and_research_changes_mark_semantics_stale(self):
        idea = make_idea()
        state = idea.semantic_state
        state.status = SemanticStatus.READY
        state.save(update_fields=["status"])
        ResearchEntry.objects.create(idea=idea, topic="New finding", model=make_ai_model())
        state.refresh_from_db()
        self.assertEqual(state.status, SemanticStatus.STALE)

    def test_processing_creates_reviewable_suggestion(self):
        target = self.ready_target()
        source = make_idea(title="Source evidence")
        process_idea(source, api=FakeSemanticAPI())
        suggestion = IdeaRelationSuggestion.objects.get()
        self.assertEqual(suggestion.target, target)
        self.assertEqual(suggestion.status, SuggestionStatus.PENDING)
        self.assertEqual(suggestion.evidence, "Both discuss the same measured outcome.")
        source.semantic_state.refresh_from_db()
        self.assertEqual(source.semantic_state.status, SemanticStatus.READY)

    def test_confidence_over_admin_threshold_is_auto_accepted(self):
        target = self.ready_target("Strong target")
        SemanticGraphSettings.objects.update_or_create(
            pk=1, defaults={"auto_accept_confidence_percent": 90}
        )
        api = FakeSemanticAPI()
        api.confidence = 0.91

        process_idea(make_idea(title="Strong source"), api=api)

        suggestion = IdeaRelationSuggestion.objects.get(target=target)
        self.assertEqual(suggestion.status, SuggestionStatus.ACCEPTED)
        self.assertIsNotNone(suggestion.accepted_relation)
        self.assertEqual(suggestion.accepted_relation.provenance, "agent")

    def test_admin_threshold_is_strict_and_configurable(self):
        self.ready_target("Configurable target")
        SemanticGraphSettings.objects.update_or_create(
            pk=1, defaults={"auto_accept_confidence_percent": 89}
        )

        process_idea(make_idea(title="Configurable source"), api=FakeSemanticAPI())

        self.assertEqual(
            IdeaRelationSuggestion.objects.get().status, SuggestionStatus.ACCEPTED
        )

    def test_processing_does_not_recommend_dependency_cycle(self):
        target = self.ready_target("Cycle target")
        source = make_idea(title="Cycle source")
        IdeaRelation.objects.create(
            source=target, target=source, relation_type=RelationType.DEPENDS_ON
        )
        api = FakeSemanticAPI()
        api.confidence = 0.95
        api.relation_type = RelationType.DEPENDS_ON

        process_idea(source, api=api)

        self.assertFalse(IdeaRelationSuggestion.objects.exists())

    def test_deleting_idea_with_research_does_not_recreate_state(self):
        idea = make_idea()
        ResearchEntry.objects.create(idea=idea, topic="Finding", model=make_ai_model())
        idea_id = idea.pk
        idea.delete()
        self.assertFalse(IdeaSemanticState.objects.filter(idea_id=idea_id).exists())


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class GraphApiTests(TestCase):
    def test_graph_context_is_authenticated_and_bounded(self):
        idea = make_idea()
        self.assertEqual(self.client.get(f"/api/ideas/{idea.pk}/graph-context/").status_code, 401)
        response = self.client.get(f"/api/ideas/{idea.pk}/graph-context/?depth=1&max_nodes=5", **AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["idea"]["idea_id"], idea.pk)

    def test_graph_context_accepts_agent_task_and_token_budget(self):
        idea = make_idea()
        response = self.client.get(
            f"/api/ideas/{idea.pk}/graph-context/?task=execute&token_budget=700",
            **AUTH,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"], "execute")
        self.assertEqual(response.json()["budget"]["requested_tokens"], 700)

    def test_graph_endpoint_excludes_archived_by_default(self):
        visible, hidden = make_idea(), make_idea(status=Status.ARCHIVED)
        ids = {node["idea_id"] for node in self.client.get("/api/graph/", **AUTH).json()["nodes"]}
        self.assertIn(visible.pk, ids)
        self.assertNotIn(hidden.pk, ids)

    def test_graph_search_finds_idea_text(self):
        idea = make_idea(title="Quantum Orchard")
        make_idea(title="Unrelated")
        response = self.client.get("/api/graph/search/?q=quantum", **AUTH)
        self.assertEqual([row["idea_id"] for row in response.json()["results"]], [idea.pk])
