"""Token-authed JSON API for agents that can't reach the DB directly.

Three endpoints, all guarded by a single shared token in IDEAFLOW_API_TOKEN:

    GET  /api/ideas/                 list ideas (optional ?status=)
    GET  /api/ideas/<pk>/            one idea, with resources + research entries
    POST /api/ideas/<pk>/effort/     record a work-effort report (ResearchEntry)

The token goes in an `Authorization: Bearer <token>` (or `X-API-Token`) header.
Unset token disables the whole API, so it stays off until you opt in.
"""

import json
from collections import defaultdict
from functools import wraps

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from .feeds import is_acceptable_feed_url, link_feed, record_feed_item_summary
from .models import AGENT_CHILD_LIMIT, AIModel, Category, Feed, FeedItem, Idea, Status
from .reporting import record_effort
from .serialize import (
    feed_item_to_dict,
    feed_to_dict,
    idea_to_dict,
    research_entry_to_dict,
)

_DETAIL_PREFETCH = (
    "resources",
    "research_entries",
    "research_entries__model",
    "children",
)


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
    ideas = Idea.objects.select_related("category", "stage")
    status = request.GET.get("status")
    if status:
        ideas = ideas.filter(status=status)
    return JsonResponse({"ideas": [idea_to_dict(i, detail=False) for i in ideas]})


@require_api_token
def idea_detail(request, pk):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    idea = get_object_or_404(
        Idea.objects.select_related("category", "stage").prefetch_related(
            *_DETAIL_PREFETCH
        ),
        pk=pk,
    )
    return JsonResponse(idea_to_dict(idea, detail=True))


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
            exec_summary=payload.get("exec_summary"),
        )
    except (ValueError, LookupError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    idea = (
        Idea.objects.select_related("category", "stage")
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
    items = FeedItem.objects.select_related("feed", "summary_model")
    if request.GET.get("feed"):
        items = items.filter(feed_id=request.GET["feed"])
    if request.GET.get("idea"):
        items = items.filter(feed__idea_feeds__idea=request.GET["idea"]).distinct()
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

    # Page the queue (?limit=25&offset=50) — the full corpus is megabytes, and
    # a scoring run only ever wants the next handful.
    offset = _positive_int(request.GET.get("offset")) or 0
    limit = _positive_int(request.GET.get("limit"))
    items = items[offset : offset + limit] if limit else items[offset:]

    with_content = bool(request.GET.get("content"))
    return JsonResponse(
        {"items": [feed_item_to_dict(i, content=with_content) for i in items]}
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
    try:
        record_feed_item_summary(
            item,
            summary=payload.get("summary", ""),
            model=payload.get("model", "other"),
            usefulness=payload.get("usefulness"),
        )
    except (ValueError, LookupError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    item = FeedItem.objects.select_related("feed", "summary_model").get(pk=item.pk)
    return JsonResponse(feed_item_to_dict(item), status=201)


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
    )
    child = Idea.objects.select_related("category", "stage", "parent").prefetch_related(
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


urlpatterns = [
    path("ideas/", idea_list, name="api_idea_list"),
    path("ideas/<int:pk>/", idea_detail, name="api_idea_detail"),
    path("ideas/<int:pk>/effort/", idea_effort, name="api_idea_effort"),
    path("ideas/<int:pk>/children/", idea_children, name="api_idea_children"),
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
