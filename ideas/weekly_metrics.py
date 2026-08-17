from collections import Counter
from datetime import timedelta

from django.utils import timezone
from urllib.parse import urlparse


METRIC_GROUPS = (
    "tasks_by_type",
    "prs",
    "tokens_by_task",
    "tokens_by_model",
    "tokens_by_category",
)

PR_KEYS = ("created", "reviewed", "closed")


def sunday_saturday_period(day):
    start = day - timedelta(days=(day.weekday() + 1) % 7)
    return start, start + timedelta(days=6)


def missing_weekly_periods(activity_datetimes, existing_periods, today=None):
    """Completed Sunday-Saturday periods with activity and no persisted summary."""
    today = today or timezone.localdate()
    counts = Counter()
    for value in activity_datetimes:
        if value is None:
            continue
        day = timezone.localtime(value).date() if hasattr(value, "tzinfo") else value
        start, end = sunday_saturday_period(day)
        if end < today:
            counts[(start, end)] += 1
    existing = set(existing_periods)
    return [
        {"period_start": start.isoformat(), "period_end": end.isoformat(), "activity_count": counts[(start, end)]}
        for start, end in sorted(counts)
        if (start, end) not in existing
    ]


def normalize_weekly_metrics(value):
    """Return the stable, non-negative integer metrics schema accepted by the API."""
    if not isinstance(value, dict):
        raise ValueError("metrics must be a JSON object.")
    normalized = {}
    for group in METRIC_GROUPS:
        raw = value.get(group) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"metrics.{group} must be a JSON object.")
        clean = {}
        for key, count in raw.items():
            label = str(key).strip()
            if not label or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"metrics.{group} values must be non-negative integers.")
            clean[label] = count
        normalized[group] = clean
    normalized["prs"] = {key: normalized["prs"].get(key, 0) for key in PR_KEYS}
    total = value.get("total_tokens", sum(normalized["tokens_by_task"].values()))
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("metrics.total_tokens must be a non-negative integer.")
    normalized["total_tokens"] = total
    raw_prs = value.get("open_prs") or []
    if not isinstance(raw_prs, list):
        raise ValueError("metrics.open_prs must be a JSON array.")
    open_prs = []
    seen = set()
    for item in raw_prs:
        if not isinstance(item, dict):
            raise ValueError("Each metrics.open_prs item must be a JSON object.")
        url = str(item.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or "/pull/" not in parsed.path:
            raise ValueError("Each open PR must have an https://github.com/.../pull/... URL.")
        if url in seen:
            continue
        seen.add(url)
        idea_id = item.get("idea_id")
        if idea_id is not None and (isinstance(idea_id, bool) or not isinstance(idea_id, int) or idea_id <= 0):
            raise ValueError("Open PR idea_id must be a positive integer or null.")
        open_prs.append(
            {
                "url": url,
                "title": str(item.get("title") or url)[:300],
                "description": str(item.get("description") or "")[:1000],
                "idea_id": idea_id,
                "state": "OPEN",
            }
        )
    normalized["open_prs"] = open_prs
    return normalized


def metric_comparison_rows(current, previous=None):
    current = current or {}
    previous = previous or {}
    keys = sorted(set(current) | set(previous), key=lambda key: (-current.get(key, 0), key.lower()))
    maximum = max([1, *current.values(), *previous.values()])
    return [
        {
            "label": key,
            "value": current.get(key, 0),
            "previous": previous.get(key, 0),
            "delta": current.get(key, 0) - previous.get(key, 0),
            "width": round(current.get(key, 0) * 100 / maximum),
            "previous_width": round(previous.get(key, 0) * 100 / maximum),
        }
        for key in keys
    ]
