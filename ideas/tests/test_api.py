import json

from django.test import TestCase, override_settings

from ideas.models import ResearchEntry, Status

from .helpers import make_idea, make_stage

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
        self.assertIn("research_entries", body)

    def test_detail_404_for_unknown_idea(self):
        response = self.client.get("/api/ideas/999999/", **AUTH)
        self.assertEqual(response.status_code, 404)


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

        record_feed_item_summary(done, summary="s", usefulness=3)
        r = self.client.get("/api/feed-items/?unsummarized=1", **AUTH)
        guids = {i["guid"] for i in r.json()["items"]}
        self.assertEqual(guids, {"a"})

    def test_summarize_feed_item(self):
        from .helpers import make_feed_item

        item = make_feed_item()
        r = self._post(
            f"/api/feed-items/{item.pk}/summarize/",
            {"summary": "Gist.", "model": "other", "usefulness": 4},
        )
        self.assertEqual(r.status_code, 201)
        item.refresh_from_db()
        self.assertEqual(item.usefulness, 4)
        self.assertTrue(item.is_summarized)

    def test_summarize_bad_usefulness_is_400(self):
        from .helpers import make_feed_item

        item = make_feed_item()
        r = self._post(
            f"/api/feed-items/{item.pk}/summarize/", {"summary": "x", "usefulness": 9}
        )
        self.assertEqual(r.status_code, 400)


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
        idea.agent_runs_since_feedback = 3
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
