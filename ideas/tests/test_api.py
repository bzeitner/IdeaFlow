import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from ideas.models import Artifact, AIModel, Episode, EpisodeRun, EpisodeRunStatus, IdeaPersona, Persona, PersonaReview, RepeatResult, RepeatResultStatus, ResearchEntry, Status, VoiceProfile

from .helpers import make_idea, make_podcast_show, make_stage, make_user

TOKEN = "test-token-123"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiAuthTests(TestCase):
    def test_missing_token_is_rejected(self):
        idea = make_idea()
        response = self.client.get(f"/api/ideas/{idea.pk}/")
        self.assertEqual(response.status_code, 401)

    def test_wrong_token_is_rejected(self):
        idea = make_idea()
        response = self.client.get(
            f"/api/ideas/{idea.pk}/", HTTP_AUTHORIZATION="Bearer nope"
        )
        self.assertEqual(response.status_code, 401)

    def test_x_api_token_header_also_works(self):
        idea = make_idea()
        response = self.client.get(f"/api/ideas/{idea.pk}/", HTTP_X_API_TOKEN=TOKEN)
        self.assertEqual(response.status_code, 200)


@override_settings(IDEAFLOW_API_TOKEN="")
class ApiDisabledTests(TestCase):
    def test_empty_token_setting_disables_the_api(self):
        idea = make_idea()
        response = self.client.get(f"/api/ideas/{idea.pk}/", **AUTH)
        self.assertEqual(response.status_code, 503)


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiReadTests(TestCase):
    def test_list_returns_all_ideas(self):
        make_idea(title="One")
        make_idea(title="Two", status=Status.TRACKING)
        response = self.client.get("/api/ideas/", **AUTH)
        self.assertEqual(response.status_code, 200)
        titles = {i["title"] for i in response.json()["ideas"]}
        self.assertEqual(titles, {"One", "Two"})

    def test_list_filters_by_status(self):
        make_idea(title="Cur", status=Status.CURRENT)
        make_idea(title="Trk", status=Status.TRACKING)
        response = self.client.get("/api/ideas/?status=tracking", **AUTH)
        titles = [i["title"] for i in response.json()["ideas"]]
        self.assertEqual(titles, ["Trk"])

    def test_detail_includes_related_collections(self):
        idea = make_idea(title="Deep", notes="secret notes")
        idea.resources.create(label="Docs", url="https://example.com")
        response = self.client.get(f"/api/ideas/{idea.pk}/", **AUTH)
        body = response.json()
        self.assertEqual(body["title"], "Deep")
        self.assertEqual(body["notes"], "secret notes")
        self.assertEqual(body["resources"][0]["url"], "https://example.com")
        self.assertIsInstance(body["resources"][0]["id"], int)
        self.assertIn("research_entries", body)
        self.assertIn("artifacts", body)

    def test_summary_upload_upserts_and_completes_request_even_when_archived(self):
        idea = make_idea(status=Status.ARCHIVED, summary_requested_at=timezone.now())
        response = self.client.post(
            f"/api/ideas/{idea.pk}/artifacts/",
            {
                "title": "Summary",
                "kind": "summary",
                "description": "High-level report",
                "file": SimpleUploadedFile("summary.md", b"# Executive summary\nUseful."),
            },
            **AUTH,
        )
        self.assertEqual(response.status_code, 201)
        artifact = idea.artifacts.get(kind=Artifact.Kind.SUMMARY)
        self.addCleanup(artifact.file.delete, save=False)
        idea.refresh_from_db()
        self.assertIsNone(idea.summary_requested_at)

        replacement = self.client.post(
            f"/api/ideas/{idea.pk}/artifacts/",
            {
                "title": "Summary",
                "kind": "summary",
                "file": SimpleUploadedFile("summary.md", b"# Executive summary\nUpdated."),
            },
            **AUTH,
        )
        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(idea.artifacts.filter(kind=Artifact.Kind.SUMMARY).count(), 1)
        artifact.refresh_from_db()
        self.addCleanup(artifact.file.delete, save=False)

    def test_detail_404_for_unknown_idea(self):
        response = self.client.get("/api/ideas/999999/", **AUTH)
        self.assertEqual(response.status_code, 404)

    def test_delete_resource_is_scoped_to_idea(self):
        idea = make_idea()
        other = make_idea(title="Other")
        resource = idea.resources.create(
            label="PR", url="https://github.com/x/y/pull/1"
        )

        wrong = self.client.delete(
            f"/api/ideas/{other.pk}/resources/{resource.pk}/", **AUTH
        )
        self.assertEqual(wrong.status_code, 404)
        self.assertTrue(idea.resources.filter(pk=resource.pk).exists())

        response = self.client.delete(
            f"/api/ideas/{idea.pk}/resources/{resource.pk}/", **AUTH
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"]["id"], resource.pk)
        self.assertFalse(idea.resources.filter(pk=resource.pk).exists())

    def test_reconcile_closed_pr_removes_resource_and_advances_queue(self):
        url = "https://github.com/x/y/pull/17"
        idea = make_idea(
            next_action=f"Review {url}",
            next_actions=[f"Review {url}", "Deploy the accepted change"],
            agent_runs_since_feedback=2,
        )
        resource = idea.resources.create(label="PR", url=url)

        response = self.client.post(
            f"/api/ideas/{idea.pk}/reconcile-pr/",
            data=json.dumps({"url": url, "state": "MERGED"}),
            content_type="application/json",
            **AUTH,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["removed_resource_ids"], [resource.pk])
        self.assertTrue(response.json()["advanced_next_action"])
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "Deploy the accepted change")
        self.assertEqual(idea.agent_runs_since_feedback, 0)
        self.assertFalse(idea.resources.exists())

    def test_reconcile_pr_rejects_open_or_non_github_state(self):
        idea = make_idea(next_action="Keep this")
        for payload in (
            {"url": "https://github.com/x/y/pull/17", "state": "OPEN"},
            {"url": "https://example.com/pull/17", "state": "CLOSED"},
        ):
            response = self.client.post(
                f"/api/ideas/{idea.pk}/reconcile-pr/",
                data=json.dumps(payload),
                content_type="application/json",
                **AUTH,
            )
            self.assertEqual(response.status_code, 400)
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "Keep this")


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiEffortTests(TestCase):
    def _post(self, idea, payload):
        return self.client.post(
            f"/api/ideas/{idea.pk}/effort/",
            data=json.dumps(payload),
            content_type="application/json",
            **AUTH,
        )

    def test_records_a_research_entry(self):
        idea = make_idea()
        response = self._post(
            idea,
            {
                "topic": "Prototyped it",
                "model": "claude-opus-4-8",
                "context": "Built a spike.",
                "effort": 4,
                "quality": 5,
                "tokens_used": 12345,
            },
        )
        self.assertEqual(response.status_code, 201)
        entry = ResearchEntry.objects.get()
        self.assertEqual(entry.idea, idea)
        self.assertEqual(entry.topic, "Prototyped it")
        self.assertEqual(entry.effort, 4)
        self.assertEqual(entry.tokens_used, 12345)
        self.assertEqual(entry.model.slug, "claude-opus-4-8")

    def test_records_and_serializes_open_questions(self):
        idea = make_idea()
        response = self._post(
            idea,
            {
                "topic": "Needs a decision",
                "model": "other",
                "open_questions": ["Which market should we prioritize?"],
            },
        )

        self.assertEqual(response.status_code, 201)
        entry = ResearchEntry.objects.get()
        self.assertEqual(entry.open_questions, ["Which market should we prioritize?"])
        self.assertEqual(
            response.json()["research_entry"]["open_questions"],
            ["Which market should we prioritize?"],
        )

    def test_does_not_repeat_questions_already_open_on_the_idea(self):
        idea = make_idea()
        first = self._post(
            idea,
            {
                "topic": "First pass",
                "model": "other",
                "open_questions": ["Which market should we prioritize?"],
            },
        )
        second = self._post(
            idea,
            {
                "topic": "Second pass",
                "model": "other",
                "open_questions": ["  WHICH market should we prioritize?  ", "What budget?"],
            },
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(
            ResearchEntry.objects.get(topic="Second pass").open_questions,
            ["What budget?"],
        )

    def test_answered_historical_question_does_not_block_a_new_question(self):
        idea = make_idea()
        first = self._post(
            idea,
            {
                "topic": "First pass",
                "model": "other",
                "open_questions": ["Which market should we prioritize?"],
            },
        )
        entry = ResearchEntry.objects.get(pk=first.json()["research_entry"]["id"])
        entry.question_answers = {"0": "Enterprise"}
        entry.save(update_fields=["question_answers"])

        second = self._post(
            idea,
            {
                "topic": "Second pass",
                "model": "other",
                "open_questions": ["Which market should we prioritize?"],
            },
        )

        self.assertEqual(second.status_code, 201)
        self.assertEqual(
            ResearchEntry.objects.get(topic="Second pass").open_questions,
            ["Which market should we prioritize?"],
        )

    def test_records_actual_execution_identity_separately_from_routing_model(self):
        idea = make_idea()
        response = self._post(
            idea,
            {
                "topic": "Codex implementation",
                "model": "claude-opus-4-8",
                "execution_provider": "codex",
                "execution_model": "gpt-5-codex",
            },
        )

        self.assertEqual(response.status_code, 201)
        entry = ResearchEntry.objects.get()
        self.assertEqual(entry.model.slug, "claude-opus-4-8")
        self.assertEqual(entry.execution_provider, "codex")
        self.assertEqual(entry.execution_model, "gpt-5-codex")
        self.assertEqual(response.json()["research_entry"]["execution_model"], "gpt-5-codex")

    def test_extracts_open_questions_from_markdown_report(self):
        idea = make_idea()
        response = self._post(
            idea,
            {
                "topic": "Research report",
                "model": "other",
                "context": "## Evidence\nFound it.\n\n## Open questions\n- Which region?\n- What budget?\n\n## Risks\nNone.",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            ResearchEntry.objects.get().open_questions,
            ["Which region?", "What budget?"],
        )

    def test_attaches_a_result_resource(self):
        idea = make_idea()
        response = self._post(
            idea,
            {
                "topic": "Made a repo",
                "model": "other",
                "resource": {"label": "Repo", "url": "https://github.com/x/y"},
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(idea.resources.get().url, "https://github.com/x/y")

    def test_pr_resource_schedules_review_and_preserves_followup(self):
        idea = make_idea()
        pr_url = "https://github.com/x/y/pull/42"

        response = self._post(
            idea,
            {
                "topic": "Implemented it",
                "model": "other",
                "resource": {"label": "PR", "url": pr_url},
                "next_action": "Deploy after merge",
            },
        )

        self.assertEqual(response.status_code, 201)
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, f"Critical PR review: {pr_url}")
        self.assertEqual(
            idea.next_actions,
            [f"Critical PR review: {pr_url}", "Deploy after merge"],
        )

    def test_effort_can_append_queued_next_actions(self):
        idea = make_idea(next_action="Active")
        response = self._post(
            idea,
            {
                "topic": "Planned follow-ups",
                "model": "other",
                "queued_next_actions": ["Second", "Third"],
            },
        )

        self.assertEqual(response.status_code, 201)
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "Active")
        self.assertEqual(idea.next_actions, ["Active", "Second", "Third"])
        self.assertEqual(response.json()["idea"]["next_actions"], ["Active", "Second", "Third"])

    def test_queued_next_actions_must_be_a_list(self):
        idea = make_idea()
        response = self._post(
            idea,
            {"topic": "Bad queue", "model": "other", "queued_next_actions": "nope"},
        )
        self.assertEqual(response.status_code, 400)


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiPersonaReviewTests(TestCase):
    def setUp(self):
        self.idea = make_idea(persona_review_enabled=True, next_action="Stale action")
        self.idea.idea_personas.all().delete()
        self.personas = [
            Persona.objects.create(
                name=f"Council {index}", description="Role", goals="Progress", constraints="Reversible only"
            )
            for index in range(2)
        ]
        for persona in self.personas:
            IdeaPersona.objects.create(idea=self.idea, persona=persona, required=True)

    def post_review(self, votes, *, reversible=True, question_answers=None):
        return self.client.post(
            f"/api/ideas/{self.idea.pk}/persona-reviews/",
            data=json.dumps(
                {
                    "proposal": {
                        "summary": "Run a bounded test",
                        "action_type": "test",
                        "next_action": "Test the reversible validation approach",
                        "reversible": reversible,
                        "question_answers": question_answers or [],
                    },
                    "votes": votes,
                }
            ),
            content_type="application/json",
            **AUTH,
        )

    def test_required_personas_must_unanimously_approve(self):
        response = self.post_review(
            [
                {"persona_id": self.personas[0].pk, "decision": "approve"},
                {"persona_id": self.personas[1].pk, "decision": "abstain", "rationale": "Needs owner input"},
            ]
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "no_consensus")
        self.assertFalse(response.json()["acted"])
        self.idea.refresh_from_db()
        self.assertEqual(self.idea.next_action, "Stale action")

    def test_unanimous_reversible_proposal_sets_next_action(self):
        response = self.post_review(
            [
                {"persona_id": persona.pk, "decision": "approve", "rationale": "Within goals"}
                for persona in self.personas
            ]
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["acted"])
        self.idea.refresh_from_db()
        self.assertEqual(self.idea.next_action, "Test the reversible validation approach")

    def test_irreversible_proposal_is_rejected_even_with_unanimity(self):
        response = self.post_review(
            [{"persona_id": persona.pk, "decision": "approve"} for persona in self.personas],
            reversible=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("only", response.json()["error"])

    def test_every_required_persona_must_vote_explicitly(self):
        response = self.post_review(
            [{"persona_id": self.personas[0].pk, "decision": "approve"}]
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["missing_persona_ids"], [self.personas[1].pk])

    def test_consensus_answer_keeps_persona_provenance_not_human_provenance(self):
        entry = ResearchEntry.objects.create(
            idea=self.idea,
            topic="Decision",
            model=AIModel.objects.get(slug="other"),
            open_questions=["Which direction fits the project goals?"],
        )
        response = self.post_review(
            [{"persona_id": persona.pk, "decision": "approve"} for persona in self.personas],
            question_answers=[
                {"research_entry_id": entry.pk, "question_index": 0, "answer": "Run the smaller validation first."}
            ],
        )

        self.assertEqual(response.status_code, 201)
        entry.refresh_from_db()
        self.assertEqual(entry.question_answers, {})
        self.assertEqual(
            PersonaReview.objects.get(pk=response.json()["review_id"]).proposal["question_answers"][0]["answer"],
            "Run the smaller validation first.",
        )


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiResearchQuestionTests(TestCase):
    def _entry(self, idea, **kwargs):
        from ideas.reporting import record_effort

        entry, _resource = record_effort(
            idea, topic="Historical research", model="other", **kwargs
        )
        return entry

    def test_requires_api_token(self):
        idea = make_idea()
        entry = self._entry(idea)

        response = self.client.post(
            f"/api/ideas/{idea.pk}/research/{entry.pk}/open-questions/",
            data=json.dumps({"questions": ["Decide?"]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_additively_merges_without_changing_human_answers(self):
        idea = make_idea()
        entry = self._entry(idea, open_questions=["Existing question?"])
        entry.question_answers = {"0": "Existing answer"}
        entry.save(update_fields=["question_answers"])

        response = self.client.post(
            f"/api/ideas/{idea.pk}/research/{entry.pk}/open-questions/",
            data=json.dumps(
                {"questions": ["Existing question?", "New human decision?"]}
            ),
            content_type="application/json",
            **AUTH,
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(
            entry.open_questions,
            ["Existing question?", "New human decision?"],
        )
        self.assertEqual(entry.question_answers, {"0": "Existing answer"})

    def test_does_not_merge_a_question_open_on_another_entry(self):
        idea = make_idea()
        self._entry(idea, open_questions=["Which market?"])
        entry = self._entry(idea)

        response = self.client.post(
            f"/api/ideas/{idea.pk}/research/{entry.pk}/open-questions/",
            data=json.dumps({"questions": [" WHICH market? ", "What budget?"]}),
            content_type="application/json",
            **AUTH,
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.open_questions, ["What budget?"])

    def test_entry_must_belong_to_idea(self):
        idea = make_idea()
        other = make_idea(title="Other")
        entry = self._entry(other)

        response = self.client.post(
            f"/api/ideas/{idea.pk}/research/{entry.pk}/open-questions/",
            data=json.dumps({"questions": ["Decide?"]}),
            content_type="application/json",
            **AUTH,
        )

        self.assertEqual(response.status_code, 404)

    def test_archived_idea_is_read_only(self):
        idea = make_idea(status=Status.ARCHIVED)
        entry = self._entry(idea)

        response = self.client.post(
            f"/api/ideas/{idea.pk}/research/{entry.pk}/open-questions/",
            data=json.dumps({"questions": ["Decide?"]}),
            content_type="application/json",
            **AUTH,
        )

        self.assertEqual(response.status_code, 409)
        entry.refresh_from_db()
        self.assertEqual(entry.open_questions, [])


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiRepeatResultTests(TestCase):
    def _post(self, idea, payload):
        return self.client.post(
            f"/api/ideas/{idea.pk}/effort/",
            data=json.dumps(payload), content_type="application/json", **AUTH,
        )

    def test_records_deduplicated_results_and_completes_run(self):
        idea = make_idea(repeat_enabled=True, repeat_goal="Find five job leads")
        payload = {
            "results": [
                {"title": "Engineer", "url": "https://jobs.example/1", "details": "Local"},
                {"title": "Duplicate", "url": "https://jobs.example/1"},
            ]
        }
        response = self.client.post(
            f"/api/ideas/{idea.pk}/repeat-results/",
            data=json.dumps(payload), content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(idea.repeat_results.count(), 1)
        idea.refresh_from_db()
        self.assertIsNotNone(idea.last_repeat_run_at)
        self.assertFalse(idea.repeat_is_due)

    def test_non_repeatable_idea_cannot_receive_results(self):
        idea = make_idea()
        response = self.client.post(
            f"/api/ideas/{idea.pk}/repeat-results/",
            data=json.dumps({"results": []}), content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 404)

    def test_paused_repeat_task_rejects_results(self):
        idea = make_idea(repeat_enabled=True, repeat_paused=True, repeat_goal="Find leads")
        response = self.client.post(
            f"/api/ideas/{idea.pk}/repeat-results/",
            data=json.dumps({"results": []}), content_type="application/json", **AUTH,
        )
        self.assertEqual(response.status_code, 409)

    def test_moves_stage_and_status(self):
        idea = make_idea(status=Status.CURRENT)
        stage = make_stage(name="Prototyping")
        response = self._post(
            idea,
            {"topic": "t", "model": "other", "stage": stage.slug, "status": "tracking"},
        )
        self.assertEqual(response.status_code, 201)
        idea.refresh_from_db()
        self.assertEqual(idea.stage, stage)
        self.assertEqual(idea.status, Status.TRACKING)

    def test_missing_topic_is_a_400(self):
        idea = make_idea()
        response = self._post(idea, {"model": "other"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ResearchEntry.objects.exists())

    def test_unknown_model_is_a_400(self):
        idea = make_idea()
        response = self._post(idea, {"topic": "t", "model": "no-such-model"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ResearchEntry.objects.exists())

    def test_out_of_range_effort_is_a_400(self):
        idea = make_idea()
        response = self._post(idea, {"topic": "t", "model": "other", "effort": 9})
        self.assertEqual(response.status_code, 400)

    def test_bad_status_rolls_back_the_entry(self):
        idea = make_idea()
        response = self._post(
            idea, {"topic": "t", "model": "other", "status": "bogus"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ResearchEntry.objects.exists())

    def test_get_on_effort_endpoint_is_405(self):
        idea = make_idea()
        response = self.client.get(f"/api/ideas/{idea.pk}/effort/", **AUTH)
        self.assertEqual(response.status_code, 405)


def _make_podcast_script(**overrides):
    script = {
        "schema_version": 1,
        "title": "Why Small Models Are Getting Better",
        "target_duration_seconds": 60,
        "segments": [
            {
                "id": "0001-host", "sequence": 1, "speaker": "host",
                "voice_profile": "host-primary", "text": "Welcome back.",
                "emotion": None, "pause_after_ms": 300,
            },
            {
                "id": "0002-analyst", "sequence": 2, "speaker": "analyst",
                "voice_profile": "analyst-primary", "text": "Glad to be here.",
                "emotion": None, "pause_after_ms": 0,
            },
        ],
        "citations": [],
    }
    script.update(overrides)
    return script


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiPodcastEpisodeTests(TestCase):
    def setUp(self):
        VoiceProfile.objects.create(name="host-primary", speaker_label="host")
        VoiceProfile.objects.create(name="analyst-primary", speaker_label="analyst")

    def _post(self, idea, payload):
        return self.client.post(
            f"/api/ideas/{idea.pk}/podcast-episode/",
            data=json.dumps(payload), content_type="application/json", **AUTH,
        )

    def test_creates_episode_and_run_and_advances_repeat_clock(self):
        idea = make_idea(repeat_enabled=True, repeat_goal="Cover this week's research")
        show = make_podcast_show(idea=idea)
        response = self._post(idea, {"title": "Episode One", "script": _make_podcast_script()})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()

        episode = Episode.objects.get(pk=body["episode_id"])
        self.assertEqual(episode.show, show)
        self.assertEqual(episode.episode_number, 1)
        self.assertEqual(episode.title, "Episode One")
        self.assertEqual(episode.script["segments"][0]["text"], "Welcome back.")

        run = EpisodeRun.objects.get(pk=body["run_id"])
        self.assertEqual(run.episode, episode)
        self.assertEqual(run.status, EpisodeRunStatus.AWAITING_AUDIO)
        self.assertEqual(run.manifest["run_id"], run.pk)
        self.assertEqual(run.manifest["episode_id"], episode.pk)
        self.assertIn("host-primary", run.manifest["voice_profiles"])
        self.assertIn("analyst-primary", run.manifest["voice_profiles"])

        idea.refresh_from_db()
        self.assertIsNotNone(idea.last_repeat_run_at)
        self.assertFalse(idea.repeat_is_due)

    def test_repeat_result_ids_are_actioned_and_linked_to_the_episode(self):
        source_idea = make_idea(repeat_enabled=True, title="Source backlog idea")
        candidate = RepeatResult.objects.create(
            idea=source_idea, title="A good find", url="https://example.com/a",
            status=RepeatResultStatus.INTERESTED,
        )
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        response = self._post(
            idea,
            {
                "title": "Ep 1",
                "script": _make_podcast_script(),
                "repeat_result_ids": [candidate.pk],
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["actioned_repeat_result_ids"], [candidate.pk])

        candidate.refresh_from_db()
        self.assertEqual(candidate.status, RepeatResultStatus.ACTIONED)
        self.assertEqual(candidate.episode_id, response.json()["episode_id"])

    def test_oversized_repeat_result_ids_is_rejected(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        response = self._post(
            idea,
            {
                "title": "Ep",
                "script": _make_podcast_script(),
                "repeat_result_ids": list(range(101)),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Episode.objects.count(), 0)

    def test_non_integer_repeat_result_ids_is_rejected(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        response = self._post(
            idea,
            {"title": "Ep", "script": _make_podcast_script(), "repeat_result_ids": ["abc"]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Episode.objects.count(), 0)

    def test_episode_numbers_increment_per_show(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        first = self._post(idea, {"title": "Ep 1", "script": _make_podcast_script()}).json()
        second = self._post(idea, {"title": "Ep 2", "script": _make_podcast_script()}).json()
        self.assertEqual(Episode.objects.get(pk=first["episode_id"]).episode_number, 1)
        self.assertEqual(Episode.objects.get(pk=second["episode_id"]).episode_number, 2)

    def test_idea_without_a_podcast_show_is_rejected(self):
        idea = make_idea(repeat_enabled=True)
        response = self._post(idea, {"title": "Ep", "script": _make_podcast_script()})
        self.assertEqual(response.status_code, 409)

    def test_archived_idea_is_rejected(self):
        idea = make_idea(repeat_enabled=True, status=Status.ARCHIVED)
        make_podcast_show(idea=idea)
        response = self._post(idea, {"title": "Ep", "script": _make_podcast_script()})
        self.assertEqual(response.status_code, 409)

    def test_paused_repeat_task_is_rejected(self):
        idea = make_idea(repeat_enabled=True, repeat_paused=True)
        make_podcast_show(idea=idea)
        response = self._post(idea, {"title": "Ep", "script": _make_podcast_script()})
        self.assertEqual(response.status_code, 409)

    def test_unregistered_voice_profile_is_rejected(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        script = _make_podcast_script()
        script["segments"][0]["voice_profile"] = "not-a-real-voice"
        response = self._post(idea, {"title": "Ep", "script": script})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Episode.objects.count(), 0)

    def test_inactive_voice_profile_is_rejected(self):
        VoiceProfile.objects.filter(name="analyst-primary").update(is_active=False)
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        response = self._post(idea, {"title": "Ep", "script": _make_podcast_script()})
        self.assertEqual(response.status_code, 400)

    def test_missing_title_is_rejected(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        response = self._post(idea, {"script": _make_podcast_script()})
        self.assertEqual(response.status_code, 400)

    def test_oversized_segment_text_is_rejected(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea)
        script = _make_podcast_script()
        script["segments"][0]["text"] = "x" * 3000
        response = self._post(idea, {"title": "Ep", "script": script})
        self.assertEqual(response.status_code, 400)

    def test_script_under_half_configured_duration_is_rejected(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea, target_episode_duration_seconds=60)
        response = self._post(idea, {"title": "Ep", "script": _make_podcast_script()})
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 75", response.json()["error"])
        self.assertEqual(Episode.objects.count(), 0)

    def test_script_at_half_configured_duration_is_accepted(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea, target_episode_duration_seconds=8)
        script = _make_podcast_script()
        script["segments"][0]["text"] = "one two three four five"
        script["segments"][1]["text"] = "six seven eight nine ten"
        response = self._post(idea, {"title": "Ep", "script": script})
        self.assertEqual(response.status_code, 201, response.content)

    def test_fractional_minimum_word_threshold_rounds_up(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea, target_episode_duration_seconds=9)
        script = _make_podcast_script()
        script["segments"][0]["text"] = "one two three four five six"
        script["segments"][1]["text"] = "seven eight nine ten eleven"
        response = self._post(idea, {"title": "Ep", "script": script})
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 12", response.json()["error"])

    def test_show_duration_over_one_hour_is_rejected_defensively(self):
        idea = make_idea(repeat_enabled=True)
        make_podcast_show(idea=idea, target_episode_duration_seconds=3601)
        response = self._post(idea, {"title": "Ep", "script": _make_podcast_script()})
        self.assertEqual(response.status_code, 400)
        self.assertIn("1-hour limit", response.json()["error"])


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiFeedTests(TestCase):
    def _post(self, path, payload):
        return self.client.post(
            path, data=json.dumps(payload), content_type="application/json", **AUTH
        )

    def test_add_feed_creates_and_links_idea(self):
        from .helpers import make_idea as _mk

        idea = _mk()
        r = self._post(
            "/api/feeds/", {"url": "https://ex.com/f.xml", "title": "F", "idea_id": idea.pk}
        )
        self.assertEqual(r.status_code, 201)
        from ideas.models import Feed

        feed = Feed.objects.get(url="https://ex.com/f.xml")
        self.assertTrue(feed.idea_feeds.filter(idea=idea).exists())

    def test_add_feed_is_idempotent(self):
        self._post("/api/feeds/", {"url": "https://ex.com/f.xml"})
        r = self._post("/api/feeds/", {"url": "https://ex.com/f.xml"})
        self.assertEqual(r.status_code, 200)  # reused, not created
        from ideas.models import Feed

        self.assertEqual(Feed.objects.filter(url="https://ex.com/f.xml").count(), 1)

    def test_add_feed_requires_url(self):
        r = self._post("/api/feeds/", {"title": "no url"})
        self.assertEqual(r.status_code, 400)

    def test_feed_items_unsummarized_filter(self):
        from .helpers import make_feed, make_feed_item

        feed = make_feed()
        make_feed_item(feed=feed, guid="a")
        done = make_feed_item(feed=feed, guid="b")
        from ideas.feeds import record_feed_item_summary

        record_feed_item_summary(done, summary="s")
        r = self.client.get("/api/feed-items/?unsummarized=1", **AUTH)
        guids = {i["guid"] for i in r.json()["items"]}
        self.assertEqual(guids, {"a"})

    def test_feed_items_limit_and_offset(self):
        from .helpers import make_feed, make_feed_item

        feed = make_feed()
        for guid in ("a", "b", "c"):
            make_feed_item(feed=feed, guid=guid)
        r = self.client.get("/api/feed-items/?limit=2", **AUTH)
        self.assertEqual(len(r.json()["items"]), 2)
        page2 = self.client.get("/api/feed-items/?limit=2&offset=2", **AUTH)
        self.assertEqual(len(page2.json()["items"]), 1)
        # No overlap between the pages.
        first = {i["guid"] for i in r.json()["items"]}
        self.assertNotIn(page2.json()["items"][0]["guid"], first)

    def test_feed_items_body_is_opt_in(self):
        from .helpers import make_feed_item

        make_feed_item(guid="a", content="The whole post.")
        plain = self.client.get("/api/feed-items/", **AUTH).json()["items"][0]
        self.assertNotIn("content", plain)
        withbody = self.client.get("/api/feed-items/?content=1", **AUTH)
        self.assertEqual(withbody.json()["items"][0]["content"], "The whole post.")

    def test_feed_items_content_0_is_false(self):
        from .helpers import make_feed_item

        make_feed_item(guid="a", content="The whole post.")
        r = self.client.get("/api/feed-items/?content=0", **AUTH)
        self.assertNotIn("content", r.json()["items"][0])

    def test_feed_items_content_without_limit_gets_a_default_cap(self):
        from unittest.mock import patch

        from .helpers import make_feed, make_feed_item

        feed = make_feed()
        for guid in ("a", "b", "c"):
            make_feed_item(feed=feed, guid=guid, content="body")
        with patch("ideas.api.DEFAULT_CONTENT_LIMIT", 2):
            r = self.client.get("/api/feed-items/?content=1", **AUTH)
        self.assertEqual(len(r.json()["items"]), 2)

    def test_feed_items_explicit_limit_beats_content_default(self):
        from unittest.mock import patch

        from .helpers import make_feed, make_feed_item

        feed = make_feed()
        for guid in ("a", "b", "c"):
            make_feed_item(feed=feed, guid=guid, content="body")
        with patch("ideas.api.DEFAULT_CONTENT_LIMIT", 1):
            r = self.client.get("/api/feed-items/?content=1&limit=3", **AUTH)
        self.assertEqual(len(r.json()["items"]), 3)

    def test_summarize_feed_item(self):
        from .helpers import make_feed_item
        from ideas.feeds import link_feed

        item = make_feed_item()
        idea = make_idea()
        link_feed(idea, item.feed)
        r = self._post(
            f"/api/feed-items/{item.pk}/summarize/",
            {
                "summary": "Gist.",
                "model": "other",
                "idea_id": idea.pk,
                "usefulness": 4,
            },
        )
        self.assertEqual(r.status_code, 201)
        item.refresh_from_db()
        self.assertEqual(item.assessments.get(idea=idea).usefulness, 4)
        self.assertTrue(item.is_summarized)

    def test_summarize_bad_usefulness_is_400(self):
        from .helpers import make_feed_item
        from ideas.feeds import link_feed

        item = make_feed_item()
        idea = make_idea()
        link_feed(idea, item.feed)
        r = self._post(
            f"/api/feed-items/{item.pk}/summarize/",
            {"summary": "x", "idea_id": idea.pk, "usefulness": 9},
        )
        self.assertEqual(r.status_code, 400)

    def test_usefulness_without_idea_is_rejected_without_summarizing(self):
        from .helpers import make_feed_item

        item = make_feed_item()
        r = self._post(
            f"/api/feed-items/{item.pk}/summarize/",
            {"summary": "Should not persist.", "usefulness": 4},
        )

        self.assertEqual(r.status_code, 400)
        item.refresh_from_db()
        self.assertFalse(item.is_summarized)
        self.assertEqual(item.summary, "")

    def test_unassessed_filter_is_specific_to_the_idea(self):
        from .helpers import make_feed_item
        from ideas.feeds import link_feed, record_feed_item_summary

        item = make_feed_item()
        first = make_idea()
        second = make_idea()
        link_feed(first, item.feed)
        link_feed(second, item.feed)
        record_feed_item_summary(
            item, summary="Neutral.", idea=first, usefulness=5
        )

        first_items = self.client.get(
            f"/api/feed-items/?idea={first.pk}&unassessed=1", **AUTH
        ).json()["items"]
        second_items = self.client.get(
            f"/api/feed-items/?idea={second.pk}&unassessed=1", **AUTH
        ).json()["items"]

        self.assertEqual(first_items, [])
        self.assertEqual([row["id"] for row in second_items], [item.pk])
        self.assertEqual(second_items[0]["summary"], "Neutral.")
        self.assertIsNone(second_items[0]["assessment"])

    def test_unassessed_filter_requires_an_idea(self):
        response = self.client.get("/api/feed-items/?unassessed=1", **AUTH)

        self.assertEqual(response.status_code, 400)


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiFeedSafetyTests(TestCase):
    def test_add_feed_rejects_unsafe_url(self):
        from ideas.models import Feed

        r = self.client.post(
            "/api/feeds/",
            data=json.dumps({"url": "http://169.254.169.254/"}),
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Feed.objects.count(), 0)


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiPauseAndRatingTests(TestCase):
    def _post(self, path, payload):
        return self.client.post(
            path, data=json.dumps(payload), content_type="application/json", **AUTH
        )

    def test_effort_blocked_when_paused(self):
        from .helpers import make_idea as mk

        idea = mk()
        idea.agent_runs_since_feedback = 2
        idea.save()
        r = self._post(f"/api/ideas/{idea.pk}/effort/", {"topic": "t", "model": "other"})
        self.assertEqual(r.status_code, 409)

    def test_add_feed_stores_rating_on_link(self):
        from .helpers import make_idea as mk
        from ideas.models import IdeaFeed

        idea = mk()
        r = self._post(
            "/api/feeds/", {"url": "https://ex.com/r.xml", "idea_id": idea.pk, "rating": 4}
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(IdeaFeed.objects.get(idea=idea).rating, 4)


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiArchivedTests(TestCase):
    def test_effort_blocked_on_archived_idea(self):
        idea = make_idea(status=Status.ARCHIVED)
        r = self.client.post(
            f"/api/ideas/{idea.pk}/effort/",
            data=json.dumps({"topic": "t", "model": "other"}),
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(r.status_code, 409)
        from ideas.models import ResearchEntry

        self.assertFalse(ResearchEntry.objects.exists())


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class ApiChildIdeaTests(TestCase):
    def _post(self, path, payload):
        return self.client.post(
            path, data=json.dumps(payload), content_type="application/json", **AUTH
        )

    def test_agent_creates_child_under_top_level(self):
        from ideas.models import Idea

        owner = make_user(email="owner@example.com")
        parent = make_idea(title="Passive Income", created_by=owner)
        r = self._post(f"/api/ideas/{parent.pk}/children/", {"title": "A SaaS"})
        self.assertEqual(r.status_code, 201)
        child = Idea.objects.get(title="A SaaS")
        self.assertEqual(child.parent_id, parent.pk)
        self.assertTrue(child.proposed_by_agent)
        self.assertEqual(child.category_id, parent.category_id)  # inherited
        self.assertEqual(child.created_by, owner)

    def test_child_limit_is_five(self):
        parent = make_idea()
        for i in range(5):
            self.assertEqual(
                self._post(f"/api/ideas/{parent.pk}/children/", {"title": f"c{i}"}).status_code,
                201,
            )
        r = self._post(f"/api/ideas/{parent.pk}/children/", {"title": "sixth"})
        self.assertEqual(r.status_code, 409)

    def test_child_cannot_have_its_own_children(self):
        parent = make_idea()
        child = make_idea(title="child", parent=parent)
        r = self._post(f"/api/ideas/{child.pk}/children/", {"title": "grand"})
        self.assertEqual(r.status_code, 409)

    def test_cannot_create_child_on_archived(self):
        parent = make_idea(status=Status.ARCHIVED)
        r = self._post(f"/api/ideas/{parent.pk}/children/", {"title": "x"})
        self.assertEqual(r.status_code, 409)

    def test_suggest_children_appends_and_works_on_a_child(self):
        parent = make_idea()
        child = make_idea(parent=parent)
        r = self._post(
            f"/api/ideas/{child.pk}/suggest-children/", {"suggestions": ["one", "two"]}
        )
        self.assertEqual(r.status_code, 201)
        child.refresh_from_db()
        self.assertIn("one", child.suggested_children)
        self.assertIn("two", child.suggested_children)


@override_settings(IDEAFLOW_API_TOKEN=TOKEN, IDEAFLOW_TASK_MODELS={"summary": "claude-haiku-4-5", "research": "claude-opus-4-8"})
class ApiConfigAndRoutingTests(TestCase):
    def test_config_returns_task_models(self):
        r = self.client.get("/api/config/", **AUTH)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["task_models"]["summary"], "claude-haiku-4-5")
        self.assertIn("model_tiers", body)

    def test_feed_items_per_feed_cap(self):
        from .helpers import make_feed, make_feed_item

        feed = make_feed()
        for i in range(8):
            make_feed_item(feed=feed, guid=f"g{i}")
        r = self.client.get("/api/feed-items/?per_feed=5", **AUTH)
        self.assertEqual(len(r.json()["items"]), 5)

    def test_feed_items_filter_by_idea(self):
        from .helpers import make_feed, make_feed_item, make_idea
        from ideas.feeds import link_feed

        idea = make_idea()
        f1, f2 = make_feed(), make_feed()
        link_feed(idea, f1, rating=5)
        make_feed_item(feed=f1, guid="in")
        make_feed_item(feed=f2, guid="out")  # not linked to idea
        r = self.client.get(f"/api/feed-items/?idea={idea.pk}", **AUTH)
        guids = {i["guid"] for i in r.json()["items"]}
        self.assertEqual(guids, {"in"})

    def test_effort_sets_exec_summary(self):
        from .helpers import make_idea

        idea = make_idea()
        r = self.client.post(
            f"/api/ideas/{idea.pk}/effort/",
            data=json.dumps({"topic": "t", "model": "other", "exec_summary": "State: solid."}),
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(r.status_code, 201)
        idea.refresh_from_db()
        self.assertEqual(idea.exec_summary, "State: solid.")
