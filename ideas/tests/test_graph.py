from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from ideas.graph.projection import graph_context, graph_projection, neighborhood
from ideas.graph.revision import current_revision
from ideas.models import IdeaRelation, RelationType, Status

from .helpers import MODEL_BACKEND, make_idea, make_user

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

    def test_manager_can_create_relation(self):
        source, target = make_idea(status=Status.CURRENT), make_idea()
        user = make_user(roles=["role_graph", "role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        self.client.post(reverse("ideas:graph_relation_create"), {"source": source.pk, "target": target.pk, "relation_type": "related_to", "confidence": 4})
        self.assertTrue(IdeaRelation.objects.exists())


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class GraphApiTests(TestCase):
    def test_graph_context_is_authenticated_and_bounded(self):
        idea = make_idea()
        self.assertEqual(self.client.get(f"/api/ideas/{idea.pk}/graph-context/").status_code, 401)
        response = self.client.get(f"/api/ideas/{idea.pk}/graph-context/?depth=1&max_nodes=5", **AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["idea"]["idea_id"], idea.pk)

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
