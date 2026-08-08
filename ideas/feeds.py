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
from django.utils import timezone

from .models import FeedItem
from .reporting import resolve_ai_model

# Only ever fetch/store web feeds — no file://, ftp://, javascript:, data:, etc.
ALLOWED_SCHEMES = {"http", "https"}


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
def ingest_entries(feed, entries):
    """Upsert entries into `feed`, skipping any whose (feed, guid) already
    exists. Returns the list of newly-created FeedItems (those needing a
    summary) — existing items are left untouched, which is what keeps each
    entry summarized only once."""
    created = []
    for entry in entries:
        guid = _guid(entry)
        if not guid:
            continue
        item, was_created = FeedItem.objects.get_or_create(
            feed=feed,
            guid=guid[:500],
            defaults={
                "link": (entry.get("link") or "")[:500],
                "title": (entry.get("title") or "")[:300],
                "published_at": _published(entry),
                "content_hash": _hash(_content(entry)),
            },
        )
        if was_created:
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

    new_items = ingest_entries(feed, getattr(parsed, "entries", []))
    return status, new_items


def _star(value, field):
    value = int(value)
    if not 1 <= value <= 5:
        raise ValueError(f"{field} must be between 1 and 5, got {value}.")
    return value


def record_feed_item_summary(item, *, summary, model=None, usefulness=None):
    """The ingesting agent's write-back: set the summary, the model that wrote
    it, and a 1-5 usefulness rating. Stamps summarized_at so the item won't be
    picked up as unsummarized again."""
    item.summary = summary or ""
    if model:
        item.summary_model = resolve_ai_model(model)
    if usefulness is not None:
        item.usefulness = _star(usefulness, "usefulness")
    item.summarized_at = timezone.now()
    item.save()
    return item
