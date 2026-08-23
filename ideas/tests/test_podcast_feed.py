import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ideas.models import EpisodeStatus

from .helpers import make_episode, make_podcast_show


def _make_published_episode(show=None, **kwargs):
    kwargs.setdefault("status", EpisodeStatus.PUBLISHED)
    kwargs.setdefault("published_at", timezone.now())
    episode = make_episode(show=show, **kwargs)
    episode.audio_file.save(
        "episode.mp3", SimpleUploadedFile("episode.mp3", b"x" * 1000, content_type="audio/mpeg"),
        save=False,
    )
    episode.audio_mime_type = "audio/mpeg"
    episode.audio_size_bytes = 1000
    episode.audio_duration_seconds = 42.5
    episode.save()
    return episode


class PodcastFeedTests(TestCase):
    def setUp(self):
        self.show = make_podcast_show(
            slug="the-weekly-signal", title="The Weekly Signal",
            is_publicly_listed=True, host_name="Host Name",
            description="A show about things.",
            cover_image=SimpleUploadedFile("cover.jpg", b"fake-jpg-bytes", content_type="image/jpeg"),
        )
        self.episode = _make_published_episode(
            show=self.show, slug="ep-one", episode_number=1, title="Episode One",
            description="First episode.",
        )

    def tearDown(self):
        if self.episode.audio_file:
            self.episode.audio_file.delete(save=False)
        if self.show.cover_image:
            self.show.cover_image.delete(save=False)

    def _feed(self):
        return self.client.get(reverse("ideas:podcast_feed", args=[self.show.slug]))

    def test_feed_is_publicly_addressable_without_login(self):
        response = self._feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/rss+xml; charset=utf-8")

    def test_feed_includes_itunes_namespace_and_artwork(self):
        body = self._feed().content.decode()
        self.assertIn('xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"', body)
        self.assertIn("<itunes:image", body)
        self.assertIn("cover.jpg", body)

    def test_feed_item_has_stable_guid_not_permalink(self):
        body = self._feed().content.decode()
        self.assertIn(f"<guid isPermaLink=\"false\">{self.episode.guid}</guid>", body)

    def test_feed_item_has_correct_enclosure(self):
        body = self._feed().content.decode()
        audio_url = reverse("ideas:podcast_episode_audio", args=[self.show.slug, self.episode.slug])
        match = re.search(r"<enclosure\s+(.+?)\s*/>", body)
        self.assertIsNotNone(match)
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1)))
        self.assertTrue(attrs["url"].endswith(audio_url))
        self.assertEqual(attrs["length"], "1000")
        self.assertEqual(attrs["type"], "audio/mpeg")

    def test_feed_item_has_duration_and_pubdate(self):
        body = self._feed().content.decode()
        self.assertIn("<itunes:duration>42</itunes:duration>", body)
        self.assertIn("<pubDate>", body)

    def test_unlisted_show_feed_404s(self):
        self.show.is_publicly_listed = False
        self.show.save(update_fields=["is_publicly_listed"])
        self.assertEqual(self._feed().status_code, 404)

    def test_draft_episode_is_absent_from_feed(self):
        draft = make_episode(show=self.show, slug="ep-two", episode_number=2, title="Draft Ep")
        body = self._feed().content.decode()
        self.assertNotIn("Draft Ep", body)
        draft.delete()

    def test_deleting_an_episode_removes_it_from_the_next_feed_fetch(self):
        self.assertIn("Episode One", self._feed().content.decode())
        episode_slug, show_slug = self.episode.slug, self.show.slug
        self.episode.audio_file.delete(save=False)
        self.episode.delete()

        self.assertNotIn("Episode One", self._feed().content.decode())
        page_response = self.client.get(reverse("ideas:podcast_episode", args=[show_slug, episode_slug]))
        self.assertEqual(page_response.status_code, 404)
        audio_response = self.client.get(
            reverse("ideas:podcast_episode_audio", args=[show_slug, episode_slug])
        )
        self.assertEqual(audio_response.status_code, 404)

    def test_unpublishing_removes_it_from_the_feed_and_404s_its_pages(self):
        self.episode.unpublish()
        self.assertNotIn("Episode One", self._feed().content.decode())
        page_response = self.client.get(
            reverse("ideas:podcast_episode", args=[self.show.slug, self.episode.slug])
        )
        self.assertEqual(page_response.status_code, 404)


class PodcastShowPageTests(TestCase):
    def setUp(self):
        self.show = make_podcast_show(
            slug="public-show", title="Public Show", is_publicly_listed=True,
        )

    def test_show_page_is_public(self):
        response = self.client.get(reverse("ideas:podcast_show", args=[self.show.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public Show")

    def test_show_page_never_links_the_feed_url(self):
        response = self.client.get(reverse("ideas:podcast_show", args=[self.show.slug]))
        self.assertNotContains(response, "feed.xml")

    def test_unlisted_show_page_404s(self):
        self.show.is_publicly_listed = False
        self.show.save(update_fields=["is_publicly_listed"])
        response = self.client.get(reverse("ideas:podcast_show", args=[self.show.slug]))
        self.assertEqual(response.status_code, 404)

    def test_homepage_never_links_the_feed_url(self):
        published = _make_published_episode(show=self.show, episode_number=1, slug="e1")
        response = self.client.get(reverse("ideas:home"))
        self.assertContains(response, "Public Show")
        self.assertNotContains(response, "feed.xml")
        published.audio_file.delete(save=False)


class PodcastAudioTests(TestCase):
    def setUp(self):
        self.show = make_podcast_show(slug="audio-show", is_publicly_listed=True)
        self.episode = _make_published_episode(show=self.show, slug="e1", episode_number=1)

    def tearDown(self):
        if self.episode.audio_file:
            self.episode.audio_file.delete(save=False)

    def _url(self):
        return reverse("ideas:podcast_episode_audio", args=[self.show.slug, self.episode.slug])

    def test_get_returns_full_audio_with_correct_type_and_length(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "audio/mpeg")
        self.assertEqual(response["Content-Length"], "1000")
        self.assertEqual(b"".join(response.streaming_content), b"x" * 1000)

    def test_head_request_returns_headers_without_body(self):
        response = self.client.head(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Length"], "1000")

    def test_byte_range_request_returns_partial_content(self):
        response = self.client.get(self._url(), HTTP_RANGE="bytes=0-99")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Length"], "100")
        self.assertEqual(response["Content-Range"], "bytes 0-99/1000")
        self.assertEqual(b"".join(response.streaming_content), b"x" * 100)

    def test_unpublished_episode_audio_404s(self):
        self.episode.unpublish()
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)
