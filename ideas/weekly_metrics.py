from collections import Counter
from datetime import timedelta

from django.utils import timezone


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
