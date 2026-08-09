import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase

from ideas.feeds import ingest_entries, record_feed_item_summary
from ideas.models import FeedItem

from .helpers import make_feed, make_feed_item, make_idea


def run(command, *args):
    out = StringIO()
    call_command(command, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def entry(guid, **over):
    data = {"id": guid, "link": f"https://example.com/{guid}", "title": guid.title()}
    data.update(over)
    return data


class IngestTests(TestCase):
    def test_ingest_creates_items_unsummarized(self):
        feed = make_feed()
        created = ingest_entries(feed, [entry("a"), entry("b")])
        self.assertEqual(len(created), 2)
        self.assertEqual(feed.items.count(), 2)
        self.assertFalse(any(i.is_summarized for i in feed.items.all()))

    def test_reingesting_same_guid_is_a_noop(self):
        feed = make_feed()
        ingest_entries(feed, [entry("a"), entry("b")])
        created = ingest_entries(feed, [entry("a"), entry("b"), entry("c")])
        # Only the genuinely new entry comes back and gets stored.
        self.assertEqual([i.guid for i in created], ["c"])
        self.assertEqual(feed.items.count(), 3)

    def test_entry_without_guid_or_link_is_skipped(self):
        feed = make_feed()
        created = ingest_entries(feed, [{"title": "no id"}])
        self.assertEqual(created, [])
        self.assertEqual(feed.items.count(), 0)

    def test_published_parsed_becomes_a_datetime(self):
        feed = make_feed()
        (item,) = ingest_entries(
            feed, [entry("dated", published_parsed=(2026, 7, 20, 14, 30, 0, 0, 0, 0))]
        )
        self.assertEqual(item.published_at.year, 2026)
        self.assertEqual(item.published_at.hour, 14)

    def test_same_guid_across_feeds_is_allowed(self):
        a, b = make_feed(), make_feed()
        ingest_entries(a, [entry("shared")])
        ingest_entries(b, [entry("shared")])
        self.assertEqual(FeedItem.objects.filter(guid="shared").count(), 2)

    def test_unique_feed_guid_constraint(self):
        feed = make_feed()
        make_feed_item(feed=feed, guid="dup")
        with self.assertRaises(IntegrityError):
            FeedItem.objects.create(feed=feed, guid="dup")


class SummaryTests(TestCase):
    def test_records_summary_model_and_usefulness(self):
        item = make_feed_item()
        record_feed_item_summary(
            item, summary="Concise.", model="claude-opus-4-8", usefulness=4
        )
        item.refresh_from_db()
        self.assertEqual(item.summary, "Concise.")
        self.assertEqual(item.summary_model.slug, "claude-opus-4-8")
        self.assertEqual(item.usefulness, 4)
        self.assertTrue(item.is_summarized)

    def test_out_of_range_usefulness_rejected(self):
        item = make_feed_item()
        with self.assertRaises(ValueError):
            record_feed_item_summary(item, summary="x", usefulness=9)


class FeedCommandTests(TestCase):
    def test_add_feed_is_idempotent_and_links_idea(self):
        idea = make_idea()
        url = "https://example.com/rss.xml"
        run("add_feed", "--url", url, "--title", "Example", "--idea", str(idea.pk))
        run("add_feed", "--url", url)  # second call reuses, no duplicate
        from ideas.models import Feed

        feed = Feed.objects.get(url=url)
        self.assertEqual(Feed.objects.filter(url=url).count(), 1)
        self.assertTrue(feed.idea_feeds.filter(idea=idea).exists())

    def test_dump_feed_items_unsummarized_is_the_work_queue(self):
        feed = make_feed()
        ingest_entries(feed, [entry("a"), entry("b")])
        summarized = feed.items.first()
        record_feed_item_summary(summarized, summary="done", usefulness=3)
        data = json.loads(run("dump_feed_items", "--unsummarized"))
        guids = {i["guid"] for i in data["items"]}
        self.assertNotIn(summarized.guid, guids)
        self.assertEqual(len(data["items"]), 1)

    def test_summarize_feed_item_command(self):
        item = make_feed_item()
        run(
            "summarize_feed_item",
            str(item.pk),
            "--summary", "The gist.",
            "--model", "other",
            "--usefulness", "5",
        )
        item.refresh_from_db()
        self.assertEqual(item.summary, "The gist.")
        self.assertEqual(item.usefulness, 5)
        self.assertTrue(item.is_summarized)

    def test_summarize_unknown_item_raises(self):
        with self.assertRaises(CommandError):
            run("summarize_feed_item", "999999", "--summary", "x")


class UrlSafetyTests(TestCase):
    def test_scheme_check(self):
        from ideas.feeds import is_http_url

        self.assertTrue(is_http_url("https://example.com/x"))
        self.assertTrue(is_http_url("http://example.com/x"))
        for bad in ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", ""]:
            self.assertFalse(is_http_url(bad))

    def test_acceptable_feed_url_blocks_bad_scheme_and_private_ip_literals(self):
        from ideas.feeds import is_acceptable_feed_url

        self.assertTrue(is_acceptable_feed_url("https://example.com/feed.xml"))
        self.assertTrue(is_acceptable_feed_url("https://93.184.216.34/feed"))  # public IP
        for bad in [
            "file:///etc/passwd",
            "http://127.0.0.1/feed",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.1.2.3/feed",
            "http://[::1]/feed",
        ]:
            self.assertFalse(is_acceptable_feed_url(bad), bad)

    def test_is_fetchable_url_blocks_internal_ip_literals(self):
        from ideas.feeds import is_fetchable_url

        self.assertFalse(is_fetchable_url("http://169.254.169.254/"))
        self.assertFalse(is_fetchable_url("http://127.0.0.1:5432/"))
        self.assertFalse(is_fetchable_url("ftp://example.com/"))

    def test_fetch_and_ingest_refuses_internal_url(self):
        # IP literal → no DNS needed; the guard raises before any network call.
        from ideas.feeds import fetch_and_ingest

        feed = make_feed(url="http://169.254.169.254/latest/")
        with self.assertRaises(ValueError):
            fetch_and_ingest(feed)


class AddFeedSafetyTests(TestCase):
    def test_add_feed_command_rejects_unsafe_url(self):
        from ideas.models import Feed

        with self.assertRaises(CommandError):
            run("add_feed", "--url", "file:///etc/passwd")
        with self.assertRaises(CommandError):
            run("add_feed", "--url", "http://169.254.169.254/")
        self.assertEqual(Feed.objects.count(), 0)


class NextActionEffortTests(TestCase):
    def test_record_effort_sets_next_action(self):
        from ideas.reporting import record_effort

        idea = make_idea()
        record_effort(idea, topic="Review", model="other", next_action="Ship an MVP")
        idea.refresh_from_db()
        self.assertEqual(idea.next_action, "Ship an MVP")


class FeedCapTests(TestCase):
    def test_non_research_idea_caps_at_5(self):
        from ideas.feeds import link_feed

        idea = make_idea()
        for r in [1, 2, 3, 4, 5, 5]:
            link_feed(idea, make_feed(), rating=r)
        self.assertEqual(idea.idea_feeds.count(), 5)
        # the lowest-rated (1) got pruned
        self.assertNotIn(1, list(idea.idea_feeds.values_list("rating", flat=True)))

    def test_research_category_caps_at_10(self):
        from .helpers import make_category
        from ideas.feeds import link_feed

        idea = make_idea(category=make_category(is_research=True))
        self.assertEqual(idea.feed_cap, 10)
        for _ in range(12):
            link_feed(idea, make_feed(), rating=3)
        self.assertEqual(idea.idea_feeds.count(), 10)

    def test_recent_articles_only_summarized(self):
        from ideas.feeds import ingest_entries, link_feed, recent_articles, record_feed_item_summary

        idea = make_idea()
        feed = make_feed()
        link_feed(idea, feed, rating=5)
        (article,) = ingest_entries(feed, [entry("x")])
        self.assertEqual(recent_articles(idea), [])          # unsummarized excluded
        record_feed_item_summary(article, summary="s", usefulness=3)
        self.assertEqual([i.id for i in recent_articles(idea)], [article.id])


class AgentPauseTests(TestCase):
    def test_effort_increments_and_pauses(self):
        from ideas.reporting import record_effort

        idea = make_idea()
        for _ in range(3):
            record_effort(idea, topic="t", model="other")
        idea.refresh_from_db()
        self.assertEqual(idea.agent_runs_since_feedback, 3)
        self.assertTrue(idea.is_paused)
