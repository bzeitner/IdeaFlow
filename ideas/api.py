"""Token-authed JSON API for agents that can't reach the DB directly.

Core idea endpoints, all guarded by a single shared token in IDEAFLOW_API_TOKEN:

    GET  /api/ideas/                 list ideas (optional ?status=)
    GET  /api/ideas/<pk>/            one idea, with resources + research entries
    POST /api/ideas/<pk>/effort/     record a work-effort report (ResearchEntry)
    POST /api/ideas/<pk>/artifacts/  upload a durable report/list artifact
    DELETE /api/ideas/<pk>/resources/<resource_pk>/ remove a stale resource

The token goes in an `Authorization: Bearer <token>` (or `X-API-Token`) header.
Unset token disables the whole API, so it stays off until you opt in.
"""

import json
from collections import defaultdict
from functools import wraps
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .feeds import is_acceptable_feed_url, link_feed, record_feed_item_summary
from .graph.projection import graph_context, graph_projection, graph_search, neighborhood
from .models import AGENT_CHILD_LIMIT, Artifact, AIModel, Category, Feed, FeedItem, Idea, IdeaRelationSuggestion, PersonaReview, PersonaVote, PromptRevisionStatus, PromptTemplate, RelationshipCouncilReview, RelationshipCouncilVote, RepeatResult, ResearchEntry, Resource, Status, SuggestionStatus, WeeklySummary
from .reporting import new_open_questions, record_effort
from .serialize import (
    feed_item_to_dict,
    feed_to_dict,
    idea_to_dict,
    research_entry_to_dict,
)
from .weekly_metrics import missing_weekly_periods, normalize_weekly_metrics

_DETAIL_PREFETCH = (
    "resources",
    "artifacts__research_entry",
    "referenced_artifacts__idea",
    "research_entries",
    "research_entries__model",
    "children",
    "repeat_results",
    "idea_personas__persona",
    "persona_reviews__votes__persona",
)

# ?content=1 on /api/feed-items/ pulls full stored bodies; without an
# explicit ?limit that would return the whole (megabytes-large) corpus.
DEFAULT_CONTENT_LIMIT = 100


def _provided_token(request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :].strip()
    return request.headers.get("X-API-Token", "").strip()


def require_api_token(view):
    """Gate a view on the shared API token. CSRF is exempted because auth is by
    header token, not session cookie — there's no cookie to forge against."""

    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = getattr(settings, "IDEAFLOW_API_TOKEN", "")
        if not expected:
            return JsonResponse(
                {"error": "API disabled: set IDEAFLOW_API_TOKEN to enable it."},
                status=503,
            )
        if not constant_time_compare(_provided_token(request), expected):
            return JsonResponse({"error": "Invalid or missing API token."}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


@require_api_token
def idea_list(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    ideas = Idea.objects.select_related("category", "stage", "created_by")
    status = request.GET.get("status")
    if status:
        ideas = ideas.filter(status=status)
    return JsonResponse({"ideas": [idea_to_dict(i, detail=False) for i in ideas]})


@require_api_token
def approved_prompt_view(request, key):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    template = get_object_or_404(PromptTemplate, key=key, is_active=True)
    revision = template.revisions.filter(status=PromptRevisionStatus.APPROVED).order_by("-version").first()
    if revision is None:
        return JsonResponse({"error": "This prompt has no approved revision."}, status=409)
    return JsonResponse({"key": key, "version": revision.version, "content": revision.content})


def weekly_summary_to_dict(summary):
    return {
        "id": summary.pk,
        "period_start": summary.period_start.isoformat(),
        "period_end": summary.period_end.isoformat(),
        "title": summary.title,
        "content": summary.content,
        "model": summary.model,
        "execution_provider": summary.execution_provider,
        "tokens_used": summary.tokens_used,
        "metrics": summary.metrics,
        "generated_at": summary.generated_at.isoformat(),
    }


@require_api_token
def weekly_summary_list(request):
    if request.method == "GET":
        activity = list(Idea.objects.values_list("created_at", flat=True))
        activity += list(Idea.objects.values_list("updated_at", flat=True))
        activity += list(ResearchEntry.objects.values_list("occurred_at", flat=True))
        activity += list(RepeatResult.objects.values_list("found_at", flat=True))
        existing = WeeklySummary.objects.values_list("period_start", "period_end")
        return JsonResponse(
            {
                "weekly_summaries": [weekly_summary_to_dict(s) for s in WeeklySummary.objects.all()],
                "missing_periods": missing_weekly_periods(activity, existing),
            }
        )
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    period_start = parse_date(payload.get("period_start") or "")
    period_end = parse_date(payload.get("period_end") or "")
    content = (payload.get("content") or "").strip()
    if not period_start or not period_end or period_end < period_start:
        return JsonResponse({"error": "Provide a valid period_start and period_end."}, status=400)
    if not content:
        return JsonResponse({"error": "content is required."}, status=400)
    tokens_used = payload.get("tokens_used")
    if tokens_used is not None and (not isinstance(tokens_used, int) or tokens_used < 0):
        return JsonResponse({"error": "tokens_used must be a non-negative integer."}, status=400)
    try:
        metrics = normalize_weekly_metrics(payload.get("metrics") or {})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    defaults = {
        "title": (payload.get("title") or f"Weekly summary: {period_start}–{period_end}")[:200],
        "content": content,
        "model": (payload.get("model") or "")[:100],
        "execution_provider": (payload.get("execution_provider") or "")[:32],
        "tokens_used": tokens_used,
        "metrics": metrics,
        "generated_at": timezone.now(),
    }
    summary, created = WeeklySummary.objects.update_or_create(
        period_start=period_start, period_end=period_end, defaults=defaults
    )
    return JsonResponse(weekly_summary_to_dict(summary), status=201 if created else 200)


@require_api_token
def idea_detail(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    idea = get_object_or_404(
        Idea.objects.select_related("category", "stage", "created_by").prefetch_related(
            *_DETAIL_PREFETCH
        ),
        pk=pk,
    )
    return JsonResponse(idea_to_dict(idea, detail=True))


@require_api_token
def idea_resource(request, pk, resource_pk):
    """Remove one resource after an agent has verified it is no longer active."""
    if request.method != "DELETE":
        return HttpResponseNotAllowed(["DELETE"])
    resource = get_object_or_404(Resource, pk=resource_pk, idea_id=pk)
    deleted = {"id": resource.pk, "label": resource.label, "url": resource.url}
    resource.delete()
    return JsonResponse({"deleted": deleted})


@require_api_token
def idea_artifact(request, pk, artifact_pk=None):
    """Create or update a durable agent deliverable using multipart form data."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    idea = get_object_or_404(Idea, pk=pk)
    artifact = (
        get_object_or_404(Artifact, pk=artifact_pk, idea=idea)
        if artifact_pk else None
    )
    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()
    kind = (request.POST.get("kind") or Artifact.Kind.REPORT).strip()
    external_url = (request.POST.get("url") or "").strip()
    uploaded = request.FILES.get("file")
    if not title:
        return JsonResponse({"error": "title is required."}, status=400)
    if kind not in Artifact.Kind.values:
        return JsonResponse({"error": "kind must be report, list, or summary."}, status=400)
    if uploaded and uploaded.size > settings.IDEAFLOW_ARTIFACT_MAX_BYTES:
        return JsonResponse({"error": "Artifact files must be 10 MB or smaller."}, status=413)
    if artifact is None and not uploaded and not external_url:
        return JsonResponse({"error": "Upload a file or provide an external link."}, status=400)
    entry = None
    entry_id = request.POST.get("research_entry_id")
    if entry_id:
        entry = get_object_or_404(ResearchEntry, pk=entry_id, idea=idea)
    generated_at = parse_datetime(request.POST.get("generated_at") or "") or timezone.now()
    created = artifact is None
    if artifact is None and kind == Artifact.Kind.SUMMARY:
        artifact = Artifact.objects.filter(idea=idea, kind=kind).first()
        created = artifact is None
    artifact = artifact or Artifact(idea=idea)
    old_file_name = artifact.file.name if artifact.pk and artifact.file and uploaded else ""
    artifact.title = title[:200]
    artifact.description = description
    artifact.kind = kind
    artifact.url = external_url
    artifact.research_entry = entry
    artifact.generated_at = generated_at
    if uploaded:
        filename = Artifact.build_storage_filename(artifact.title, uploaded.name)
        artifact.file.save(filename, uploaded, save=False)
    artifact.save()
    if old_file_name and old_file_name != artifact.file.name:
        artifact.file.storage.delete(old_file_name)
    if kind == Artifact.Kind.SUMMARY and idea.summary_requested_at:
        idea.summary_requested_at = None
        idea.save(update_fields=["summary_requested_at", "updated_at"])
    return JsonResponse(
        {
            "artifact": {
                "id": artifact.pk,
                "title": artifact.title,
                "kind": artifact.kind,
                "url": artifact.link,
                "research_entry_id": artifact.research_entry_id,
                "generated_at": artifact.generated_at.isoformat(),
            }
        },
        status=201 if created else 200,
    )


@require_api_token
@transaction.atomic
def idea_reconcile_pr(request, pk):
    """Apply a verified external PR closure without manufacturing an effort."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    url = str(payload.get("url") or "").strip().rstrip("/")
    state = str(payload.get("state") or "").strip().upper()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or "/pull/" not in parsed.path:
        return JsonResponse({"error": "url must be an HTTPS GitHub pull-request URL."}, status=400)
    if state not in {"CLOSED", "MERGED"}:
        return JsonResponse({"error": "state must be CLOSED or MERGED."}, status=400)
    idea = get_object_or_404(Idea.objects.prefetch_related("resources"), pk=pk)
    removed = []
    for resource in idea.resources.all():
        if resource.url.rstrip("/") == url:
            removed.append(resource.pk)
            resource.delete()
    advanced = url in idea.next_action.rstrip("/")
    if advanced:
        idea.replace_active_next_action("")
        idea.agent_runs_since_feedback = 0
        idea.last_meaningful_progress_at = timezone.now()
        idea.save(
            update_fields=[
                "next_action",
                "next_actions",
                "agent_runs_since_feedback",
                "last_meaningful_progress_at",
                "updated_at",
            ]
        )
    return JsonResponse(
        {
            "idea_id": idea.pk,
            "url": url,
            "state": state,
            "removed_resource_ids": removed,
            "advanced_next_action": advanced,
            "next_action": idea.next_action,
        }
    )


@require_api_token
@transaction.atomic
def idea_repeat_results(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    idea = get_object_or_404(Idea, pk=pk, repeat_enabled=True)
    if idea.is_archived:
        return JsonResponse({"error": "Idea is archived."}, status=409)
    if idea.repeat_paused:
        return JsonResponse({"error": "Repeat task is paused."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    results = payload.get("results")
    if not isinstance(results, list) or len(results) > 100:
        return JsonResponse({"error": "results must be a list of at most 100 items."}, status=400)
    created = []
    for item in results:
        if not isinstance(item, dict) or not (item.get("title") or "").strip():
            return JsonResponse({"error": "Every result requires a title."}, status=400)
        title = item["title"].strip()[:300]
        url = (item.get("url") or "").strip()[:1000]
        details = (item.get("details") or "").strip()
        if url:
            result, was_created = RepeatResult.objects.get_or_create(
                idea=idea, url=url, defaults={"title": title, "details": details}
            )
        else:
            result = RepeatResult.objects.create(idea=idea, title=title, details=details)
            was_created = True
        if was_created:
            created.append(result.pk)
    idea.last_repeat_run_at = timezone.now()
    idea.save(update_fields=["last_repeat_run_at", "updated_at"])
    return JsonResponse({"created": created, "received": len(results), "completed_at": idea.last_repeat_run_at.isoformat()}, status=201)


@require_api_token
def graph_data(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return JsonResponse(
        graph_projection(include_archived=request.GET.get("archived") == "1")
    )


@require_api_token
def graph_neighborhood(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    idea = get_object_or_404(Idea, pk=request.GET.get("idea"))
    try:
        depth = int(request.GET.get("depth", 1))
        max_nodes = int(request.GET.get("max_nodes", 50))
    except ValueError:
        return JsonResponse({"error": "depth and max_nodes must be integers."}, status=400)
    return JsonResponse(neighborhood(idea, depth=depth, max_nodes=max_nodes))


@require_api_token
def graph_search_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"error": "q is required."}, status=400)
    return JsonResponse(graph_search(query))


@require_api_token
def idea_graph_context(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    idea = get_object_or_404(Idea.objects.select_related("category", "stage", "created_by"), pk=pk)
    try:
        depth = int(request.GET.get("depth", 1))
        max_nodes = int(request.GET.get("max_nodes", 30))
        token_budget = int(request.GET.get("token_budget", settings.IDEAFLOW_GRAPH_CONTEXT_DEFAULT_TOKEN_BUDGET))
    except ValueError:
        return JsonResponse({"error": "depth, max_nodes, and token_budget must be integers."}, status=400)
    task = request.GET.get("task", "research")
    return JsonResponse(graph_context(idea, depth=depth, max_nodes=max_nodes, token_budget=token_budget, task=task))


@require_api_token
def idea_effort(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    idea = get_object_or_404(Idea, pk=pk)
    if idea.is_archived:
        return JsonResponse(
            {"error": "Idea is archived — agents don't work archived ideas."},
            status=409,
        )
    if idea.is_paused:
        return JsonResponse(
            {
                "error": "Idea is paused for human feedback — add a next action or "
                "click Continue work before agents work it again.",
                "agent_runs_since_feedback": idea.agent_runs_since_feedback,
            },
            status=409,
        )
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Request body must be a JSON object."}, status=400)

    occurred_at = payload.get("occurred_at")
    if occurred_at:
        occurred_at = parse_datetime(occurred_at)
        if occurred_at is None:
            return JsonResponse(
                {"error": "occurred_at must be an ISO 8601 datetime."}, status=400
            )
    resource = payload.get("resource") or {}

    try:
        entry, res = record_effort(
            idea,
            topic=payload.get("topic", ""),
            model=payload.get("model", "other"),
            execution_provider=payload.get("execution_provider", ""),
            execution_model=payload.get("execution_model", ""),
            context=payload.get("context", ""),
            focus=payload.get("focus", ""),
            effort=payload.get("effort", 3),
            quality=payload.get("quality", 3),
            tokens_used=payload.get("tokens_used"),
            occurred_at=occurred_at,
            resource_url=resource.get("url"),
            resource_label=resource.get("label", ""),
            stage=payload.get("stage"),
            status=payload.get("status"),
            next_action=payload.get("next_action"),
            queued_next_actions=payload.get("queued_next_actions") or [],
            exec_summary=payload.get("exec_summary"),
            open_questions=payload.get("open_questions") or [],
        )
    except (ValueError, LookupError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    idea = (
        Idea.objects.select_related("category", "stage", "created_by")
        .prefetch_related(*_DETAIL_PREFETCH)
        .get(pk=idea.pk)
    )
    return JsonResponse(
        {
            "research_entry": research_entry_to_dict(entry),
            "idea": idea_to_dict(idea, detail=True),
        },
        status=201,
    )


@require_api_token
def research_open_questions(request, pk, entry_pk):
    """Merge structured questions into an existing, idea-scoped research entry."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    idea = get_object_or_404(Idea, pk=pk)
    if idea.is_archived:
        return JsonResponse(
            {"error": "Archived ideas cannot be changed through the agent API."},
            status=409,
        )
    entry = get_object_or_404(ResearchEntry, pk=entry_pk, idea=idea)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, list) or len(questions) > 50:
        return JsonResponse(
            {"error": "questions must be a list of at most 50 items."}, status=400
        )
    clean = []
    for raw in questions:
        question = str(raw).strip()
        if not question:
            continue
        if len(question) > 500:
            return JsonResponse(
                {"error": "Each question must be at most 500 characters."}, status=400
            )
        if question not in clean:
            clean.append(question)
    existing = [str(item).strip() for item in entry.open_questions if str(item).strip()]
    additions = new_open_questions(idea, clean, exclude_entry=entry)
    existing_keys = {" ".join(question.split()).casefold() for question in existing}
    merged = existing + [
        question
        for question in additions
        if " ".join(question.split()).casefold() not in existing_keys
    ]
    if merged != existing:
        entry.open_questions = merged
        entry.save(update_fields=["open_questions"])
    return JsonResponse({"research_entry": research_entry_to_dict(entry)})


@require_api_token
def feed_list(request):
    if request.method == "GET":
        feeds = Feed.objects.select_related().all()
        return JsonResponse({"feeds": [feed_to_dict(f) for f in feeds]})
    if request.method == "POST":
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
        url = (payload.get("url") or "").strip()
        if not url:
            return JsonResponse({"error": "url is required."}, status=400)
        if not is_acceptable_feed_url(url):
            return JsonResponse(
                {"error": "url must be http(s) and not a private address."}, status=400
            )
        feed, created = Feed.objects.get_or_create(
            url=url, defaults={"title": payload.get("title", "")}
        )
        if payload.get("title") and not feed.title:
            feed.title = payload["title"]
            feed.save(update_fields=["title"])
        idea_id = payload.get("idea_id")
        if idea_id:
            idea = Idea.objects.filter(pk=idea_id).first()
            if idea is None:
                return JsonResponse({"error": f"No idea with id {idea_id}."}, status=400)
            try:
                link_feed(idea, feed, payload.get("rating"))
            except (ValueError, LookupError) as exc:
                return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse(feed_to_dict(feed), status=201 if created else 200)
    return HttpResponseNotAllowed(["GET", "POST"])


@require_api_token
def api_config(request):
    """Task->model routing + per-model tiers, so agents pick the right tier."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    tiers = {m.slug: m.tier for m in AIModel.objects.all()}
    return JsonResponse(
        {"task_models": settings.IDEAFLOW_TASK_MODELS, "model_tiers": tiers}
    )


def _positive_int(value):
    """Query-param ints, ignoring anything that isn't a positive number."""
    if value and value.isdigit() and int(value) > 0:
        return int(value)
    return None


@require_api_token
def feed_item_list(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.GET.get("unassessed") and not request.GET.get("idea"):
        return JsonResponse(
            {"error": "unassessed requires an idea query parameter."}, status=400
        )
    items = FeedItem.objects.select_related("feed", "summary_model")
    if request.GET.get("feed"):
        items = items.filter(feed_id=request.GET["feed"])
    idea = None
    if request.GET.get("idea"):
        idea = get_object_or_404(Idea, pk=request.GET["idea"])
        items = items.filter(feed__idea_feeds__idea=idea).distinct()
        if request.GET.get("unassessed"):
            items = items.exclude(assessments__idea=idea)
    if request.GET.get("unsummarized"):
        items = items.filter(summarized_at__isnull=True)

    # Cap items per feed (e.g. ?per_feed=5) — bounds summaries per feed per
    # idea per effort.
    per_feed = request.GET.get("per_feed")
    if per_feed and per_feed.isdigit():
        cap = int(per_feed)
        seen = defaultdict(int)
        capped = []
        for item in items:
            if seen[item.feed_id] < cap:
                capped.append(item)
                seen[item.feed_id] += 1
        items = capped

    # ?content=1 includes each item's stored body; ?content=0 (or omitted)
    # doesn't. Bodies are large, so a request for them gets a default page
    # size unless the caller sets their own ?limit.
    with_content = request.GET.get("content") not in (None, "", "0")

    # Page the queue (?limit=25&offset=50) — the full corpus is megabytes, and
    # a scoring run only ever wants the next handful.
    offset = _positive_int(request.GET.get("offset")) or 0
    limit = _positive_int(request.GET.get("limit"))
    if limit is None and with_content:
        limit = DEFAULT_CONTENT_LIMIT
    items = items[offset : offset + limit] if limit else items[offset:]

    if idea is not None:
        from django.db.models import Prefetch

        from .models import FeedItemAssessment

        items = list(items)
        # The list may already have been sliced/paged, so prefetch onto those
        # instances rather than attempting to modify the queryset afterward.
        from django.db.models import prefetch_related_objects

        prefetch_related_objects(
            items,
            Prefetch(
                "assessments",
                queryset=FeedItemAssessment.objects.filter(idea=idea),
            ),
        )
    return JsonResponse(
        {
            "items": [
                feed_item_to_dict(i, content=with_content, idea=idea) for i in items
            ]
        }
    )


@require_api_token
def feed_item_summarize(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(FeedItem, pk=pk)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    idea = None
    idea_id = payload.get("idea_id")
    if idea_id is not None:
        idea = get_object_or_404(Idea, pk=idea_id)
        if not idea.idea_feeds.filter(feed=item.feed).exists():
            return JsonResponse(
                {"error": "This feed item is not linked to that idea."}, status=400
            )
    try:
        record_feed_item_summary(
            item,
            summary=payload.get("summary", ""),
            model=payload.get("model", "other"),
            idea=idea,
            usefulness=payload.get("usefulness"),
            relevance_note=payload.get("relevance_note", ""),
        )
    except (ValueError, LookupError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    item = FeedItem.objects.select_related("feed", "summary_model").prefetch_related(
        "assessments"
    ).get(pk=item.pk)
    return JsonResponse(feed_item_to_dict(item, idea=idea), status=201)


@require_api_token
def idea_children(request, pk):
    """Agent proposes a child idea under a top-level idea. Capped, and only for
    top-level parents — a child idea can't get its own children."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    parent = get_object_or_404(Idea, pk=pk)
    if parent.is_archived:
        return JsonResponse(
            {"error": "Idea is archived — agents don't work archived ideas."}, status=409
        )
    if parent.parent_id is not None:
        return JsonResponse(
            {
                "error": "Child ideas can't have their own children. Use "
                "suggest-children to leave suggestions for a human instead."
            },
            status=409,
        )
    if parent.children.filter(proposed_by_agent=True).count() >= AGENT_CHILD_LIMIT:
        return JsonResponse(
            {
                "error": f"Agent child limit ({AGENT_CHILD_LIMIT}) reached for this "
                "idea. Use suggest-children for any more."
            },
            status=409,
        )
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    title = (payload.get("title") or "").strip()
    if not title:
        return JsonResponse({"error": "title is required."}, status=400)

    category = parent.category
    cat_val = payload.get("category")
    if cat_val:
        category = (
            Category.objects.filter(slug=cat_val).first()
            or Category.objects.filter(name__iexact=cat_val).first()
        )
        if category is None:
            return JsonResponse({"error": f"No category matches {cat_val!r}."}, status=400)

    child = Idea.objects.create(
        title=title[:200],
        summary=payload.get("summary", ""),
        category=category,
        parent=parent,
        status=Status.CURRENT,
        proposed_by_agent=True,
        created_by=parent.created_by,
    )
    child = Idea.objects.select_related("category", "stage", "parent", "created_by").prefetch_related(
        *_DETAIL_PREFETCH
    ).get(pk=child.pk)
    return JsonResponse(idea_to_dict(child, detail=True), status=201)


@require_api_token
def idea_suggest_children(request, pk):
    """Agent leaves child-idea suggestions for a human (allowed on any idea,
    including children that can't create their own)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    idea = get_object_or_404(Idea, pk=pk)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    raw = payload.get("suggestions")
    if isinstance(raw, list):
        lines = [str(s).strip() for s in raw if str(s).strip()]
    else:
        one = (payload.get("text") or raw or "")
        lines = [str(one).strip()] if str(one).strip() else []
    if not lines:
        return JsonResponse({"error": "Provide one or more suggestions."}, status=400)
    idea.suggested_children = "\n".join(
        ([idea.suggested_children] if idea.suggested_children else []) + lines
    )
    idea.save(update_fields=["suggested_children", "updated_at"])
    return JsonResponse(idea_to_dict(idea, detail=True), status=201)


@require_api_token
@transaction.atomic
def idea_persona_review(request, pk):
    """Record one required-persona vote each and act only on unanimous approval."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    idea = get_object_or_404(Idea, pk=pk, persona_review_enabled=True)
    if idea.is_archived:
        return JsonResponse({"error": "Idea is archived."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    proposal = payload.get("proposal") if isinstance(payload, dict) else None
    votes = payload.get("votes") if isinstance(payload, dict) else None
    if not isinstance(proposal, dict) or not isinstance(votes, list):
        return JsonResponse({"error": "proposal must be an object and votes a list."}, status=400)
    if proposal.get("reversible") is not True:
        return JsonResponse(
            {"error": "Persona councils may act only on explicitly reversible proposals."},
            status=400,
        )
    safe_action_types = {"research", "analysis", "draft", "prototype", "test", "planning"}
    if proposal.get("action_type") not in safe_action_types:
        return JsonResponse(
            {"error": "Persona action_type must be one of the reversible allowlisted types.", "allowed": sorted(safe_action_types)},
            status=400,
        )
    raw_answers = proposal.get("question_answers", [])
    if not isinstance(raw_answers, list) or len(raw_answers) > 50:
        return JsonResponse({"error": "question_answers must be a list of at most 50 items."}, status=400)
    entry_ids = {
        raw.get("research_entry_id")
        for raw in raw_answers
        if isinstance(raw, dict)
    }
    entries = {
        entry.pk: entry
        for entry in ResearchEntry.objects.filter(idea=idea, pk__in=entry_ids)
    }
    normalized_answers = []
    for raw in raw_answers:
        if not isinstance(raw, dict):
            return JsonResponse({"error": "Each persona question answer must be an object."}, status=400)
        entry = entries.get(raw.get("research_entry_id"))
        index = raw.get("question_index")
        answer = str(raw.get("answer") or "").strip()
        questions = entry.open_questions if entry and isinstance(entry.open_questions, list) else []
        if not isinstance(index, int) or index < 0 or index >= len(questions) or not answer:
            return JsonResponse({"error": "Each persona answer must reference an open question on this idea and include an answer."}, status=400)
        normalized_answers.append(
            {"research_entry_id": entry.pk, "question_index": index, "answer": answer}
        )
    proposal["question_answers"] = normalized_answers
    assignments = {
        row.persona_id: row
        for row in idea.idea_personas.select_related("persona").filter(
            active=True, persona__is_active=True
        )
    }
    required_ids = {pk for pk, row in assignments.items() if row.required}
    if not required_ids:
        return JsonResponse({"error": "At least one active required persona is needed."}, status=409)
    parsed = {}
    for raw in votes:
        if not isinstance(raw, dict) or raw.get("persona_id") not in assignments:
            return JsonResponse({"error": "Every vote must name an assigned active persona."}, status=400)
        persona_id = raw["persona_id"]
        decision = str(raw.get("decision") or "").lower()
        if decision not in PersonaVote.Decision.values or persona_id in parsed:
            return JsonResponse({"error": "Votes must be unique and use approve, reject, or abstain."}, status=400)
        parsed[persona_id] = (decision, str(raw.get("rationale") or "").strip())
    missing = required_ids - parsed.keys()
    if missing:
        return JsonResponse(
            {"error": "Every required persona must explicitly approve, reject, or abstain.", "missing_persona_ids": sorted(missing)},
            status=400,
        )
    consensus = bool(required_ids) and all(
        parsed[persona_id][0] == PersonaVote.Decision.APPROVE
        for persona_id in required_ids
    )
    context = graph_context(idea, depth=2, task="persona")
    review = PersonaReview.objects.create(
        idea=idea,
        proposal=proposal,
        context=context,
        status=(PersonaReview.Status.CONSENSUS if consensus else PersonaReview.Status.NO_CONSENSUS),
    )
    PersonaVote.objects.bulk_create(
        [
            PersonaVote(
                review=review,
                persona_id=persona_id,
                decision=decision,
                rationale=rationale,
            )
            for persona_id, (decision, rationale) in parsed.items()
        ]
    )
    acted = False
    if consensus:
        next_action = str(proposal.get("next_action") or "").strip()
        if not next_action:
            transaction.set_rollback(True)
            return JsonResponse({"error": "A unanimous proposal requires a concrete next_action."}, status=400)
        safe_verbs = {
            "analyze", "compare", "document", "draft", "inventory", "investigate",
            "plan", "prototype", "research", "review", "summarize", "test", "validate",
        }
        first_word = next_action.split()[0].lower().strip(".,:;!?")
        if first_word not in safe_verbs:
            transaction.set_rollback(True)
            return JsonResponse(
                {"error": "Persona next_action must begin with an allowlisted reversible verb.", "allowed": sorted(safe_verbs)},
                status=400,
            )
        idea.replace_active_next_action(next_action)
        acted = True
    idea.last_persona_review_at = timezone.now()
    update_fields = ["last_persona_review_at", "updated_at"]
    if acted:
        update_fields.extend(["next_action", "next_actions"])
    idea.save(update_fields=update_fields)
    return JsonResponse(
        {"review_id": review.pk, "status": review.status, "acted": acted, "next_action": idea.next_action},
        status=201,
    )


@require_api_token
def relationship_council_queue(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        limit = max(1, min(int(request.GET.get("limit", 10)), 50))
    except ValueError:
        return JsonResponse({"error": "limit must be an integer."}, status=400)
    suggestions = (
        IdeaRelationSuggestion.objects.filter(
            status=SuggestionStatus.PENDING,
            relationship_council_review__isnull=True,
        )
        .select_related("source", "target")
        .prefetch_related("source__idea_personas__persona")
    )
    items = []
    for suggestion in suggestions:
        assignments = [
            assignment
            for assignment in suggestion.source.idea_personas.all()
            if assignment.active
            and assignment.required
            and assignment.persona.is_active
        ]
        if len(assignments) != 3:
            continue
        items.append(
            {
                "suggestion_id": suggestion.pk,
                "source": {
                    "id": suggestion.source_id,
                    "title": suggestion.source.title,
                    "summary": suggestion.source.summary,
                },
                "target": {
                    "id": suggestion.target_id,
                    "title": suggestion.target.title,
                    "summary": suggestion.target.summary,
                },
                "relationship": {
                    "type": suggestion.relation_type,
                    "description": suggestion.description,
                    "evidence": suggestion.evidence,
                    "confidence": suggestion.confidence,
                },
                "personas": [
                    {
                        "id": assignment.persona_id,
                        "name": assignment.persona.name,
                        "description": assignment.persona.description,
                        "goals": assignment.persona.goals,
                        "constraints": assignment.persona.constraints,
                    }
                    for assignment in assignments
                ],
            }
        )
        if len(items) >= limit:
            break
    return JsonResponse({"suggestions": items})


@require_api_token
@transaction.atomic
def relationship_council_submit(request, suggestion_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    suggestion = get_object_or_404(
        IdeaRelationSuggestion.objects.select_related("source", "target"),
        pk=suggestion_pk,
    )
    if suggestion.status != SuggestionStatus.PENDING:
        return JsonResponse({"error": "Suggestion is no longer pending."}, status=409)
    if hasattr(suggestion, "relationship_council_review"):
        return JsonResponse({"error": "Suggestion was already reviewed by the council."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    votes = payload.get("votes") if isinstance(payload, dict) else None
    if not isinstance(votes, list) or len(votes) != 3:
        return JsonResponse({"error": "Exactly three council votes are required."}, status=400)
    assignments = {
        assignment.persona_id: assignment
        for assignment in suggestion.source.idea_personas.select_related("persona").filter(
            active=True, required=True, persona__is_active=True
        )
    }
    if len(assignments) != 3:
        return JsonResponse({"error": "The source idea must have exactly three active required personas."}, status=409)
    parsed = {}
    providers = set()
    for raw in votes:
        if not isinstance(raw, dict) or raw.get("persona_id") not in assignments:
            return JsonResponse({"error": "Each vote must name an assigned required persona."}, status=400)
        persona_id = raw["persona_id"]
        decision = str(raw.get("decision") or "").lower()
        provider = str(raw.get("provider") or "").lower()
        rationale = str(raw.get("rationale") or "").strip()
        if persona_id in parsed or decision not in RelationshipCouncilVote.Decision.values:
            return JsonResponse({"error": "Votes must be unique and use accept, reject, or abstain."}, status=400)
        if provider not in {"claude", "codex"} or not rationale:
            return JsonResponse({"error": "Each vote requires a Claude/Codex provider and rationale."}, status=400)
        providers.add(provider)
        parsed[persona_id] = {
            "decision": decision,
            "provider": provider,
            "model": str(raw.get("model") or "")[:100],
            "rationale": rationale[:4000],
        }
    if set(parsed) != set(assignments) or providers != {"claude", "codex"}:
        return JsonResponse({"error": "All three personas must vote, using at least one Claude and one Codex run."}, status=400)
    accept_count = sum(vote["decision"] == "accept" for vote in parsed.values())
    reject_count = sum(vote["decision"] == "reject" for vote in parsed.values())
    if accept_count == 3:
        from .graph.semantic import accept_suggestion

        outcome = (
            RelationshipCouncilReview.Outcome.ACCEPTED
            if accept_suggestion(suggestion)
            else RelationshipCouncilReview.Outcome.NO_DECISION
        )
    elif reject_count >= 2:
        outcome = RelationshipCouncilReview.Outcome.REJECTED
        suggestion.status = SuggestionStatus.REJECTED
        suggestion.reviewed_at = timezone.now()
        suggestion.save(update_fields=["status", "reviewed_at", "updated_at"])
    else:
        outcome = RelationshipCouncilReview.Outcome.NO_DECISION
    review = RelationshipCouncilReview.objects.create(
        suggestion=suggestion,
        outcome=outcome,
    )
    RelationshipCouncilVote.objects.bulk_create(
        [
            RelationshipCouncilVote(
                review=review,
                persona_id=persona_id,
                **vote,
            )
            for persona_id, vote in parsed.items()
        ]
    )
    return JsonResponse(
        {
            "review_id": review.pk,
            "suggestion_id": suggestion.pk,
            "outcome": outcome,
            "accept_votes": accept_count,
            "reject_votes": reject_count,
        },
        status=201,
    )


urlpatterns = [
    path("prompts/<slug:key>/", approved_prompt_view, name="api_approved_prompt"),
    path("graph/", graph_data, name="api_graph"),
    path("graph/neighborhood/", graph_neighborhood, name="api_graph_neighborhood"),
    path("graph/search/", graph_search_view, name="api_graph_search"),
    path("ideas/", idea_list, name="api_idea_list"),
    path("weekly-summaries/", weekly_summary_list, name="api_weekly_summary_list"),
    path("ideas/<int:pk>/", idea_detail, name="api_idea_detail"),
    path("ideas/<int:pk>/artifacts/", idea_artifact, name="api_idea_artifact_create"),
    path("ideas/<int:pk>/artifacts/<int:artifact_pk>/", idea_artifact, name="api_idea_artifact_update"),
    path("ideas/<int:pk>/reconcile-pr/", idea_reconcile_pr, name="api_idea_reconcile_pr"),
    path("ideas/<int:pk>/repeat-results/", idea_repeat_results, name="api_idea_repeat_results"),
    path(
        "ideas/<int:pk>/resources/<int:resource_pk>/",
        idea_resource,
        name="api_idea_resource",
    ),
    path("ideas/<int:pk>/graph-context/", idea_graph_context, name="api_idea_graph_context"),
    path("ideas/<int:pk>/effort/", idea_effort, name="api_idea_effort"),
    path(
        "ideas/<int:pk>/research/<int:entry_pk>/open-questions/",
        research_open_questions,
        name="api_research_open_questions",
    ),
    path("ideas/<int:pk>/children/", idea_children, name="api_idea_children"),
    path("ideas/<int:pk>/persona-reviews/", idea_persona_review, name="api_idea_persona_review"),
    path("relationship-council-reviews/", relationship_council_queue, name="api_relationship_council_queue"),
    path("relationship-council-reviews/<int:suggestion_pk>/", relationship_council_submit, name="api_relationship_council_submit"),
    path(
        "ideas/<int:pk>/suggest-children/",
        idea_suggest_children,
        name="api_idea_suggest_children",
    ),
    path("config/", api_config, name="api_config"),
    path("feeds/", feed_list, name="api_feed_list"),
    path("feed-items/", feed_item_list, name="api_feed_item_list"),
    path(
        "feed-items/<int:pk>/summarize/",
        feed_item_summarize,
        name="api_feed_item_summarize",
    ),
]
