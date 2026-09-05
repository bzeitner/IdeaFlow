import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse

from ideas.models import (
    IdeaRelationSuggestion, PersonaReview, RelationshipCouncilReview,
    ResearchEntry, Resource, WeeklySummary,
)
from ideas.weekly_metrics import (
    execution_metrics_for_periods, missing_weekly_periods, normalize_weekly_metrics,
)
from executions.models import ExecutionTrace, LLMRun, TraceStatus
from executions.tests.helpers import make_configuration, make_workflow_version

from .helpers import MODEL_BACKEND, make_ai_model, make_idea, make_user


TOKEN = "weekly-test-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


class WeeklySummaryViewTests(TestCase):
    def test_permission_is_required(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:weekly_summaries"))

        self.assertRedirects(
            response, reverse("ideas:home"), fetch_redirect_response=False
        )

    def test_latest_is_expanded_and_history_is_collapsed(self):
        parent = make_idea(pk=9, title="Parent")
        make_idea(pk=10, title="Child", parent=parent)
        older = WeeklySummary.objects.create(
            period_start=date(2026, 8, 2),
            period_end=date(2026, 8, 8),
            title="Older week",
            content="Old state",
            generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        latest = WeeklySummary.objects.create(
            period_start=date(2026, 8, 9),
            period_end=date(2026, 8, 15),
            title="Latest week",
            content=(
                "Current **state**\n\n# Blockers\n\n"
                "- None identified\n\n"
                "[Review the work](https://example.com/report)\n\n"
                "<script>alert('unsafe')</script>"
            ),
            generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            metrics={
                "tasks_by_type": {"research": 4, "implementation": 2},
                "tasks_by_idea": {
                    "Idea #9 — Parent": 2,
                    "Idea #10 — Child": 4,
                    "Idea #9 — Parent + children (total)": 6,
                },
                "prs": {"created": 2, "reviewed": 1, "closed": 1},
                "open_prs": [
                    {
                        "url": "https://github.com/bzeitner/IdeaFlow/pull/33",
                        "title": "Persist feed backfill cutoff",
                        "description": "Review migration behavior and API limits.",
                        "idea_id": 12,
                        "state": "OPEN",
                    }
                ],
                "tokens_by_task": {"research": 1000, "implementation": 2000},
                "tokens_by_model": {"claude-opus-4-8": 3000},
                "tokens_by_category": {"Project": 3000},
                "tokens_by_idea": {"Idea #9 — Repeat task status": 3000},
                "total_tasks": 6,
                "total_tokens": 3000,
                "execution_runs_by_workflow": {
                    "Relationship council": 3,
                    "Feed scoring": 2,
                },
                "execution_tokens_by_workflow": {
                    "Relationship council": 0,
                    "Feed scoring": 900,
                },
                "execution_ledger": {
                    "runs": 5,
                    "tokens": 900,
                    "token_unmeasured_runs": 3,
                },
            },
        )
        user = make_user(roles=["role_weekly_summary"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:weekly_summaries"))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(body.index(latest.title), body.index(older.title))
        self.assertEqual(body.count('class="weekly-summary" open'), 1)
        self.assertContains(response, "Weekly Summary")
        self.assertContains(response, "Trends over time")
        self.assertContains(response, "Tokens by model")
        self.assertContains(response, "Tokens by idea")
        self.assertContains(response, "Tasks by idea")
        self.assertContains(response, "Execution runs by workflow")
        self.assertContains(response, "All tracked tokens by workflow/source")
        self.assertNotContains(response, "Relationship council")
        self.assertContains(response, "0</strong> runs without provider token usage")
        self.assertContains(response, "6</strong> recorded tasks")
        self.assertContains(response, "Idea #9 — Parent + children (total)")
        self.assertContains(
            response,
            f'href="{reverse("ideas:detail", args=[parent.pk])}">Idea #9 — Parent</a>',
        )
        self.assertNotContains(response, "Idea #9 — Repeat task status")
        self.assertContains(response, "Created / reviewed / closed")
        self.assertContains(response, "Open pull requests")
        self.assertContains(response, "Persist feed backfill cutoff")
        self.assertContains(response, "<strong>state</strong>", html=True)
        self.assertContains(response, "<h2>Blockers</h2>", html=True)
        self.assertContains(response, "<li>None identified</li>", html=True)
        self.assertContains(
            response,
            '<a href="https://example.com/report" target="_blank" rel="noopener">Review the work</a>',
            html=True,
        )
        self.assertContains(response, "&lt;script&gt;alert(&#x27;unsafe&#x27;)&lt;/script&gt;")


class DailyReportViewTests(TestCase):
    def test_report_is_permission_gated(self):
        user = make_user(roles=["role_current"])
        self.client.force_login(user, backend=MODEL_BACKEND)
        response = self.client.get(reverse("ideas:daily_report"))
        self.assertRedirects(
            response, reverse("ideas:home"), fetch_redirect_response=False
        )

    def test_report_shows_deterministic_workflow_usage_and_linked_prs(self):
        configuration = make_configuration()
        workflow_version = make_workflow_version(key="feed_score")
        workflow_version.workflow.name = "Feed scoring"
        workflow_version.workflow.save(update_fields=["name"])
        completed_at = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
        trace = ExecutionTrace.objects.create(
            workflow_version=workflow_version,
            trigger="test",
            status=TraceStatus.SUCCEEDED,
            queued_at=completed_at,
            completed_at=completed_at,
        )
        LLMRun.objects.create(
            trace=trace,
            purpose="classification",
            model_configuration=configuration,
            rendered_input_hash="daily-feed-score",
            status=TraceStatus.SUCCEEDED,
            queued_at=completed_at,
            completed_at=completed_at,
            total_tokens=275,
        )
        idea = make_idea(title="Daily reporting")
        Resource.objects.create(
            idea=idea,
            label="Review daily report PR",
            url="https://github.com/bzeitner/IdeaFlow/pull/99",
        )
        user = make_user(roles=["role_weekly_summary"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(
            reverse("ideas:daily_report"), {"date": "2026-08-12"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no AI generation")
        self.assertContains(response, "Feed scoring")
        self.assertContains(response, "275")
        self.assertContains(response, "Tasks requiring review")
        self.assertContains(response, "Pull requests requiring review or verification")
        self.assertContains(response, "Review daily report PR")

    def test_report_includes_only_unattributed_legacy_tokens(self):
        idea = make_idea()
        model = make_ai_model()
        occurred_at = datetime(2026, 8, 12, 14, tzinfo=timezone.utc)
        ResearchEntry.objects.create(
            idea=idea, model=model, topic="Legacy work", tokens_used=425,
            occurred_at=occurred_at,
        )
        user = make_user(roles=["role_weekly_summary"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(
            reverse("ideas:daily_report"), {"date": "2026-08-12"}
        )

        self.assertEqual(response.context["ledger"]["all_tracked_tokens"], 425)
        self.assertContains(response, "Legacy / unattributed research")
        self.assertContains(response, "425 tokens from 1 legacy/unattributed research task")

    def test_review_queue_excludes_resolved_council_and_splits_automation(self):
        now = datetime(2026, 8, 12, 14, tzinfo=timezone.utc)
        resolved = make_idea(
            title="Resolved council", last_persona_review_at=now,
            last_meaningful_progress_at=now + timedelta(hours=1),
        )
        PersonaReview.objects.create(
            idea=resolved, status=PersonaReview.Status.NO_CONSENSUS
        )
        source = make_idea(title="Source")
        target_one = make_idea(title="Automation target")
        target_two = make_idea(title="Human target")
        IdeaRelationSuggestion.objects.create(
            analyzed_idea=source, source=source, target=target_one,
            relation_type="related_to", source_content_hash="a",
            target_content_hash="b", classifier_model="test",
        )
        human_review = IdeaRelationSuggestion.objects.create(
            analyzed_idea=source, source=source, target=target_two,
            relation_type="depends_on", source_content_hash="a",
            target_content_hash="c", classifier_model="test",
        )
        RelationshipCouncilReview.objects.create(
            suggestion=human_review,
            outcome=RelationshipCouncilReview.Outcome.NO_DECISION,
        )
        user = make_user(roles=["role_weekly_summary"])
        self.client.force_login(user, backend=MODEL_BACKEND)

        response = self.client.get(reverse("ideas:daily_report"))

        review_titles = {item["title"] for item in response.context["review_items"]}
        automation_titles = {
            item["title"] for item in response.context["automation_items"]
        }
        self.assertNotIn("Resolved council", review_titles)
        self.assertIn("Source → Human target", review_titles)
        self.assertIn("Source → Automation target", automation_titles)


@override_settings(IDEAFLOW_API_TOKEN=TOKEN)
class WeeklySummaryApiTests(TestCase):
    url = "/api/weekly-summaries/"

    def payload(self, **overrides):
        data = {
            "period_start": "2026-08-09",
            "period_end": "2026-08-15",
            "title": "Week ending 2026-08-15",
            "content": "# Executive summary\nShipped useful work.",
            "model": "claude-opus-4-8",
            "execution_provider": "claude",
            "tokens_used": 12000,
            "metrics": {
                "tasks_by_type": {"research": 3},
                "tasks_by_idea": {"Idea #4 — Example": 3},
                "prs": {"created": 1, "reviewed": 2, "closed": 1},
                "open_prs": [
                    {
                        "url": "https://github.com/bzeitner/IdeaFlow/pull/33",
                        "title": "Backfill cutoff",
                        "description": "Needs review.",
                        "idea_id": 12,
                    }
                ],
                "tokens_by_task": {"research": 5000},
                "tokens_by_model": {"claude-opus-4-8": 5000},
                "tokens_by_category": {"Project": 5000},
                "tokens_by_idea": {"Idea #4 — Example": 5000},
                "total_tasks": 3,
                "total_tokens": 5000,
            },
        }
        data.update(overrides)
        return data

    def test_agent_can_create_and_list_a_summary(self):
        response = self.client.post(
            self.url,
            data=json.dumps(self.payload()),
            content_type="application/json",
            **AUTH,
        )

        self.assertEqual(response.status_code, 201)
        summary_id = response.json()["id"]
        listed = self.client.get(self.url, **AUTH)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["weekly_summaries"][0]["id"], summary_id)
        self.assertEqual(listed.json()["weekly_summaries"][0]["metrics"]["prs"]["reviewed"], 2)
        self.assertEqual(listed.json()["weekly_summaries"][0]["metrics"]["open_prs"][0]["state"], "OPEN")
        self.assertEqual(listed.json()["weekly_summaries"][0]["execution_provider"], "claude")

    def test_execution_usage_is_derived_and_broken_out_by_workflow(self):
        configuration = make_configuration()
        completed_at = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
        for key, name, tokens in (
            ("relationship_council", "Relationship council", None),
            ("persona_council", "Persona council", 120),
            ("feed_score", "Feed scoring", 80),
            ("podcast_script", "Podcast script", 300),
        ):
            workflow_version = make_workflow_version(key=key)
            workflow_version.workflow.name = name
            workflow_version.workflow.save(update_fields=["name"])
            trace = ExecutionTrace.objects.create(
                workflow_version=workflow_version,
                trigger="test",
                status=TraceStatus.SUCCEEDED,
                queued_at=completed_at,
                started_at=completed_at,
                completed_at=completed_at,
            )
            LLMRun.objects.create(
                trace=trace,
                purpose="evaluation",
                model_configuration=configuration,
                rendered_input_hash=f"input-{key}",
                status=TraceStatus.SUCCEEDED,
                queued_at=completed_at,
                started_at=completed_at,
                completed_at=completed_at,
                total_tokens=tokens,
            )

        response = self.client.post(
            self.url,
            data=json.dumps(self.payload()),
            content_type="application/json",
            **AUTH,
        )

        self.assertEqual(response.status_code, 201)
        metrics = response.json()["metrics"]
        self.assertEqual(metrics["execution_runs_by_workflow"], {
            "Feed scoring": 1,
            "Persona council": 1,
            "Podcast script": 1,
            "Relationship council": 1,
        })
        self.assertEqual(metrics["execution_tokens_by_workflow"], {
            "Feed scoring": 80,
            "Persona council": 120,
            "Podcast script": 300,
            "Relationship council": 0,
        })
        self.assertEqual(metrics["execution_ledger"]["tokens"], 500)
        self.assertEqual(metrics["execution_ledger"]["token_measured_runs"], 3)
        self.assertEqual(metrics["execution_ledger"]["token_unmeasured_runs"], 1)

    def test_same_period_is_replaced_instead_of_duplicated(self):
        for content in ("First", "Corrected"):
            response = self.client.post(
                self.url,
                data=json.dumps(self.payload(content=content)),
                content_type="application/json",
                **AUTH,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WeeklySummary.objects.count(), 1)
        self.assertEqual(WeeklySummary.objects.get().content, "Corrected")

    def test_rejects_invalid_period_or_empty_content(self):
        response = self.client.post(
            self.url,
            data=json.dumps(self.payload(period_end="2026-08-01")),
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            self.url,
            data=json.dumps(self.payload(content="")),
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(response.status_code, 400)


class WeeklyMetricTests(TestCase):
    def test_multiple_periods_are_loaded_with_two_reporting_queries(self):
        periods = [
            (date(2026, 8, 2), date(2026, 8, 8)),
            (date(2026, 8, 9), date(2026, 8, 15)),
        ]
        with self.assertNumQueries(2):
            result = execution_metrics_for_periods(periods)

        self.assertEqual(set(result), set(periods))

    def test_missing_periods_are_sunday_through_saturday_and_skip_existing(self):
        pacific = ZoneInfo("America/Los_Angeles")
        activity = [
            datetime(2026, 8, 2, 0, 1, tzinfo=pacific),
            datetime(2026, 8, 8, 23, 59, tzinfo=pacific),
            datetime(2026, 8, 9, 0, 1, tzinfo=pacific),
        ]

        periods = missing_weekly_periods(
            activity,
            {(date(2026, 8, 2), date(2026, 8, 8))},
            today=date(2026, 8, 16),
        )

        self.assertEqual(
            periods,
            [{"period_start": "2026-08-09", "period_end": "2026-08-15", "activity_count": 1}],
        )

    def test_metrics_reject_negative_or_non_integer_values(self):
        with self.assertRaises(ValueError):
            normalize_weekly_metrics({"tasks_by_type": {"research": -1}})
        with self.assertRaises(ValueError):
            normalize_weekly_metrics({"tokens_by_model": {"opus": 1.5}})
        with self.assertRaises(ValueError):
            normalize_weekly_metrics(
                {"open_prs": [{"url": "https://example.com/not-a-pr"}]}
            )
