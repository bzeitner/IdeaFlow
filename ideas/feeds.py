"""Feed ingest + summary bookkeeping.

The point of this module is the "once" guarantee: a feed is downloaded only
when it has changed (conditional GET), and each entry is stored — and later
summarized — exactly once, keyed by (feed, guid). Re-running refresh_feeds or
pointing several ideas at the same feed never re-does that work.
"""

import hashlib
import ipaddress
import socket
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlsplit

from django.db import transaction
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import Feed, FeedItem, IdeaFeed, Status
from .reporting import resolve_ai_model

# Only ever fetch/store web feeds — no file://, ftp://, javascript:, data:, etc.
ALLOWED_SCHEMES = {"http", "https"}

# Feed bodies are stored so a scoring agent doesn't have to re-download the
# page; 20k characters is well past where a summary stops improving.
CONTENT_MAX_CHARS = 20_000


def refreshable_feeds():
    """Feeds needed by at least one unarchived, unpaused idea.

    Eligibility is evaluated at refresh time so pausing/archiving is reversible
    and a shared feed continues while any other associated idea still needs it.
    """
    return Feed.objects.filter(
        is_active=True,
        idea_feeds__idea__feed_ingestion_paused=False,
        idea_feeds__idea__status__in=(Status.CURRENT, Status.TRACKING),
    ).distinct()


def _ip_or_none(host):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_public_ip(ip):
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_http_url(url):
    """True for an http/https URL — used to keep rendered links from carrying a
    javascript:/data: scheme (stored-XSS guard)."""
    return urlsplit(url or "").scheme in ALLOWED_SCHEMES


def is_acceptable_feed_url(url):
    """Cheap, no-DNS check for *accepting* a feed: http/https, and if the host is
    a literal IP it must be public. Hostnames are resolved and re-checked at
    fetch time by is_fetchable_url()."""
    parts = urlsplit(url or "")
    if parts.scheme not in ALLOWED_SCHEMES or not parts.hostname:
        return False
    ip = _ip_or_none(parts.hostname)
    return ip is None or _is_public_ip(ip)


def is_fetchable_url(url):
    """Full SSRF guard applied right before fetching: http/https and every
    resolved address is public. Blocks internal/loopback/link-local targets
    such as cloud metadata (169.254.169.254) and localhost services."""
    parts = urlsplit(url or "")
    if parts.scheme not in ALLOWED_SCHEMES or not parts.hostname:
        return False
    ip = _ip_or_none(parts.hostname)
    if ip is not None:
        return _is_public_ip(ip)
    try:
        infos = socket.getaddrinfo(parts.hostname, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    ips = [ipaddress.ip_address(info[4][0]) for info in infos]
    return bool(ips) and all(_is_public_ip(ip) for ip in ips)


def _guid(entry):
    return (entry.get("id") or entry.get("guid") or entry.get("link") or "").strip()


def _published(entry):
    # feedparser gives *_parsed as a time.struct_time in UTC.
    tp = entry.get("published_parsed") or entry.get("updated_parsed")
    if not tp:
        return None
    return datetime(*tp[:6], tzinfo=dt_timezone.utc)


def _content(entry):
    content = entry.get("content")
    if content:
        try:
            return content[0].get("value", "")
        except (AttributeError, IndexError, KeyError):
            pass
    return entry.get("summary") or entry.get("description") or ""


def _hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@transaction.atomic
def ingest_entries(feed, entries, *, limit=None):
    """Upsert entries into `feed`, skipping any whose (feed, guid) already
    exists. Returns the list of newly-created FeedItems (those needing a
    summary) — existing items are left untouched, which is what keeps each
    entry summarized only once. `limit` caps how many entries are considered,
    taking them in feed order (newest first, by convention). Entries older
    than `feed.backfill_cutoff` are always skipped (undated entries are kept,
    since there's no date to judge them by) — this bounds backfill on every
    ingest, not just the first."""
    created = []
    if limit is not None:
        entries = list(entries)[:limit]
    cutoff = feed.backfill_cutoff
    for entry in entries:
        guid = _guid(entry)
        if not guid:
            continue
        published = _published(entry)
        if cutoff is not None and published is not None and published < cutoff:
            continue
        body = _content(entry)
        item, was_created = FeedItem.objects.get_or_create(
            feed=feed,
            guid=guid[:500],
            defaults={
                "link": (entry.get("link") or "")[:500],
                "title": (entry.get("title") or "")[:300],
                "published_at": published,
                "content_hash": _hash(body),
                "content": (body or "")[:CONTENT_MAX_CHARS],
            },
        )
        if was_created:
            # Phase 3 mirrors only genuinely new ingress into the measured
            # evidence funnel. Historical rows enter through the importer with
            # eligible_for_processing=False and are never backlogged for LLMs.
            if settings.IDEAFLOW_SOURCES_PHASE3_ENABLED:
                from sources.services import sync_new_feed_item

                sync_new_feed_item(item)
            created.append(item)
    return created


def fetch_and_ingest(feed):
    """Conditional-GET a feed and ingest new entries. Returns
    (status, new_items). feedparser handles ETag/Last-Modified, so an unchanged
    feed comes back 304 with nothing to do."""
    # SSRF guard: never let a stored URL point the server at an internal address
    # (metadata endpoints, localhost services) or a non-web scheme.
    if not is_fetchable_url(feed.url):
        raise ValueError(f"Refusing to fetch unsafe or non-public feed URL: {feed.url}")

    import feedparser  # lazy: keeps ingest_entries importable/testable without it

    parsed = feedparser.parse(
        feed.url,
        etag=feed.etag or None,
        modified=feed.last_modified or None,
    )
    status = getattr(parsed, "status", None)
    feed.last_fetched_at = timezone.now()

    if status == 304:
        feed.save(update_fields=["last_fetched_at"])
        return status, []

    etag = getattr(parsed, "etag", "") or ""
    modified = getattr(parsed, "modified", "") or ""
    if etag:
        feed.etag = etag[:300]
    if modified:
        feed.last_modified = modified[:100]
    if not feed.title and getattr(parsed, "feed", None):
        feed.title = (parsed.feed.get("title", "") or "")[:200]
    feed.save()

    # Backfill is bounded by feed.backfill_cutoff (set once, at creation) on
    # every ingest — not just the first — so there's no separate first-fetch
    # limit here.
    new_items = ingest_entries(feed, getattr(parsed, "entries", []))
    return status, new_items


def _star(value, field):
    value = int(value)
    if not 1 <= value <= 5:
        raise ValueError(f"{field} must be between 1 and 5, got {value}.")
    return value


def prune_idea_feeds(idea):
    """Keep only the idea's top `feed_cap` feeds by relevance rating (unrated
    sort last), dropping the excess links. A feed that ends up linked to no idea
    is deleted outright (with its items), so orphans don't accrue and get
    ingested forever. Returns the list of removed feed ids."""
    keep = idea.feed_cap
    links = list(
        idea.idea_feeds.order_by(F("rating").desc(nulls_last=True), "-created_at")
    )
    removed = []
    for link in links[keep:]:
        feed_id = link.feed_id
        removed.append(feed_id)
        from sources.models import Subscription

        Subscription.objects.filter(legacy_idea_feed=link).update(
            legacy_idea_feed=None, is_paused=True
        )
        link.delete()
        if not IdeaFeed.objects.filter(feed_id=feed_id).exists():
            Feed.objects.filter(pk=feed_id).delete()
    return removed


def link_feed(idea, feed, rating=None):
    """Associate a feed with an idea (idempotent), optionally rating its
    relevance, then prune the idea back to its feed cap. Returns the IdeaFeed."""
    link, _ = IdeaFeed.objects.get_or_create(idea=idea, feed=feed)
    if rating is not None:
        link.rating = _star(rating, "rating")
        link.save(update_fields=["rating"])
    if settings.IDEAFLOW_SOURCES_PHASE3_ENABLED:
        from sources.services import ensure_subscription

        ensure_subscription(link)
    prune_idea_feeds(idea)
    return link


def recent_articles(idea, limit=10):
    """The idea's most recent *summarized* feed items, across its linked feeds."""
    from django.db.models import Prefetch

    from .models import FeedItemAssessment

    return list(
        FeedItem.objects.filter(
            feed__idea_feeds__idea=idea, summarized_at__isnull=False
        )
        .select_related("feed", "summary_model")
        .prefetch_related(
            Prefetch(
                "assessments",
                queryset=FeedItemAssessment.objects.filter(idea=idea),
            )
        )
        .order_by("-published_at", "-created_at")
        .distinct()[:limit]
    )


def record_feed_item_summary(
    item, *, summary, model=None, idea=None, usefulness=None, relevance_note="",
    produced_by_run=None,
):
    """Store one neutral global summary and an optional idea-specific score."""
    from .models import FeedItemAssessment

    score = None
    if usefulness is not None:
        if idea is None:
            raise ValueError("idea is required when usefulness is provided.")
        score = _star(usefulness, "usefulness")
    if item.summarized_at is None:
        item.summary = summary or ""
        if model:
            item.summary_model = resolve_ai_model(model)
        item.summarized_at = timezone.now()
        item.summarized_by_run = produced_by_run
        item.save(update_fields=[
            "summary", "summary_model", "summarized_at", "summarized_by_run"
        ])
    if usefulness is not None:
        existing = FeedItemAssessment.objects.filter(idea=idea, item=item).first()
        if (
            existing and existing.produced_by_run_id and produced_by_run
            and existing.produced_by_run_id != produced_by_run.pk
        ):
            raise ValueError("Assessment is already attributed to another execution run.")
        assessment, _created = FeedItemAssessment.objects.update_or_create(
            idea=idea,
            item=item,
            defaults={
                "usefulness": score,
                "relevance_note": relevance_note or "",
                "produced_by_run": (
                    existing.produced_by_run if existing and existing.produced_by_run_id
                    else produced_by_run
                ),
            },
        )
        if settings.IDEAFLOW_SOURCES_PHASE3_ENABLED:
            from sources.services import sync_legacy_assessment

            sync_legacy_assessment(assessment)
    return item
