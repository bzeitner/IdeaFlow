from collections import Counter
from datetime import timedelta

from django.utils import timezone
from urllib.parse import urlparse


METRIC_GROUPS = (
    "tasks_by_type",
    "tasks_by_idea",
    "prs",
    "tokens_by_task",
    "tokens_by_model",
    "tokens_by_category",
    "tokens_by_idea",
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


def execution_metrics_for_periods(periods):
    """Authoritative execution and unattributed-token totals, batched by period."""
    from executions.models import LLMRun
    from ideas.models import ResearchEntry

    periods = list(periods)
    if not periods:
        return {}
    earliest = min(start for start, _end in periods)
    latest = max(end for _start, end in periods)
    result = {
        (start, end): {
            "execution_runs_by_workflow": {},
            "execution_tokens_by_workflow": {},
            "execution_ledger": {
                "runs": 0,
                "succeeded": 0,
                "failed": 0,
                "token_measured_runs": 0,
                "token_unmeasured_runs": 0,
                "tokens": 0,
                "cost_micros": 0,
                "token_measured_runs_by_workflow": {},
                "unattributed_research_tasks": 0,
                "unattributed_research_tokens": 0,
                "all_tracked_tokens": 0,
            },
        }
        for start, end in periods
    }
    day_to_period = {
        start + timedelta(days=offset): (start, end)
        for start, end in periods
        for offset in range((end - start).days + 1)
    }

    def period_for(value):
        return day_to_period.get(timezone.localtime(value).date())

    runs = LLMRun.objects.filter(
        completed_at__date__gte=earliest,
        completed_at__date__lte=latest,
    ).select_related("trace__workflow_version__workflow")
    for run in runs:
        period = period_for(run.completed_at)
        if period is None:
            continue
        metrics = result[period]
        ledger = metrics["execution_ledger"]
        workflow = run.trace.workflow_version.workflow
        label = workflow.name or workflow.key
        runs_by_workflow = metrics["execution_runs_by_workflow"]
        tokens_by_workflow = metrics["execution_tokens_by_workflow"]
        measured_runs_by_workflow = ledger["token_measured_runs_by_workflow"]
        runs_by_workflow[label] = runs_by_workflow.get(label, 0) + 1
        ledger["runs"] += 1
        if run.status == "succeeded":
            ledger["succeeded"] += 1
        elif run.status == "failed":
            ledger["failed"] += 1
        ledger["cost_micros"] += run.cost_micros or 0
        if run.total_tokens is not None:
            tokens_by_workflow[label] = tokens_by_workflow.get(label, 0) + run.total_tokens
            measured_runs_by_workflow[label] = measured_runs_by_workflow.get(label, 0) + 1
            ledger["token_measured_runs"] += 1
            ledger["tokens"] += run.total_tokens
        else:
            # A zero makes unmeasured workflows visible instead of silently omitting them.
            tokens_by_workflow.setdefault(label, 0)
            measured_runs_by_workflow.setdefault(label, 0)
            ledger["token_unmeasured_runs"] += 1

    legacy_entries = ResearchEntry.objects.filter(
        occurred_at__date__gte=earliest,
        occurred_at__date__lte=latest,
        produced_by_run=None,
        tokens_used__isnull=False,
    )
    for entry in legacy_entries:
        period = period_for(entry.occurred_at)
        if period is None:
            continue
        ledger = result[period]["execution_ledger"]
        ledger["unattributed_research_tasks"] += 1
        ledger["unattributed_research_tokens"] += entry.tokens_used
    for metrics in result.values():
        ledger = metrics["execution_ledger"]
        if ledger["unattributed_research_tokens"]:
            metrics["execution_tokens_by_workflow"][
                "Legacy / unattributed research"
            ] = ledger["unattributed_research_tokens"]
        ledger["all_tracked_tokens"] = (
            ledger["tokens"] + ledger["unattributed_research_tokens"]
        )
    return result


def execution_metrics_for_period(period_start, period_end):
    return execution_metrics_for_periods([(period_start, period_end)])[
        (period_start, period_end)
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
    total_tasks = value.get("total_tasks", sum(normalized["tasks_by_type"].values()))
    if isinstance(total_tasks, bool) or not isinstance(total_tasks, int) or total_tasks < 0:
        raise ValueError("metrics.total_tasks must be a non-negative integer.")
    normalized["total_tasks"] = total_tasks
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
