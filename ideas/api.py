"""Token-authed JSON API for agents that can't reach the DB directly.

Three endpoints, all guarded by a single shared token in IDEAFLOW_API_TOKEN:

    GET  /api/ideas/                 list ideas (optional ?status=)
    GET  /api/ideas/<pk>/            one idea, with resources + research entries
    POST /api/ideas/<pk>/effort/     record a work-effort report (ResearchEntry)

The token goes in an `Authorization: Bearer <token>` (or `X-API-Token`) header.
Unset token disables the whole API, so it stays off until you opt in.
"""

import json
from functools import wraps

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from .feeds import record_feed_item_summary
from .models import Feed, FeedItem, Idea
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
            feed.ideas.add(idea)
        return JsonResponse(feed_to_dict(feed), status=201 if created else 200)
    return HttpResponseNotAllowed(["GET", "POST"])


@require_api_token
def feed_item_list(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    items = FeedItem.objects.select_related("feed", "summary_model")
    if request.GET.get("feed"):
        items = items.filter(feed_id=request.GET["feed"])
    if request.GET.get("unsummarized"):
        items = items.filter(summarized_at__isnull=True)
    return JsonResponse({"items": [feed_item_to_dict(i) for i in items]})


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


urlpatterns = [
    path("ideas/", idea_list, name="api_idea_list"),
    path("ideas/<int:pk>/", idea_detail, name="api_idea_detail"),
    path("ideas/<int:pk>/effort/", idea_effort, name="api_idea_effort"),
    path("feeds/", feed_list, name="api_feed_list"),
    path("feed-items/", feed_item_list, name="api_feed_item_list"),
    path(
        "feed-items/<int:pk>/summarize/",
        feed_item_summarize,
        name="api_feed_item_summarize",
    ),
]
