"""Public-facing podcast pages and RSS feed — no login required. Only ever
serves a PodcastShow with is_publicly_listed=True, and only ever serves an
Episode with status=PUBLISHED; anything else 404s, which is also what
happens automatically once an episode is deleted or unpublished (see
podcast_plan.md, "Episode lifecycle: unpublish vs. delete").

Per the plan's explicit decision, no page here (or anywhere else in the app)
ever links to or prints the feed.xml URL — it exists for one-time manual
submission to podcast directories, not for on-site discovery.
"""

import re

from django.contrib.syndication.views import Feed
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.feedgenerator import Enclosure, Rss201rev2Feed

from .models import Episode, EpisodeStatus, PodcastShow

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class _LimitedFile:
    """Wraps an open file handle so FileResponse's streaming never reads
    past the requested byte range — FileResponse itself just reads in
    fixed-size chunks until EOF, with no concept of a range end."""

    def __init__(self, fh, length):
        self.fh = fh
        self.remaining = length

    def read(self, amount=None):
        if self.remaining <= 0:
            return b""
        amount = self.remaining if amount is None else min(amount, self.remaining)
        data = self.fh.read(amount)
        self.remaining -= len(data)
        return data

    def close(self):
        self.fh.close()


def serve_range_aware_file(request, file_field, content_type):
    """A dynamic (non-static) file response with real HTTP Range support —
    podcast apps rely on this for seeking and resuming partial downloads.
    Django's FileResponse/static.serve don't implement Range parsing
    themselves (that's WhiteNoise's job for /static/, not applicable here);
    HEAD is unaffected — FileResponse-backed views naturally return an
    empty body for HEAD since Django's test client / WSGI handling strips
    it, while headers (Content-Length, Accept-Ranges) are still computed."""
    size = file_field.size
    match = _RANGE_RE.match(request.headers.get("Range", ""))

    def _full_response():
        response = FileResponse(file_field.open("rb"), content_type=content_type)
        response["Content-Length"] = str(size)
        response["Accept-Ranges"] = "bytes"
        return response

    if not match or (not match.group(1) and not match.group(2)):
        return _full_response()

    start_s, end_s = match.groups()
    if start_s:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    else:
        # Suffix range, e.g. "bytes=-500" — the last 500 bytes.
        start = max(size - int(end_s), 0)
        end = size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        return response

    length = end - start + 1
    fh = file_field.open("rb")
    fh.seek(start)
    response = FileResponse(_LimitedFile(fh, length), content_type=content_type, status=206)
    response["Content-Length"] = str(length)
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Accept-Ranges"] = "bytes"
    return response


def _public_show_or_404(show_slug):
    return get_object_or_404(PodcastShow, slug=show_slug, is_publicly_listed=True)


def _public_episode_or_404(show_slug, episode_slug):
    show = _public_show_or_404(show_slug)
    return get_object_or_404(
        Episode, show=show, slug=episode_slug, status=EpisodeStatus.PUBLISHED
    )


class ITunesFeedGenerator(Rss201rev2Feed):
    """Adds the iTunes-namespace elements podcast directories/apps expect,
    on top of Django's standard RSS 2.0 generator."""

    def rss_attributes(self):
        attrs = super().rss_attributes()
        attrs["xmlns:itunes"] = "http://www.itunes.com/dtds/podcast-1.0.dtd"
        return attrs

    def add_root_elements(self, handler):
        super().add_root_elements(handler)
        show = self.feed["show"]
        if show.host_name:
            handler.addQuickElement("itunes:author", show.host_name)
        handler.addQuickElement("itunes:explicit", "true" if show.is_explicit else "false")
        if show.cover_image:
            handler.addQuickElement("itunes:image", None, {"href": self.feed["image_url"]})
        if show.category:
            handler.startElement("itunes:category", {"text": show.category})
            handler.endElement("itunes:category")

    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, item)
        episode = item["episode"]
        if episode.audio_duration_seconds:
            handler.addQuickElement("itunes:duration", str(int(episode.audio_duration_seconds)))
        handler.addQuickElement("itunes:explicit", "true" if item["show"].is_explicit else "false")


class PodcastFeed(Feed):
    """Note on `self._request`: the urlconf instantiates one PodcastFeed()
    at import time and reuses it for every request to this URL, so stashing
    the request as instance state here is only safe because gunicorn runs
    sync workers (deploy/ideaflow.service: `--workers 3`, no --threads) —
    one request fully completes per worker process before the next starts,
    so there's no window for two requests' `self._request` to collide.
    This would need revisiting (e.g. passing request through explicitly
    instead of via self) before ever moving to a threaded/async worker
    class."""

    feed_type = ITunesFeedGenerator

    def get_object(self, request, show_slug):
        self._request = request
        return _public_show_or_404(show_slug)

    def title(self, show):
        return show.title

    def link(self, show):
        return self._request.build_absolute_uri(
            reverse("ideas:podcast_show", args=[show.slug])
        )

    def description(self, show):
        return show.description

    def author_name(self, show):
        return show.host_name

    def feed_extra_kwargs(self, show):
        return {"show": show, "image_url": self._absolute_cover_url(show)}

    def _absolute_cover_url(self, show):
        if not show.cover_image:
            return None
        return self._request.build_absolute_uri(show.cover_image.url)

    def items(self, show):
        return show.episodes.filter(status=EpisodeStatus.PUBLISHED).order_by("-published_at")

    def item_title(self, episode):
        return episode.title

    def item_description(self, episode):
        return episode.show_notes or episode.description

    def item_link(self, episode):
        return self._request.build_absolute_uri(
            reverse("ideas:podcast_episode", args=[episode.show.slug, episode.slug])
        )

    def item_guid(self, episode):
        return str(episode.guid)

    item_guid_is_permalink = False

    def item_pubdate(self, episode):
        return episode.published_at

    def item_enclosures(self, episode):
        if not episode.audio_file:
            return []
        url = self._request.build_absolute_uri(
            reverse("ideas:podcast_episode_audio", args=[episode.show.slug, episode.slug])
        )
        return [Enclosure(
            url=url,
            length=str(episode.audio_size_bytes or 0),
            mime_type=episode.audio_mime_type or "audio/mpeg",
        )]

    def item_extra_kwargs(self, episode):
        return {"episode": episode, "show": episode.show}


def podcast_show_page(request, show_slug):
    show = _public_show_or_404(show_slug)
    episodes = show.episodes.filter(status=EpisodeStatus.PUBLISHED).order_by("-published_at")
    return render(request, "ideas/podcast_show.html", {"show": show, "episodes": episodes})


def podcast_episode_page(request, show_slug, episode_slug):
    episode = _public_episode_or_404(show_slug, episode_slug)
    return render(request, "ideas/podcast_episode.html", {"show": episode.show, "episode": episode})


def podcast_episode_audio(request, show_slug, episode_slug):
    episode = _public_episode_or_404(show_slug, episode_slug)
    if not episode.audio_file:
        raise Http404
    return serve_range_aware_file(
        request, episode.audio_file, episode.audio_mime_type or "audio/mpeg"
    )
