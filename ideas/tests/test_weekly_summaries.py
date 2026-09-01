import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse

from ideas.models import WeeklySummary
from ideas.weekly_metrics import missing_weekly_periods, normalize_weekly_metrics

from .helpers import MODEL_BACKEND, make_idea, make_user


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
        self.assertContains(response, "6</strong> tasks")
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
