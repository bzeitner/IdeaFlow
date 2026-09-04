from functools import wraps
from datetime import datetime, timedelta, timezone as dt_timezone
import json
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import BooleanField, Case, CharField, Count, DateTimeField, F, IntegerField, Min, OuterRef, Prefetch, Q, Subquery, When
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import Truncator
from django.views.decorators.http import require_POST
from executions.models import LLMRun, WorkflowDefinition

from .feeds import is_http_url, recent_articles
from .forms import ArtifactForm, IdeaForm, IdeaRelationForm, PodcastShowForm, PodcastSourceForm, ProfilePreferencesForm, ResearchEntryForm, ResourceFormSet
from .artifact_presentation import MAX_RENDER_CHARS, present_artifact, present_content, present_research_context
from .graph.projection import graph_projection
from .graph.capabilities import consume_capability, issue_capability
from .graph.export import graphml_export
from .models import AGENT_RUNS_BEFORE_FEEDBACK, Artifact, Category, Episode, EpisodeRun, EpisodeRunStatus, EpisodeStatus, FeedItem, FeedItemAssessment, GraphAccessCapability, HelpMessage, Idea, IdeaFeed, IdeaRelation, IdeaRelationSuggestion, PersonaReview, PodcastShow, Profile, RelationProvenance, RelationType, RepeatResult, RepeatResultStatus, ResearchEntry, Stage, Status, SuggestionStatus, WeeklySummary
from .podcast_views import serve_range_aware_file
from .presentation import render_research_context
from .weekly_metrics import metric_comparison_rows
from tools.task_selection import select_work

STAR_RANGE = [1, 2, 3, 4, 5]


def _stars(value):
    """[(n, filled), ...] for rendering a 1-5 star row from an optional value."""
    filled_to = value or 0
    return [(n, n <= filled_to) for n in STAR_RANGE]


def _recent_update_label(updated_at, now):
    """Human-scale age for Tracking updates from the last 24 hours."""
    elapsed_seconds = max(0, int((now - updated_at).total_seconds()))
    if elapsed_seconds >= 24 * 60 * 60:
        return ""
    total_minutes = elapsed_seconds // 60
    if total_minutes == 0:
        return "less than a minute ago"
    hours, minutes = divmod(total_minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return f"{' '.join(parts)} ago"


def _history_metrics(ideas):
    """All recorded effort for the ideas currently represented by a list page."""
    ideas = list(ideas)
    by_id = {idea.pk: idea for idea in ideas}
    task_counts = {idea.pk: 0 for idea in ideas}
    token_counts = {idea.pk: 0 for idea in ideas}
    models = {}
    categories = {}
    entries = ResearchEntry.objects.filter(idea_id__in=by_id).select_related(
        "idea__category", "model"
    )
    total_tasks = 0
    total_tokens = 0
    for entry in entries:
        total_tasks += 1
        task_counts[entry.idea_id] += 1
        tokens = entry.tokens_used or 0
        token_counts[entry.idea_id] += tokens
        total_tokens += tokens
        model = entry.execution_model or entry.model.name
        models[model] = models.get(model, 0) + tokens
        category = entry.idea.category.name if entry.idea.category_id else "Uncategorized"
        categories[category] = categories.get(category, 0) + tokens

    task_rows = {}
    token_rows = {}
    for idea in ideas:
        label = f"Idea #{idea.pk} — {idea.title}"
        task_rows[label] = task_counts[idea.pk]
        token_rows[label] = token_counts[idea.pk]
    for parent in ideas:
        child_ids = [idea.pk for idea in ideas if idea.parent_id == parent.pk]
        if not child_ids:
            continue
        label = f"Idea #{parent.pk} — {parent.title} + children (total)"
        family_ids = [parent.pk, *child_ids]
        task_rows[label] = sum(task_counts[idea_id] for idea_id in family_ids)
        token_rows[label] = sum(token_counts[idea_id] for idea_id in family_ids)

    sections = (
        ("Tasks by idea", task_rows),
        ("Tokens by idea", token_rows),
        ("Tokens by model", models),
        ("Tokens by category", categories),
    )
    rendered_sections = []
    for title, values in sections:
        rows = metric_comparison_rows(values)
        rows = _link_idea_metric_rows(rows, by_id)
        rendered_sections.append({"title": title, "rows": rows})
    return {
        "idea_count": len(ideas),
        "total_tasks": total_tasks,
        "total_tokens": total_tokens,
        "sections": rendered_sections,
    }


def _link_idea_metric_rows(rows, ideas_by_id):
    """Attach detail links and fill missing titles in persisted idea metric labels."""
    for row in rows:
        match = re.match(r"^Idea #(\d+)(.*)$", row["label"])
        if not match:
            continue
        idea_id = int(match.group(1))
        idea = ideas_by_id.get(idea_id)
        if idea is None:
            continue
        row["idea_id"] = idea_id
        family_total = "+ children (total)" in match.group(2)
        row["label"] = f"Idea #{idea_id} — {idea.title}"
        if family_total:
            row["label"] += " + children (total)"
    return rows

TAB_SPEC = [
    (Status.CURRENT, "Current", "ideas:current"),
    (Status.TRACKING, "Tracking", "ideas:tracking"),
    (Status.ARCHIVED, "Archive", "ideas:archive"),
]

ROLE_COLUMNS = [
    ("role_admin", "Admin"),
    ("role_current", "Current"),
    ("role_tracking", "Tracking"),
    ("role_archive", "Archive"),
    ("role_add_ideas", "Add Ideas"),
    ("role_graph", "Knowledge Graph"),
    ("role_weekly_summary", "Weekly Summary"),
    ("role_podcast", "Podcast"),
]
ROLE_FIELDS = [field for field, _label in ROLE_COLUMNS]


def role_required(*roles):
    """Gate a view on request.user.profile having role_admin or any of `roles`."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(request, *args, **kwargs):
            if request.user.profile.has_role(*roles):
                return view_func(request, *args, **kwargs)
            messages.error(request, "You don't have access to that.")
            return redirect("ideas:home")

        return wrapped

    return decorator


def _require_status_role(request, status):
    """Like role_required, but for views whose needed role depends on an
    idea's current status rather than being fixed at decoration time."""
    if request.user.profile.can_manage_status(status):
        return None
    messages.error(request, "You don't have access to that.")
    return redirect("ideas:home")


def _require_podcast_role(request):
    """Podcast production is gated by its own role, on top of whatever tab
    role governs the idea it lives on — like role_graph gates the whole
    Graph tab, role_podcast gates the whole podcast feature (setup, source
    links, and every episode action) regardless of what else the user can
    manage."""
    if request.user.profile.has_role("role_podcast"):
        return None
    messages.error(request, "You don't have access to that.")
    return redirect("ideas:home")


def _require_podcast_access(request, status):
    """The combined gate every mutating podcast view needs: the idea's own
    tab-management role, and the separate podcast role."""
    denied = _require_status_role(request, status)
    if denied:
        return denied
    return _require_podcast_role(request)


def _tabs(profile):
    """Tab definitions with live counts, filtered to what this user can see."""
    counts = dict(
        Idea.objects.values_list("status").annotate(n=Count("id")).values_list(
            "status", "n"
        )
    )
    tabs = [
        {"value": value, "label": label, "route": route, "count": counts.get(value, 0)}
        for value, label, route in TAB_SPEC
        if profile.can_manage_status(value)
    ]
    if profile.has_role("role_graph"):
        tabs.append(
            {
                "value": "graph",
                "label": "Graph",
                "route": "ideas:graph",
                "count": IdeaRelation.objects.count(),
            }
        )
    if profile.has_role("role_weekly_summary"):
        tabs.append(
            {
                "value": "weekly-summary",
                "label": "Weekly Summary",
                "route": "ideas:weekly_summaries",
                "count": WeeklySummary.objects.count(),
            }
        )
    return tabs


@role_required("role_weekly_summary")
def weekly_summaries(request):
    summaries = list(WeeklySummary.objects.all())
    ideas_by_id = Idea.objects.in_bulk()
    section_specs = (
        ("Tasks by type", "tasks_by_type"),
        ("Tasks by idea", "tasks_by_idea"),
        ("Pull requests", "prs"),
        ("Tokens by task", "tokens_by_task"),
        ("Tokens by model", "tokens_by_model"),
        ("Tokens by category", "tokens_by_category"),
        ("Tokens by idea", "tokens_by_idea"),
    )
    for index, summary in enumerate(summaries):
        summary.presentation = present_content(
            summary.content,
            source_format="markdown",
            report=True,
        )
        previous = summaries[index + 1].metrics if index + 1 < len(summaries) else {}
        summary.metric_sections = []
        for title, key in section_specs:
            rows = metric_comparison_rows(
                (summary.metrics or {}).get(key), (previous or {}).get(key)
            )
            rows = _link_idea_metric_rows(rows, ideas_by_id)
            summary.metric_sections.append({"title": title, "rows": rows})
    chronological = list(reversed(summaries))
    max_tokens = max(
        [1, *[(summary.metrics or {}).get("total_tokens", 0) for summary in chronological]]
    )
    max_prs = max(
        [1, *[sum(((summary.metrics or {}).get("prs") or {}).values()) for summary in chronological]]
    )
    trends = [
        {
            "summary": summary,
            "tokens": (summary.metrics or {}).get("total_tokens", 0),
            "token_width": round(
                (summary.metrics or {}).get("total_tokens", 0) * 100 / max_tokens
            ),
            "prs": (summary.metrics or {}).get("prs") or {},
            "pr_width": round(
                sum(((summary.metrics or {}).get("prs") or {}).values()) * 100 / max_prs
            ),
        }
        for summary in chronological
    ]
    return render(
        request,
        "ideas/weekly_summaries.html",
        {
            "summaries": summaries,
            "trends": trends,
            "tabs": _tabs(request.user.profile),
            "active": "weekly-summary",
        },
    )


@role_required("role_graph")
def graph(request):
    include_archived = request.GET.get("archived") == "1"
    return render(
        request,
        "ideas/graph.html",
        {
            "tabs": _tabs(request.user.profile),
            "active": "graph",
            "graph_data": graph_projection(include_archived=include_archived),
            "relation_form": IdeaRelationForm(),
            "include_archived": include_archived,
            "statuses": Status.choices,
            "relation_types": [("parent_of", "Parent of"), *RelationType.choices],
            "suggestions": IdeaRelationSuggestion.objects.filter(
                status=SuggestionStatus.PENDING
            ).select_related(
                "analyzed_idea", "source", "target", "relationship_council_review"
            ).prefetch_related("relationship_council_review__votes__persona"),
            "graph_lab_enabled": settings.IDEAFLOW_GRAPH_LAB_ENABLED,
        },
    )


@role_required("role_graph")
def graph_lab(request):
    if not settings.IDEAFLOW_GRAPH_LAB_ENABLED:
        messages.error(request, "Graph Lab is not enabled.")
        return redirect("ideas:graph")
    response = render(
        request,
        "ideas/graph_lab.html",
        {
            "tabs": _tabs(request.user.profile),
            "active": "graph",
            "graph_lab_origin": settings.IDEAFLOW_GRAPH_LAB_ORIGIN,
        },
    )
    response["Cache-Control"] = "no-store"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        f"frame-src {settings.IDEAFLOW_GRAPH_LAB_ORIGIN}; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    return response


@require_POST
@role_required("role_graph")
def graph_lab_capability(request):
    if not settings.IDEAFLOW_GRAPH_LAB_ENABLED:
        return JsonResponse({"error": "Graph Lab is disabled."}, status=503)
    archived = request.POST.get("archived") == "1"
    suggestions = request.POST.get("suggestions") == "1"
    try:
        confidence = float(request.POST.get("minimum_confidence", "0.55"))
    except ValueError:
        return JsonResponse({"error": "minimum_confidence must be a number."}, status=400)
    if not 0 <= confidence <= 1:
        return JsonResponse({"error": "minimum_confidence must be between 0 and 1."}, status=400)
    recent = GraphAccessCapability.objects.filter(
        user=request.user,
        created_at__gte=timezone.now() - timedelta(minutes=1),
    ).count()
    if recent >= 20:
        return JsonResponse({"error": "Too many Graph Lab sessions. Try again shortly."}, status=429)
    capability, raw = issue_capability(
        request.user,
        filters={"archived": archived, "suggestions": suggestions, "minimum_confidence": confidence},
    )
    response = JsonResponse(
        {
            "capability": raw,
            "expires_at": capability.expires_at.isoformat(),
            "graph_revision": capability.graph_revision,
            "export_url": request.build_absolute_uri(reverse("ideas:graph_lab_export")),
        }
    )
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    response["Referrer-Policy"] = "no-referrer"
    return response


def graph_lab_export(request):
    origin = request.headers.get("Origin", "")
    allowed_origin = settings.IDEAFLOW_GRAPH_LAB_ORIGIN
    if request.method == "OPTIONS":
        if origin != allowed_origin:
            return HttpResponse(status=403)
        response = HttpResponse(status=204)
        response["Access-Control-Allow-Origin"] = allowed_origin
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Authorization"
        response["Access-Control-Max-Age"] = "600"
        response["Vary"] = "Origin"
        return response
    def cors(response):
        if origin == allowed_origin:
            response["Access-Control-Allow-Origin"] = allowed_origin
            response["Vary"] = "Origin"
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    if request.method != "GET":
        return cors(HttpResponse(status=405, headers={"Allow": "GET, OPTIONS"}))
    if not settings.IDEAFLOW_GRAPH_LAB_ENABLED:
        return cors(JsonResponse({"error": "Graph Lab is disabled."}, status=503))
    if origin != allowed_origin:
        return JsonResponse({"error": "Origin is not allowed."}, status=403)
    authorization = request.headers.get("Authorization", "")
    prefix = "GraphCapability "
    if not authorization.startswith(prefix):
        return cors(JsonResponse({"error": "Missing graph capability."}, status=401))
    capability, error = consume_capability(authorization[len(prefix):].strip())
    if error:
        status = 403 if error == "forbidden" else 429 if error == "exhausted" else 401
        return cors(JsonResponse({"error": f"Graph capability is {error}."}, status=status))
    try:
        payload, node_count, edge_count, revision = graphml_export(filters=capability.filters)
    except ValueError as exc:
        return cors(JsonResponse({"error": str(exc)}, status=413))
    except OverflowError as exc:
        return cors(JsonResponse({"error": str(exc)}, status=413))
    response = HttpResponse(payload, content_type="application/graphml+xml")
    response["X-Graph-Revision"] = str(revision)
    response["X-Graph-Nodes"] = str(node_count)
    response["X-Graph-Edges"] = str(edge_count)
    return cors(response)


@role_required("role_graph")
def graph_relation_create(request):
    if request.method != "POST":
        return redirect("ideas:graph")
    form = IdeaRelationForm(request.POST)
    if form.is_valid():
        relation = form.save(commit=False)
        if not request.user.profile.can_manage_status(relation.source.status):
            messages.error(request, "You cannot manage the source idea.")
        else:
            relation.created_by = request.user
            relation.save()
            messages.success(request, "Relationship added.")
    else:
        messages.error(
            request,
            "; ".join(message for errors in form.errors.values() for message in errors),
        )
    return redirect("ideas:graph")


@role_required("role_graph")
def graph_relation_delete(request, pk):
    relation = get_object_or_404(IdeaRelation.objects.select_related("source"), pk=pk)
    if request.method == "POST" and request.user.profile.can_manage_status(relation.source.status):
        relation.delete()
        messages.success(request, "Relationship removed.")
    return redirect("ideas:graph")


@role_required("role_graph")
def graph_suggestion_review(request, pk, decision):
    wants_json = request.headers.get("Accept") == "application/json"
    suggestion = get_object_or_404(
        IdeaRelationSuggestion.objects.select_related("analyzed_idea", "source", "target"), pk=pk
    )
    if request.method != "POST" or decision not in {"accept", "reject"}:
        if wants_json:
            return JsonResponse({"error": "Invalid review request."}, status=405)
        return redirect("ideas:graph")
    if not request.user.profile.can_manage_status(suggestion.analyzed_idea.status):
        if wants_json:
            return JsonResponse({"error": "You cannot manage the source idea."}, status=403)
        messages.error(request, "You cannot manage the source idea.")
        return redirect("ideas:graph")
    if suggestion.status != SuggestionStatus.PENDING:
        if wants_json:
            return JsonResponse({"error": "That suggestion has already been reviewed."}, status=409)
        messages.info(request, "That suggestion has already been reviewed.")
        return redirect("ideas:graph")
    with transaction.atomic():
        if decision == "accept":
            try:
                relation, _created = IdeaRelation.objects.get_or_create(
                    source=suggestion.source,
                    target=suggestion.target,
                    relation_type=suggestion.relation_type,
                    defaults={
                        "description": suggestion.description,
                        "confidence": max(1, min(5, round(suggestion.confidence * 5))),
                        "provenance": RelationProvenance.AGENT,
                        "created_by": request.user,
                    },
                )
            except ValidationError as exc:
                error = "; ".join(
                    message for messages_list in exc.message_dict.values()
                    for message in messages_list
                )
                if wants_json:
                    return JsonResponse({"error": error}, status=409)
                messages.error(request, error)
                return redirect("ideas:graph")
            suggestion.accepted_relation = relation
            suggestion.status = SuggestionStatus.ACCEPTED
            message = "Relationship accepted."
        else:
            suggestion.status = SuggestionStatus.REJECTED
            message = "Suggestion rejected."
        suggestion.reviewed_by = request.user
        suggestion.reviewed_at = timezone.now()
        suggestion.save(update_fields=["status", "accepted_relation", "reviewed_by", "reviewed_at", "updated_at"])
    if wants_json:
        payload = {"ok": True, "decision": decision, "message": message}
        if decision == "accept":
            payload["edge"] = {
                "id": f"relation-{relation.pk}",
                "source": f"idea-{relation.source_id}",
                "target": f"idea-{relation.target_id}",
                "type": relation.relation_type,
                "label": relation.get_relation_type_display(),
                "description": relation.description,
                "confidence": relation.confidence,
                "provenance": relation.provenance,
            }
        return JsonResponse(payload)
    messages.success(request, message)
    return redirect("ideas:graph")


def _public_podcast_shows():
    # Only shows with something to actually listen to — an empty-looking
    # card for a show with zero published episodes isn't worth surfacing.
    return PodcastShow.objects.filter(
        is_publicly_listed=True, episodes__status=EpisodeStatus.PUBLISHED
    ).distinct()


def home(request):
    """Public marketing page for anonymous visitors; for anyone signed in, the
    home page lists the public projects (viewable by all, editable by none from
    here). Tab links in the top bar take role-holders to their workspace."""
    podcast_shows = _public_podcast_shows()
    if not request.user.is_authenticated:
        return render(request, "ideas/landing.html", {"podcast_shows": podcast_shows})
    profile = request.user.profile
    public = list(Idea.objects.filter(is_public=True).select_related("created_by", "category", "parent").prefetch_related("resources"))
    return render(
        request,
        "ideas/home.html",
        {
            "ideas": public,
            "history_metrics": _history_metrics(public),
            "tabs": _tabs(profile),
            "can_manage": False,
            "has_any_role": profile.has_role(
                "role_current", "role_tracking", "role_archive", "role_add_ideas"
            ),
            "podcast_shows": podcast_shows,
        },
    )


@login_required
def start(request):
    """Send a signed-in user to their explicit cross-device landing choice."""
    profile = request.user.profile
    if profile.default_landing_page == Profile.LandingPage.CURRENT and profile.has_role("role_current"):
        return redirect("ideas:current")
    if profile.default_landing_page == Profile.LandingPage.TRACKING and profile.has_role("role_tracking"):
        return redirect("ideas:tracking")
    if profile.default_landing_page == Profile.LandingPage.FEEDS and profile.can_read_feeds:
        return redirect("ideas:feeds")
    return redirect(f"{reverse('ideas:home')}?public=1")


def guide(request):
    """Public, in-app guide for prospective and newly invited users."""
    context = {}
    if request.user.is_authenticated:
        context["tabs"] = _tabs(request.user.profile)
    return render(request, "ideas/guide.html", context)


@login_required
def preferences(request):
    profile = request.user.profile
    if request.method == "POST":
        form = ProfilePreferencesForm(request.POST, instance=profile, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your preferences were saved and will follow you across devices.")
            return redirect("ideas:preferences")
    else:
        form = ProfilePreferencesForm(instance=profile, user=request.user)
    return render(
        request,
        "ideas/preferences.html",
        {"form": form, "tabs": _tabs(profile), "active": "preferences"},
    )


def _tab_view(request, status, template):
    owner_default = "mine" if request.user.profile.default_owner_scope == "mine" else ""
    owner_filter = request.GET.get("owner", owner_default)
    query = request.GET.get("q", "").strip()
    ideas = Idea.objects.filter(status=status).select_related("created_by", "category", "parent").prefetch_related("resources")
    if query:
        ideas = ideas.filter(
            Q(title__icontains=query)
            | Q(summary__icontains=query)
            | Q(notes__icontains=query)
            | Q(next_action__icontains=query)
        )
    if owner_filter == "mine":
        ideas = ideas.filter(created_by=request.user)
    elif owner_filter.isdigit():
        ideas = ideas.filter(created_by_id=int(owner_filter))
    ideas = list(ideas)
    return render(
        request,
        template,
        {
            "ideas": ideas,
            "history_metrics": _history_metrics(ideas),
            "tabs": _tabs(request.user.profile),
            "active": status,
            "can_manage": True,
            "owner_filter": owner_filter,
            "q": query,
            "owners": get_user_model().objects.filter(
                ideas_created__isnull=False
            ).distinct().order_by("email", "username"),
            "using_default_view": "owner" not in request.GET and bool(owner_default),
            "list_density": request.user.profile.list_density,
        },
    )


@role_required("role_current")
def current(request):
    return _tab_view(request, Status.CURRENT, "ideas/current.html")


@role_required("role_tracking")
def tracking(request):
    ideas = Idea.objects.filter(status=Status.TRACKING).select_related("created_by").prefetch_related(
        "resources", "research_entries"
    )
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    stage = request.GET.get("stage", "")
    attention = request.GET.get("attention", "")
    owner_default = "mine" if request.user.profile.default_owner_scope == "mine" else ""
    owner_filter = request.GET.get("owner", owner_default)
    ideas = ideas.annotate(
        latest_persona_review_status=Subquery(
            PersonaReview.objects.filter(idea_id=OuterRef("pk"))
            .order_by("-created_at")
            .values("status")[:1],
            output_field=CharField(),
        )
    )
    if query:
        ideas = ideas.filter(
            Q(title__icontains=query)
            | Q(summary__icontains=query)
            | Q(notes__icontains=query)
            | Q(next_action__icontains=query)
        )
    if category:
        ideas = ideas.filter(category__slug=category)
    if stage:
        ideas = ideas.filter(stage__slug=stage)
    if owner_filter == "mine":
        ideas = ideas.filter(created_by=request.user)
    elif owner_filter.isdigit():
        ideas = ideas.filter(created_by_id=int(owner_filter))
    if attention == "paused":
        ideas = ideas.filter(
            agent_runs_since_feedback__gte=AGENT_RUNS_BEFORE_FEEDBACK
        )
    elif attention == "no-next-action":
        ideas = ideas.filter(next_action="")
    elif attention == "council":
        ideas = ideas.filter(
            latest_persona_review_status=PersonaReview.Status.NO_CONSENSUS,
            last_persona_review_at__gte=F("last_meaningful_progress_at"),
        )

    ideas = ideas.annotate(
        tracking_child_count=Count(
            "children",
            filter=Q(children__status=Status.TRACKING),
            distinct=True,
        )
    )

    sort = request.GET.get("sort", request.user.profile.default_tracking_sort)
    orderings = {
        "questions": (
            "family_rank",
            "family_id",
            "family_position",
            "rank",
            "id",
        ),
        "family": (
            "family_rank",
            "family_id",
            "family_position",
            "rank",
            "id",
        ),
        "rank": ("rank", "-interest_level", "-updated_at"),
        "interest": ("-interest_level", "rank", "-updated_at"),
        "updated": ("-updated_at", "rank"),
        "oldest": ("updated_at", "rank"),
    }
    if sort not in orderings:
        sort = "questions"
    if sort in {"questions", "family"}:
        ideas = ideas.annotate(
            family_rank=Case(
                When(parent__isnull=True, then=F("rank")),
                default=F("parent__rank"),
                output_field=IntegerField(),
            ),
            family_id=Case(
                When(parent__isnull=True, then=F("id")),
                default=F("parent_id"),
                output_field=IntegerField(),
            ),
            family_position=Case(
                When(parent__isnull=True, then=0),
                default=1,
                output_field=IntegerField(),
            ),
        )
    ideas = ideas.order_by(*orderings[sort])
    if sort == "questions":
        # JSON answers are interpreted by the model property so behavior stays
        # consistent on SQLite and PostgreSQL. Stable sorting retains the
        # family/rank ordering above within each priority group.
        ideas = sorted(ideas, key=lambda idea: idea.open_question_count == 0)
    ideas = list(ideas)
    now = timezone.now()
    for idea in ideas:
        idea.recent_update_label = _recent_update_label(idea.updated_at, now)
    return render(
        request,
        "ideas/tracking.html",
        {
            "ideas": ideas,
            "history_metrics": _history_metrics(ideas),
            "tabs": _tabs(request.user.profile),
            "active": Status.TRACKING,
            "can_manage": True,
            "categories": Category.objects.filter(is_active=True),
            "stages": Stage.objects.filter(is_active=True),
            "owners": get_user_model().objects.filter(
                ideas_created__isnull=False
            ).distinct().order_by("email", "username"),
            "filters": {
                "q": query,
                "category": category,
                "stage": stage,
                "attention": attention,
                "owner": owner_filter,
                "sort": sort,
            },
            "using_default_view": not any(name in request.GET for name in ("owner", "sort")),
            "list_density": request.user.profile.list_density,
        },
    )


@role_required("role_archive")
def archive(request):
    return _tab_view(request, Status.ARCHIVED, "ideas/archive.html")


@login_required
def detail(request, pk):
    idea = get_object_or_404(
        Idea.objects.select_related("parent", "created_by").prefetch_related(
            "resources", "artifacts__research_entry", "referenced_artifacts__idea", "research_entries", "research_entries__model", "children", "repeat_results",
            "idea_personas__persona", "podcast_show__episodes__runs",
            "incoming_relations__source",
        ),
        pk=pk,
    )
    can_manage = request.user.profile.can_manage_status(idea.status)
    # Public ideas are readable by any signed-in user; non-public ones still
    # require the tab's role. Editing stays gated on can_manage either way.
    if not (idea.is_public or can_manage):
        messages.error(request, "You don't have access to that.")
        return redirect("ideas:home")
    idea_feeds = idea.idea_feeds.select_related("feed").order_by(
        F("rating").desc(nulls_last=True), "-created_at"
    )
    research_with_open_questions = [
        entry for entry in idea.research_entries.all() if entry.unanswered_question_items
    ]
    research_entries = list(idea.research_entries.all())
    research_entry_ids = {entry.pk for entry in research_entries}
    for entry in research_entries:
        entry.rendered_excerpt = render_research_context(
            Truncator(entry.context).words(45), research_entry_ids
        )
    has_podcast_role = request.user.profile.has_role("role_podcast")
    podcast_show = getattr(idea, "podcast_show", None)
    podcast_sources = [
        relation for relation in idea.incoming_relations.all()
        if relation.relation_type == RelationType.SUPPORTS
    ] if podcast_show else []
    return render(
        request,
        "ideas/detail.html",
        {
            "idea": idea,
            "tabs": _tabs(request.user.profile),
            "active": idea.status,
            "can_manage": can_manage,
            "has_podcast_role": has_podcast_role,
            "idea_feeds": idea_feeds,
            "articles": recent_articles(idea),
            "repeat_result_statuses": RepeatResultStatus.choices,
            "research_with_open_questions": research_with_open_questions,
            "research_entries": research_entries,
            "suggested_children": [
                line for line in idea.suggested_children.splitlines() if line.strip()
            ],
            "podcast_sources": podcast_sources,
            "podcast_source_form": (
                PodcastSourceForm(user=request.user, exclude_idea=idea)
                if podcast_show and can_manage and has_podcast_role else None
            ),
        },
    )


@login_required
def idea_form(request, pk=None):
    idea = get_object_or_404(Idea, pk=pk) if pk else None
    profile = request.user.profile

    if idea is None:
        if not profile.has_role("role_add_ideas"):
            messages.error(request, "You don't have access to add ideas.")
            return redirect("ideas:home")
    else:
        denied = _require_status_role(request, idea.status)
        if denied:
            return denied

    if request.method == "POST":
        form = IdeaForm(request.POST, instance=idea, owner=request.user if idea is None else None)
        formset = ResourceFormSet(request.POST, instance=idea)
        if form.is_valid() and formset.is_valid():
            if idea is None:
                # role_add_ideas only grants creating ideas, not a target tab —
                # force new ideas into Current regardless of what "status" was
                # submitted, so a role_add_ideas-only user can't write directly
                # into a tab (e.g. archived) they have no role to manage.
                requested_status = form.cleaned_data.get("status", profile.default_new_idea_status)
                form.instance.status = requested_status if profile.can_manage_status(requested_status) else Status.CURRENT
                form.instance.created_by = request.user
            saved = form.save()
            formset.instance = saved
            formset.save()
            messages.success(request, f"Saved “{saved.title}”.")
            return redirect(saved)
    else:
        # Prefill a new idea from Web Share Target params (manifest maps a share
        # to /new/?title=&text=&url=).
        initial = {}
        resource_initial = None
        if idea is None:
            preferred_status = profile.default_new_idea_status
            initial["status"] = preferred_status if profile.can_manage_status(preferred_status) else Status.CURRENT
            initial["is_public"] = profile.default_new_idea_public
            shared_title = request.GET.get("title") or request.GET.get("name")
            shared_text = request.GET.get("text")
            shared_url = request.GET.get("url")
            if shared_title:
                initial["title"] = shared_title[:200]
            if shared_text:
                initial["summary"] = shared_text
            if shared_url:
                resource_initial = [{"url": shared_url}]
            # "+ Add child idea" links here with ?parent=<id>.
            parent_id = request.GET.get("parent")
            if parent_id and parent_id.isdigit():
                initial["parent"] = parent_id
        form = IdeaForm(instance=idea, initial=initial or None, owner=request.user if idea is None else None)
        formset = ResourceFormSet(instance=idea, initial=resource_initial)
    return render(
        request,
        "ideas/idea_form.html",
        {
            "form": form,
            "formset": formset,
            "idea": idea,
            "tabs": _tabs(profile),
            "active": idea.status if idea else None,
        },
    )


@login_required
def set_status(request, pk, status):
    """Move an idea between tabs from a list-row button."""
    if request.method != "POST":
        return redirect("ideas:home")
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    if status in {s.value for s in Status}:
        idea.status = status
        idea.save(update_fields=["status", "updated_at"])
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": True, "idea_id": idea.pk, "status": status})
        messages.success(
            request, f"Moved “{idea.title}” to {idea.get_status_display()}."
        )
    return redirect(request.POST.get("next") or "ideas:home")


@login_required
def set_next_action(request, pk):
    """Replace the active queue item from the idea detail page."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    idea.replace_active_next_action(request.POST.get("next_action", ""))
    # A human next action is feedback — clear the pause counter.
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
    messages.success(request, "Next action saved.")
    return redirect("ideas:detail", pk=pk)


@login_required
def queue_next_action(request, pk):
    """Add, remove, complete, or reorder an idea's queued next actions."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied

    operation = request.POST.get("operation", "add")
    queue = [str(item).strip() for item in idea.next_actions if str(item).strip()]
    if not queue and idea.next_action.strip():
        queue = [idea.next_action.strip()]
    if operation == "add":
        if idea.enqueue_next_action(request.POST.get("next_action", "")):
            messages.success(request, "Next action queued.")
        else:
            messages.info(request, "Enter a new action that is not already queued.")
    else:
        try:
            index = int(request.POST.get("index", ""))
        except ValueError:
            index = -1
        if 0 <= index < len(queue):
            if operation in {"complete", "remove"}:
                queue.pop(index)
                messages.success(
                    request,
                    "Next action completed." if operation == "complete" else "Next action removed.",
                )
            elif operation == "up" and index > 0:
                queue[index - 1], queue[index] = queue[index], queue[index - 1]
            elif operation == "down" and index < len(queue) - 1:
                queue[index + 1], queue[index] = queue[index], queue[index + 1]
            idea.next_actions = queue
            idea.next_action = queue[0] if queue else ""

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
    return redirect("ideas:detail", pk=pk)


@login_required
def update_repeat_result(request, pk, result_pk):
    wants_json = request.headers.get("Accept") == "application/json"
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"error": "Invalid request."}, status=405)
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    result = get_object_or_404(RepeatResult, pk=result_pk, idea=idea)
    status = request.POST.get("status")
    if status in RepeatResultStatus.values:
        result.status = status
        result.save(update_fields=["status", "updated_at"])
        if wants_json:
            return JsonResponse(
                {"ok": True, "result_id": result.pk, "status": result.status}
            )
        messages.success(request, "Result status updated.")
    elif wants_json:
        return JsonResponse({"error": "Invalid result status."}, status=400)
    return redirect("ideas:detail", pk=pk)


@login_required
def answer_research_questions(request, pk, entry_pk):
    wants_json = request.headers.get("Accept") == "application/json"
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"error": "Invalid request."}, status=405)
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    entry = get_object_or_404(ResearchEntry, pk=entry_pk, idea=idea)
    questions = entry.open_questions if isinstance(entry.open_questions, list) else []
    answers = entry.question_answers.copy() if isinstance(entry.question_answers, dict) else {}
    saved = 0
    for index, question in enumerate(questions):
        if not str(question).strip():
            continue
        answer = request.POST.get(f"answer_{index}", "").strip()
        if answer:
            answers[str(index)] = answer
            saved += 1
    if not saved:
        if wants_json:
            return JsonResponse({"error": "Enter at least one answer."}, status=400)
        messages.info(request, "Enter at least one answer.")
        return redirect("ideas:detail", pk=pk)
    entry.question_answers = answers
    entry.save(update_fields=["question_answers"])
    idea.agent_runs_since_feedback = 0
    idea.last_meaningful_progress_at = timezone.now()
    idea.save(update_fields=["agent_runs_since_feedback", "last_meaningful_progress_at", "updated_at"])
    if wants_json:
        return JsonResponse({"ok": True, "saved": saved})
    messages.success(request, "Research answers saved for the next run.")
    return redirect("ideas:detail", pk=pk)


@login_required
def toggle_repeat_pause(request, pk):
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk, repeat_enabled=True)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    idea.repeat_paused = not idea.repeat_paused
    idea.save(update_fields=["repeat_paused", "updated_at"])
    messages.success(request, "Repeat task paused." if idea.repeat_paused else "Repeat task resumed.")
    return redirect("ideas:detail", pk=pk)


@login_required
def update_persona_council(request, pk):
    """Update stalled council-review scheduling directly from the idea page."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    try:
        stall_days = int(request.POST.get("persona_stall_days", ""))
    except (TypeError, ValueError):
        stall_days = 0
    if not 1 <= stall_days <= 3650:
        messages.error(request, "Council review timeframe must be between 1 and 3650 days.")
        return redirect("ideas:detail", pk=pk)
    idea.persona_review_enabled = request.POST.get("persona_review_enabled") == "on"
    idea.persona_stall_days = stall_days
    idea.save(update_fields=["persona_review_enabled", "persona_stall_days", "updated_at"])
    messages.success(request, "Persona council review settings saved.")
    return redirect("ideas:detail", pk=pk)


@login_required
def toggle_persona_review_pause(request, pk):
    """Manually hold council review without disabling its schedule. Unlike the
    ordinary human-feedback pause (is_paused), council review otherwise runs
    even on a paused idea, so this is its own dedicated switch."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk, persona_review_enabled=True)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    idea.persona_review_paused = not idea.persona_review_paused
    idea.save(update_fields=["persona_review_paused", "updated_at"])
    messages.success(
        request,
        "Council review paused." if idea.persona_review_paused else "Council review resumed.",
    )
    return redirect("ideas:detail", pk=pk)


@login_required
def create_suggested_child(request, pk):
    """Turn one agent suggestion into a child idea with a single click."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    parent = get_object_or_404(Idea, pk=pk)
    if not request.user.profile.has_role("role_add_ideas"):
        messages.error(request, "You don't have access to add ideas.")
        return redirect("ideas:detail", pk=pk)

    title = request.POST.get("title", "").strip()
    suggestions = [
        line for line in parent.suggested_children.splitlines() if line.strip()
    ]
    if not title or title not in suggestions:
        messages.error(request, "That child-idea suggestion is no longer available.")
        return redirect("ideas:detail", pk=pk)

    child = Idea.objects.create(
        title=title[:200],
        category=parent.category,
        parent=parent,
        status=Status.CURRENT,
        created_by=request.user,
    )
    suggestions.remove(title)
    parent.suggested_children = "\n".join(suggestions)
    parent.save(update_fields=["suggested_children", "updated_at"])
    messages.success(request, f"Created child idea “{child.title}”.")
    return redirect("ideas:detail", pk=child.pk)


@login_required
def quick_update(request, pk):
    """Update prioritization fields directly from the Tracking table."""
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    if request.method == "POST":
        field = request.POST.get("field")
        value = request.POST.get("value", "").strip()
        updated = False
        if field == "rank" and value.isdigit():
            idea.rank = int(value)
            idea.save(update_fields=["rank", "updated_at"])
            updated = True
        elif field == "stage":
            stage = Stage.objects.filter(pk=value, is_active=True).first() if value else None
            if value == "" or stage:
                idea.stage = stage
                idea.save(update_fields=["stage", "updated_at"])
                updated = True
        elif field == "next_action":
            idea.replace_active_next_action(value)
            idea.agent_runs_since_feedback = 0
            idea.save(
                update_fields=[
                    "next_action",
                    "next_actions",
                    "agent_runs_since_feedback",
                    "updated_at",
                ]
            )
            updated = True
        if request.headers.get("Accept") == "application/json":
            if updated:
                return JsonResponse({"ok": True, "field": field, "value": value})
            return JsonResponse({"ok": False, "error": "Choose a valid value."}, status=400)
        if updated:
            messages.success(request, f"Updated “{idea.title}”.")
        else:
            messages.error(request, "That quick update could not be saved.")
    back = request.POST.get("next", "")
    return redirect(f"{reverse('ideas:tracking')}{back}")


@login_required
def add_research(request, pk):
    """Log research without opening the idea's full edit form."""
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    form = ResearchEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.idea = idea
        entry.save()
        messages.success(request, f"Research logged for “{idea.title}”.")
        return redirect("ideas:detail", pk=pk)
    return render(
        request,
        "ideas/research_form.html",
        {
            "idea": idea,
            "form": form,
            "tabs": _tabs(request.user.profile),
            "active": idea.status,
        },
    )


@login_required
def artifact_form(request, pk, artifact_pk=None):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    artifact = (
        get_object_or_404(Artifact, pk=artifact_pk, idea=idea)
        if artifact_pk else Artifact(idea=idea)
    )
    form = ArtifactForm(
        request.POST or None,
        request.FILES or None,
        instance=artifact,
        idea=idea,
    )
    if request.method == "POST" and form.is_valid():
        old_file_name = ""
        if artifact.pk and "file" in form.changed_data:
            old_file_name = Artifact.objects.get(pk=artifact.pk).file.name
        artifact = form.save(commit=False)
        artifact.idea = idea
        artifact.save()
        if old_file_name and old_file_name != artifact.file.name:
            artifact.file.storage.delete(old_file_name)
        messages.success(request, f"Artifact saved for “{idea.title}”.")
        return redirect("ideas:detail", pk=pk)
    return render(
        request,
        "ideas/artifact_form.html",
        {
            "idea": idea,
            "artifact": artifact if artifact.pk else None,
            "form": form,
            "tabs": _tabs(request.user.profile),
            "active": idea.status,
        },
    )


@login_required
@require_POST
def delete_artifact(request, pk, artifact_pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    artifact = get_object_or_404(Artifact, pk=artifact_pk, idea=idea)
    title = artifact.title
    if artifact.file:
        artifact.file.storage.delete(artifact.file.name)
    artifact.delete()
    messages.success(request, f"Deleted artifact “{title}”.")
    return redirect("ideas:detail", pk=pk)


@login_required
def podcast_show_form(request, pk):
    """Create or edit the PodcastShow attached to this idea — the show's
    setup used to be admin-only; this is the same form, on the idea's own
    page, gated the same way as every other idea-editing action."""
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    show = getattr(idea, "podcast_show", None)
    form = PodcastShowForm(request.POST or None, request.FILES or None, instance=show)
    if request.method == "POST" and form.is_valid():
        show = form.save(commit=False)
        show.idea = idea
        show.save()
        messages.success(request, "Podcast settings saved.")
        return redirect("ideas:detail", pk=pk)
    return render(
        request,
        "ideas/podcast_show_form.html",
        {
            "idea": idea,
            "show": show,
            "form": form,
            "tabs": _tabs(request.user.profile),
            "active": idea.status,
        },
    )


@login_required
@require_POST
def add_podcast_source(request, pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    if getattr(idea, "podcast_show", None) is None:
        messages.error(request, "Set up the podcast before adding a research source.")
        return redirect("ideas:detail", pk=pk)
    form = PodcastSourceForm(request.POST, user=request.user, exclude_idea=idea)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return redirect("ideas:detail", pk=pk)
    source = form.cleaned_data["source"]
    _, created = IdeaRelation.objects.get_or_create(
        source=source, target=idea, relation_type=RelationType.SUPPORTS,
        defaults={"created_by": request.user, "provenance": RelationProvenance.HUMAN},
    )
    if created:
        messages.success(request, f"“{source.title}” now feeds this podcast's research.")
    else:
        messages.error(request, "That idea is already connected.")
    return redirect("ideas:detail", pk=pk)


@login_required
@require_POST
def remove_podcast_source(request, pk, relation_pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    relation = get_object_or_404(
        IdeaRelation, pk=relation_pk, target=idea, relation_type=RelationType.SUPPORTS
    )
    relation.delete()
    messages.success(request, "Research source removed.")
    return redirect("ideas:detail", pk=pk)


def _get_episode(idea, episode_pk):
    return get_object_or_404(
        Episode.objects.select_related("show").prefetch_related("runs"),
        pk=episode_pk, show__idea=idea,
    )


@login_required
def episode_audio_preview(request, pk, episode_pk):
    """Authenticated preview for reviewing a not-yet-published episode — the
    public audio endpoint (ideas/podcast_views.py) only ever serves published
    episodes, so a reviewer needs a separate, login-gated way to listen."""
    idea = get_object_or_404(Idea, pk=pk)
    can_manage = request.user.profile.can_manage_status(idea.status)
    if not (idea.is_public or can_manage):
        messages.error(request, "You don't have access to that.")
        return redirect("ideas:home")
    denied = _require_podcast_role(request)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    if not episode.audio_file:
        raise Http404
    return serve_range_aware_file(
        request, episode.audio_file, episode.audio_mime_type or "audio/mpeg"
    )


@login_required
def episode_review(request, pk, episode_pk):
    idea = get_object_or_404(Idea, pk=pk)
    can_manage = request.user.profile.can_manage_status(idea.status)
    if not (idea.is_public or can_manage):
        messages.error(request, "You don't have access to that.")
        return redirect("ideas:home")
    denied = _require_podcast_role(request)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    latest_run = episode.runs.order_by("-created_at").first()
    return render(
        request,
        "ideas/episode_review.html",
        {
            "idea": idea,
            "episode": episode,
            "latest_run": latest_run,
            "can_manage": can_manage,
            "tabs": _tabs(request.user.profile),
            "active": idea.status,
        },
    )


@login_required
@require_POST
def update_episode(request, pk, episode_pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    episode.title = (request.POST.get("title") or episode.title).strip()[:300]
    episode.description = (request.POST.get("description") or "").strip()
    episode.show_notes = (request.POST.get("show_notes") or "").strip()
    episode.save(update_fields=["title", "description", "show_notes", "updated_at"])
    messages.success(request, "Episode updated.")
    return redirect("ideas:episode_review", pk=pk, episode_pk=episode_pk)


@login_required
@require_POST
def approve_and_publish_episode(request, pk, episode_pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    if not episode.audio_file:
        messages.error(request, "This episode has no rendered audio yet.")
        return redirect("ideas:episode_review", pk=pk, episode_pk=episode_pk)
    episode.publish(by=request.user)
    messages.success(request, f"Published “{episode.title}”.")
    return redirect("ideas:episode_review", pk=pk, episode_pk=episode_pk)


@login_required
@require_POST
def reject_episode(request, pk, episode_pk):
    """A reviewer declines the most recent render — keeps the episode in
    draft (never publishable in its current state) without deleting
    anything, so its script/render-report stay available to inspect."""
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    run = episode.runs.filter(status=EpisodeRunStatus.READY_FOR_REVIEW).order_by("-created_at").first()
    if run:
        run.status = EpisodeRunStatus.FAILED
        run.error_class = "rejected_by_reviewer"
        run.error_detail = f"Rejected by {request.user.email} during review."
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error_class", "error_detail", "completed_at"])
        messages.success(request, "Episode rejected.")
    else:
        messages.error(request, "Nothing to reject — no run is currently ready for review.")
    return redirect("ideas:episode_review", pk=pk, episode_pk=episode_pk)


@login_required
@require_POST
def cancel_episode_run(request, pk, episode_pk):
    """Only for a run still actually in flight — a run that's already
    ready_for_review is Reject's job, not Cancel's, so it's excluded here
    too (alongside the terminal statuses) rather than letting either
    action silently do the other's job on the same run."""
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    run = episode.runs.exclude(
        status__in=[
            EpisodeRunStatus.READY_FOR_REVIEW, EpisodeRunStatus.PUBLISHED,
            EpisodeRunStatus.FAILED, EpisodeRunStatus.CANCELLED,
        ]
    ).order_by("-created_at").first()
    if run:
        run.status = EpisodeRunStatus.CANCELLED
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "completed_at"])
        messages.success(request, "Run cancelled.")
    else:
        messages.error(request, "Nothing to cancel — no run is currently in progress.")
    return redirect("ideas:episode_review", pk=pk, episode_pk=episode_pk)


@login_required
@require_POST
def regenerate_episode(request, pk, episode_pk):
    """Full re-render, not per-segment regeneration: a new EpisodeRun with a
    fresh job directory (keyed by the new run's own id) so the worker starts
    clean rather than reusing anything from a rejected attempt. Per-segment
    regeneration would need the worker to key job directories by episode
    rather than run so a resumed render can selectively reuse segments —
    deferred, not silently different from what the plan describes."""
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    previous = episode.runs.order_by("-created_at").first()
    if previous is None:
        messages.error(request, "This episode has no prior render to regenerate from.")
        return redirect("ideas:episode_review", pk=pk, episode_pk=episode_pk)
    new_run = EpisodeRun.objects.create(
        episode=episode,
        status=EpisodeRunStatus.AWAITING_AUDIO,
        manifest=previous.manifest,
        engine=previous.engine,
        model_repo=previous.model_repo,
        model_revision=previous.model_revision,
        rendering_settings=previous.rendering_settings,
    )
    # The copied manifest still names the *previous* run — worker.py never
    # actually reads manifest["run_id"]/["episode_id"] (it keys off the
    # claim response's own id), so this has no functional effect today, but
    # a manifest that names the wrong run is a real data-hygiene bug
    # waiting to confuse whoever next reads it. Same two-step pattern
    # idea_podcast_episode (ideas/api.py) already uses.
    if isinstance(new_run.manifest, dict) and new_run.manifest:
        new_run.manifest["run_id"] = new_run.pk
        new_run.manifest["episode_id"] = episode.pk
        new_run.save(update_fields=["manifest"])
    messages.success(request, "Queued for regeneration.")
    return redirect("ideas:episode_review", pk=pk, episode_pk=episode_pk)


@login_required
@require_POST
def unpublish_episode(request, pk, episode_pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    episode.unpublish()
    messages.success(request, f"Unpublished “{episode.title}”.")
    return redirect("ideas:detail", pk=pk)


@login_required
@require_POST
def delete_episode(request, pk, episode_pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_podcast_access(request, idea.status)
    if denied:
        return denied
    episode = _get_episode(idea, episode_pk)
    title = episode.title
    if episode.audio_file:
        episode.audio_file.storage.delete(episode.audio_file.name)
    episode.delete()
    messages.success(request, f"Deleted episode “{title}”.")
    return redirect("ideas:detail", pk=pk)


@login_required
@require_POST
def request_summary(request, pk):
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    if idea.summary_requested_at:
        messages.info(request, "A Summary artifact is already scheduled.")
    else:
        idea.summary_requested_at = timezone.now()
        idea.save(update_fields=["summary_requested_at", "updated_at"])
        messages.success(request, f"Summary artifact scheduled for “{idea.title}”.")
    return redirect("ideas:detail", pk=pk)


def _idea_access_or_denied(request, idea):
    if idea.is_public or request.user.profile.can_manage_status(idea.status):
        return None
    messages.error(request, "You don't have access to that.")
    return redirect("ideas:home")


def _artifact_access_or_denied(request, artifact):
    return _idea_access_or_denied(request, artifact.idea)


@login_required
def view_research_entry(request, pk, entry_pk):
    entry = get_object_or_404(
        ResearchEntry.objects.select_related("idea", "model"),
        pk=entry_pk,
        idea_id=pk,
    )
    denied = _idea_access_or_denied(request, entry.idea)
    if denied:
        return denied
    reference_urls = {
        entry_id: f'{reverse("ideas:detail", args=[pk])}#research-entry-{entry_id}'
        for entry_id in entry.idea.research_entries.values_list("pk", flat=True)
    }
    presentation = present_research_context(
        entry.context,
        request.GET.get("view", ""),
        reference_urls=reference_urls,
    )
    return render(
        request,
        "ideas/research_entry_view.html",
        {
            "idea": entry.idea,
            "entry": entry,
            "presentation": presentation,
            "tabs": _tabs(request.user.profile),
        },
    )


@login_required
def download_artifact(request, pk, artifact_pk):
    artifact = get_object_or_404(
        Artifact.objects.select_related("idea"), pk=artifact_pk, idea_id=pk
    )
    denied = _artifact_access_or_denied(request, artifact)
    if denied:
        return denied
    if not artifact.file:
        return redirect(artifact.url)
    return FileResponse(
        artifact.file.open("rb"),
        as_attachment=True,
        filename=artifact.download_filename,
    )


@login_required
def view_artifact(request, pk, artifact_pk):
    artifact = get_object_or_404(
        Artifact.objects.select_related("idea"), pk=artifact_pk, idea_id=pk
    )
    denied = _artifact_access_or_denied(request, artifact)
    if denied:
        return denied
    if not artifact.is_viewable:
        return redirect(artifact.link)
    idea = artifact.idea
    preview_byte_limit = MAX_RENDER_CHARS * 4
    with artifact.file.open("rb") as f:
        raw = f.read(preview_byte_limit + 1)
    source_truncated = len(raw) > preview_byte_limit
    content = raw[:preview_byte_limit].decode("utf-8", errors="replace")
    presentation = present_artifact(
        artifact,
        content,
        request.GET.get("view", ""),
        source_truncated=source_truncated,
    )
    return render(
        request,
        "ideas/artifact_view.html",
        {
            "idea": idea,
            "artifact": artifact,
            "content": content,
            "presentation": presentation,
            "tabs": _tabs(request.user.profile),
        },
    )


@login_required
def continue_work(request, pk):
    """Resume agent work on a paused idea (clears the run-since-feedback count)."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    idea.agent_runs_since_feedback = 0
    idea.save(update_fields=["agent_runs_since_feedback", "updated_at"])
    messages.success(request, "Resumed — agents can work this idea again.")
    return redirect("ideas:detail", pk=pk)


@login_required
def artifacts(request):
    """Every artifact this user can see, across all ideas."""
    profile = request.user.profile
    accessible_statuses = [status for status in Status.values if profile.can_manage_status(status)]
    items = (
        Artifact.objects.select_related("idea")
        .filter(Q(idea__is_public=True) | Q(idea__status__in=accessible_statuses))
        .order_by("-generated_at", "-updated_at")
    )
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    source = request.GET.get("format", "")
    if query:
        items = items.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(idea__title__icontains=query))
    if kind in Artifact.Kind.values:
        items = items.filter(kind=kind)
    if source:
        extensions = {
            "markdown": (".md", ".markdown"),
            "html": (".html", ".htm"),
            "plain": (".txt", ".rst"),
            "yaml": (".yaml", ".yml"),
        }.get(source, (f".{source}",))
        extension_query = Q()
        for extension in extensions:
            extension_query |= Q(file__iendswith=extension)
        items = items.filter(Q(source_format=source) | extension_query)
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    recent_cutoff = timezone.now() - timedelta(days=1)
    for artifact in page.object_list:
        artifact.can_manage = profile.can_manage_status(artifact.idea.status)
        artifact.is_recent = bool(artifact.generated_at) and artifact.generated_at >= recent_cutoff
    # Published episodes are their own kind of artifact, but live on Episode/
    # PodcastShow rather than the Artifact model, so they're queried and
    # listed separately here. Only publicly-listed shows' published episodes
    # qualify — same visibility rule the public podcast pages themselves use.
    episodes = (
        Episode.objects.filter(status=EpisodeStatus.PUBLISHED, show__is_publicly_listed=True)
        .select_related("show")
        .order_by("-published_at")
    ) if not (query or kind or source) else Episode.objects.none()
    return render(
        request,
        "ideas/artifacts.html",
        {
            "page": page,
            "episodes": episodes,
            "tabs": _tabs(profile),
            "active": "artifacts",
            "filters": {"q": query, "kind": kind, "format": source},
            "artifact_kinds": Artifact.Kind.choices,
            "artifact_formats": ("markdown", "html", "csv", "tsv", "json", "plain", "log"),
        },
    )


@role_required("role_current", "role_tracking", "role_archive")
def feeds(request):
    """Read the shared feed items and rate them (interest + info value)."""
    profile = request.user.profile
    accessible_statuses = [
        status for status, role in Profile.STATUS_ROLE.items() if profile.has_role(role)
    ]
    accessible_ideas = Idea.objects.filter(
        Q(is_public=True) | Q(status__in=accessible_statuses)
    ).select_related("category")

    items = FeedItem.objects.select_related("feed", "summary_model")
    total_count = items.count()

    idea_value = request.GET.get("idea", "")
    idea_lookup = request.GET.get("idea_lookup", "").strip().removeprefix("#")
    if not idea_value and idea_lookup.isdigit():
        idea_value = idea_lookup
    selected_idea = None
    if idea_value.isdigit():
        selected_idea = accessible_ideas.filter(pk=int(idea_value)).first()

    category_value = request.GET.get("category", "")
    selected_category = None
    if category_value:
        selected_category = Category.objects.filter(
            slug=category_value, ideas__in=accessible_ideas
        ).distinct().first()

    scoped_ideas = accessible_ideas
    if selected_idea is not None:
        scoped_ideas = scoped_ideas.filter(pk=selected_idea.pk)
    if selected_category is not None:
        scoped_ideas = scoped_ideas.filter(category=selected_category)
    if selected_idea is not None or selected_category is not None:
        items = items.filter(feed__idea_feeds__idea__in=scoped_ideas).distinct()

    unrated = bool(request.GET.get("unrated"))
    if unrated:
        # Summarized only: an unsummarized item has nothing to read but its
        # title, and the summarized rows are otherwise buried thousands deep.
        items = items.filter(interest__isnull=True, summarized_at__isnull=False)

    sort = request.GET.get("sort", request.user.profile.default_feed_sort)
    orderings = {
        "published_desc": (F("published_at").desc(nulls_last=True), "-created_at", "-id"),
        "published_asc": ("published_missing", "published_at", "created_at", "id"),
        "downloaded_desc": ("-created_at", "-id"),
        "feed": ("sort_feed", "-published_at", "-id"),
        "idea": ("sort_idea", "-published_at", "-id"),
        "category": ("sort_category", "-published_at", "-id"),
    }
    if sort not in orderings:
        sort = "published_desc"
    if sort == "published_asc":
        # Avoid backend-specific ASC NULLS LAST SQL, particularly on a
        # DISTINCT queryset produced by Idea/topic filtering.
        items = items.annotate(
            published_missing=Case(
                When(
                    Q(published_at__isnull=True)
                    | Q(published_at__lt=datetime(2, 1, 1, tzinfo=dt_timezone.utc)),
                    then=1,
                ),
                default=0,
                output_field=IntegerField(),
            )
        )
    if sort == "feed":
        items = items.annotate(
            sort_feed=Case(
                When(feed__title="", then=F("feed__url")),
                default=F("feed__title"),
                output_field=CharField(),
            )
        )
    if sort in {"idea", "category"}:
        items = items.annotate(
            sort_idea=Min(
                "feed__idea_feeds__idea__title",
                filter=Q(feed__idea_feeds__idea__in=accessible_ideas),
            ),
            sort_category=Min(
                "feed__idea_feeds__idea__category__name",
                filter=Q(feed__idea_feeds__idea__in=accessible_ideas),
            ),
        )
    items = items.order_by(*orderings[sort])

    matching_count = items.count()
    accessible_links = IdeaFeed.objects.filter(idea__in=accessible_ideas).select_related(
        "idea", "idea__category"
    ).order_by("idea__title", "idea_id")
    accessible_assessments = FeedItemAssessment.objects.filter(
        idea__in=accessible_ideas
    ).select_related("idea")
    items = items.prefetch_related(
        Prefetch("feed__idea_feeds", queryset=accessible_links, to_attr="accessible_links"),
        Prefetch("assessments", queryset=accessible_assessments, to_attr="accessible_assessments"),
    )
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    for item in page:
        for link in item.feed.accessible_links:
            link.idea.can_manage_feed_ingestion = profile.can_manage_status(
                link.idea.status
            )
    rows = [
        {
            "item": item,
            # Only surface http(s) links as clickable — a feed could carry a
            # javascript:/data: link, which would be stored XSS if rendered.
            "link": item.link if is_http_url(item.link) else "",
            "interest_stars": _stars(item.interest),
            "info_value_stars": _stars(item.info_value),
            "ideas": [link.idea for link in item.feed.accessible_links],
            "assessments": [
                {
                    "idea": assessment.idea,
                    "stars": _stars(assessment.usefulness),
                    "note": assessment.relevance_note,
                }
                for assessment in item.accessible_assessments
            ],
        }
        for item in page
    ]
    preserved = request.GET.copy()
    preserved.pop("page", None)
    querystring = f"?{preserved.urlencode()}" if preserved else ""
    pagination_suffix = f"&{preserved.urlencode()}" if preserved else ""
    return render(
        request,
        "ideas/feeds.html",
        {
            "rows": rows,
            "page": page,
            "unrated": unrated,
            "total_count": total_count,
            "matching_count": matching_count,
            "ideas": accessible_ideas.order_by("title", "id"),
            "categories": Category.objects.filter(ideas__in=accessible_ideas).distinct(),
            "selected_idea": selected_idea,
            "can_manage_selected_idea": bool(
                selected_idea and profile.can_manage_status(selected_idea.status)
            ),
            "selected_category": selected_category,
            "sort": sort,
            "querystring": querystring,
            "pagination_suffix": pagination_suffix,
            "tabs": _tabs(request.user.profile),
            "active": "feeds",
        },
    )


@login_required
@require_POST
def toggle_feed_ingestion_pause(request, pk):
    """Pause/resume future feed refreshes for one idea, preserving history."""
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {"ok": False, "error": "You cannot manage feeds for this Idea."},
                status=403,
            )
        return denied
    if idea.is_archived:
        error = "Archived ideas cannot resume feed ingestion."
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
    else:
        desired = request.POST.get("paused")
        if desired not in {"0", "1"}:
            error = "Choose whether feed ingestion should be paused."
            if request.headers.get("Accept") == "application/json":
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.error(request, error)
            back = request.POST.get("next", "")
            if not back.startswith("?"):
                back = ""
            return redirect(f"{reverse('ideas:feeds')}{back}")
        idea.feed_ingestion_paused = desired == "1"
        idea.save(update_fields=["feed_ingestion_paused", "updated_at"])
        state = "paused" if idea.feed_ingestion_paused else "resumed"
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {
                    "ok": True,
                    "idea_id": idea.pk,
                    "paused": idea.feed_ingestion_paused,
                    "state": state,
                }
            )
        messages.success(request, f"Feed ingestion {state} for “{idea.title}”.")
    back = request.POST.get("next", "")
    if not back.startswith("?"):
        back = ""
    return_to = request.POST.get("return_to", "")
    if return_to == reverse("ideas:detail", args=[idea.pk]):
        return redirect(return_to)
    return redirect(f"{reverse('ideas:feeds')}{back}")


@role_required("role_current", "role_tracking", "role_archive")
def rate_feed_item(request, pk):
    """Set one of the personal ratings (interest / info_value) from the feed page."""
    if request.method != "POST":
        return redirect("ideas:feeds")
    item = get_object_or_404(FeedItem, pk=pk)
    saved_field = None
    saved_value = None
    for field in ("interest", "info_value"):
        if field in request.POST:
            try:
                value = int(request.POST[field])
            except (TypeError, ValueError):
                break
            if 1 <= value <= 5:
                setattr(item, field, value)
                item.save(update_fields=[field])
                saved_field = field
                saved_value = value
            break
    if request.headers.get("Accept") == "application/json":
        if saved_field is None:
            return JsonResponse({"ok": False, "error": "Choose a rating from 1 to 5."}, status=400)
        return JsonResponse({"ok": True, "field": saved_field, "value": saved_value})
    # Return the user to the same page/filter, scrolled to the item they rated.
    back = request.POST.get("next", "")
    return redirect(f"{reverse('ideas:feeds')}{back}#item-{pk}")


@role_required()
def user_management(request):
    """Admin-only: a checkbox matrix of every user's roles, saved in one POST."""
    profiles = (
        Profile.objects.select_related("user")
        .annotate(idea_count=Count("user__ideas_created"))
        .order_by("user__email")
    )
    if request.method == "POST":
        for profile in profiles:
            for field in ROLE_FIELDS:
                setattr(profile, field, f"role-{profile.user_id}-{field}" in request.POST)
            profile.save()
        messages.success(request, "Roles updated.")
        return redirect("ideas:user_management")
    rows = [
        {
            "profile": profile,
            "cells": [(field, getattr(profile, field)) for field in ROLE_FIELDS],
        }
        for profile in profiles
    ]
    return render(
        request,
        "ideas/user_management.html",
        {
            "rows": rows,
            "role_columns": ROLE_COLUMNS,
            "tabs": _tabs(request.user.profile),
        },
    )


@role_required()
def idea_ownership(request):
    """Admin-only ownership overview and reassignment page."""
    users = get_user_model().objects.order_by("email", "username")
    ideas = Idea.objects.select_related("created_by", "category").order_by(
        "created_by__email", "title"
    )
    return render(
        request,
        "ideas/idea_ownership.html",
        {"ideas": ideas, "owners": users, "tabs": _tabs(request.user.profile)},
    )


@role_required()
def research_history(request):
    """Admin-only unified execution history, newest first."""
    selected_type = request.GET.get("type", "").strip()
    runs = LLMRun.objects.select_related(
        "trace__workflow_version__workflow", "model_configuration",
        "trace__subject_content_type",
    ).prefetch_related(
        "research_entries__idea",
        "feed_item_assessments",
        "relation_suggestions",
        "persona_reviews__votes",
        "relationship_council_reviews__votes",
        "tool_invocations",
        "deterministic_jobs",
        "artifact_versions",
        "outcome_events",
        "child_runs",
        "trace__events",
    )
    if selected_type and selected_type != "legacy_research":
        runs = runs.filter(trace__workflow_version__workflow__key=selected_type)
    elif selected_type == "legacy_research":
        runs = runs.none()

    idea_ids = set(
        runs.filter(trace__subject_content_type__model="idea")
        .values_list("trace__subject_object_id", flat=True)
    )
    ideas_by_id = Idea.objects.in_bulk(idea_ids)
    rows = []
    for run in runs:
        workflow = run.trace.workflow_version.workflow
        research_entry = next(iter(run.research_entries.all()), None)
        idea = research_entry.idea if research_entry else (
            ideas_by_id.get(run.trace.subject_object_id)
            if run.trace.subject_content_type_id
            and run.trace.subject_content_type.model == "idea"
            else None
        )
        scores = []
        if research_entry:
            scores.extend([
                f"Effort {research_entry.effort}/5",
                f"Quality {research_entry.quality}/5",
            ])
        assessments = list(run.feed_item_assessments.all())
        if assessments:
            values = [assessment.usefulness for assessment in assessments]
            distribution = ", ".join(
                f"{score}★:{values.count(score)}" for score in range(1, 6)
                if values.count(score)
            )
            scores.append(
                f"Usefulness {sum(values) / len(values):.1f}/5 across {len(values)} item(s) ({distribution})"
            )
        suggestions = list(run.relation_suggestions.all())
        if suggestions:
            scores.append(
                f"Relations: {len(suggestions)}; confidence "
                f"{sum(item.confidence for item in suggestions) / len(suggestions):.0%}; "
                f"similarity {sum(item.similarity for item in suggestions) / len(suggestions):.0%}"
            )
        persona_reviews = list(run.persona_reviews.all())
        if persona_reviews:
            decisions = [vote.decision for review in persona_reviews for vote in review.votes.all()]
            scores.append(
                "Persona votes: " + ", ".join(
                    f"{decision} {decisions.count(decision)}"
                    for decision in ("approve", "reject", "abstain")
                    if decisions.count(decision)
                )
            )
        relationship_reviews = list(run.relationship_council_reviews.all())
        if relationship_reviews:
            decisions = [vote.decision for review in relationship_reviews for vote in review.votes.all()]
            scores.append(
                "Council votes: " + ", ".join(
                    f"{decision} {decisions.count(decision)}"
                    for decision in ("accept", "reject", "abstain")
                    if decisions.count(decision)
                )
            )
        if run.schema_valid is not None:
            scores.append(f"Schema {'valid' if run.schema_valid else 'invalid'}")

        def instrumentation_value(value):
            if value is None or value == "":
                return "—"
            if isinstance(value, (dict, list)):
                return json.dumps(value, indent=2, sort_keys=True, default=str)
            return str(value)

        def instrumentation_fields(instance):
            return [
                {
                    "label": field.verbose_name,
                    "value": instrumentation_value(getattr(instance, field.attname)),
                }
                for field in instance._meta.concrete_fields
            ]

        instrumentation = [
            {"title": "LLM run", "records": [instrumentation_fields(run)]},
            {"title": "Execution trace", "records": [instrumentation_fields(run.trace)]},
            {
                "title": "Workflow version",
                "records": [instrumentation_fields(run.trace.workflow_version)],
            },
            {
                "title": "Model configuration",
                "records": [instrumentation_fields(run.model_configuration)],
            },
        ]
        related_instrumentation = (
            ("Execution events", run.trace.events.all()),
            ("Tool invocations", run.tool_invocations.all()),
            ("Deterministic jobs", run.deterministic_jobs.all()),
            ("Artifact versions", run.artifact_versions.all()),
            ("Outcome events", run.outcome_events.all()),
            ("Child runs", run.child_runs.all()),
        )
        for title, records in related_instrumentation:
            records = list(records)
            if records:
                instrumentation.append({
                    "title": title,
                    "records": [instrumentation_fields(record) for record in records],
                })
        rows.append({
            "occurred_at": run.created_at,
            "type": workflow.key,
            "type_label": workflow.name,
            "status": run.get_status_display(),
            "status_value": run.status,
            "idea": idea,
            "title": research_entry.topic if research_entry else run.trace.subject_label or workflow.name,
            "detail_url": (
                reverse("ideas:view_research_entry", args=[idea.pk, research_entry.pk])
                if idea and research_entry else ""
            ),
            "model": str(run.model_configuration),
            "tokens": {
                "total": run.total_tokens,
                "input": run.input_tokens,
                "output": run.output_tokens,
                "cached": run.cached_tokens,
                "reasoning": run.reasoning_tokens,
                "measurement": run.get_measurement_status_display(),
            },
            "scores": scores,
            "instrumentation": instrumentation,
        })

    if not selected_type or selected_type == "legacy_research":
        for entry in ResearchEntry.objects.filter(produced_by_run=None).select_related("idea", "model"):
            rows.append({
                "occurred_at": entry.occurred_at,
                "type": "legacy_research",
                "type_label": "Legacy research",
                "status": "Succeeded",
                "status_value": "succeeded",
                "idea": entry.idea,
                "title": entry.topic or "Untitled work",
                "detail_url": reverse("ideas:view_research_entry", args=[entry.idea_id, entry.pk]),
                "model": entry.execution_model or entry.model.name,
                "tokens": {
                    "total": entry.tokens_used,
                    "input": None,
                    "output": None,
                    "cached": None,
                    "reasoning": None,
                    "measurement": "Legacy estimate" if entry.tokens_used is not None else "Unavailable",
                },
                "scores": [f"Effort {entry.effort}/5", f"Quality {entry.quality}/5"],
                "instrumentation": [{
                    "title": "Legacy research record",
                    "records": [[
                        {
                            "label": field.verbose_name,
                            "value": (
                                json.dumps(value, indent=2, sort_keys=True, default=str)
                                if isinstance(value, (dict, list))
                                else "—" if value is None or value == "" else str(value)
                            ),
                        }
                        for field in entry._meta.concrete_fields
                        for value in [getattr(entry, field.attname)]
                    ]],
                }],
            })

    rows.sort(key=lambda row: row["occurred_at"], reverse=True)
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    type_options = list(
        WorkflowDefinition.objects.order_by("name").values("key", "name")
    )
    if ResearchEntry.objects.filter(produced_by_run=None).exists():
        type_options.append({"key": "legacy_research", "name": "Legacy research"})
    return render(
        request,
        "ideas/research_history.html",
        {
            "page": page,
            "type_options": type_options,
            "selected_type": selected_type,
            "tabs": _tabs(request.user.profile),
        },
    )


@role_required()
def research_queue(request):
    """Preview the default work list for the next research_all.sh invocation."""
    ideas = list(
        Idea.objects.prefetch_related(
            "resources", "research_entries", "idea_personas__persona",
            "persona_reviews",
        )
    )
    now = timezone.now()

    def selection_detail(idea):
        persona_due = False
        if (
            idea.persona_review_enabled
            and idea.status != Status.ARCHIVED
            and not idea.persona_review_paused
            and any(
                assignment.active
                and assignment.required
                and assignment.persona.is_active
                for assignment in idea.idea_personas.all()
            )
        ):
            baseline = max(
                value
                for value in (
                    idea.last_meaningful_progress_at,
                    idea.last_persona_review_at,
                )
                if value is not None
            )
            persona_due = baseline <= now - timedelta(days=idea.persona_stall_days)
        return {
            "id": idea.pk,
            "title": idea.title,
            "status": idea.status,
            "summary_requested_at": (
                idea.summary_requested_at.isoformat()
                if idea.summary_requested_at else None
            ),
            "next_action": idea.next_action,
            "repo": idea.repo,
            "agent_runs_since_feedback": idea.agent_runs_since_feedback,
            "is_paused": idea.is_paused,
            "repeat_task": {
                "enabled": idea.repeat_enabled,
                "paused": idea.repeat_paused,
                "is_due": idea.repeat_is_due,
            },
            "persona_review": {
                "is_due": persona_due,
                "last_meaningful_progress_at": (
                    idea.last_meaningful_progress_at.isoformat()
                ),
                "recent_reviews": [
                    {
                        "status": review.status,
                        "created_at": review.created_at.isoformat(),
                    }
                    for review in list(idea.persona_reviews.all())[:10]
                ],
            },
            "research_entries": [
                {
                    "topic": entry.topic,
                    "occurred_at": entry.occurred_at.isoformat(),
                    "created_at": entry.created_at.isoformat(),
                }
                for entry in idea.research_entries.all()
            ],
            "resources": [
                {"url": resource.url, "label": resource.label}
                for resource in idea.resources.all()
            ],
        }

    details = {idea.pk: selection_detail(idea) for idea in ideas}
    ideas_by_id = {idea.pk: idea for idea in ideas}
    listing = [
        {"id": idea.pk, "status": idea.status, "title": idea.title}
        for idea in ideas
    ]
    selected, state = select_work(listing, details)
    mode_labels = {
        "research": "Research",
        "review": "Review & synthesis",
        "execute": "Execute next action",
        "critique": "Critical PR review",
        "persona": "Persona council review",
        "repeat": "Repeat task",
        "summary": "Artifact summary",
    }
    rows = [
        {
            "idea": ideas_by_id[idea_id],
            "mode": mode,
            "work_title": mode_labels.get(mode, mode.replace("_", " ").title()),
        }
        for idea_id, mode in selected
    ]
    if state["reason"] == "idle":
        rows.append(
            {
                "idea": None,
                "mode": "reflection",
                "work_title": "Portfolio reflection",
            }
        )
    return render(
        request,
        "ideas/research_queue.html",
        {
            "rows": rows,
            "state": state,
            "job_count": len(rows),
            "tabs": _tabs(request.user.profile),
        },
    )


@require_POST
@role_required()
def reassign_idea(request, pk):
    idea = get_object_or_404(Idea, pk=pk)
    owner = get_object_or_404(get_user_model(), pk=request.POST.get("created_by"))
    idea.created_by = owner
    idea.save(update_fields=["created_by", "updated_at"])
    messages.success(request, f"Reassigned “{idea.title}” to {owner.email or owner.username}.")
    return redirect("ideas:idea_ownership")


def _save_help_message(request, conversation_user):
    body = request.POST.get("body", "").strip()
    if not body:
        messages.error(request, "Enter a message before sending.")
        return False
    if len(body) > 5000:
        messages.error(request, "Messages must be 5,000 characters or fewer.")
        return False
    HelpMessage.objects.create(
        user=conversation_user,
        sender=request.user,
        body=body,
        admin_response=request.user.profile.role_admin and request.user.pk != conversation_user.pk,
    )
    messages.success(request, "Message sent.")
    return True


@login_required
def help_conversation(request):
    """The signed-in user's private, persistent conversation with admins."""
    if request.method == "POST":
        _save_help_message(request, request.user)
        return redirect("ideas:help")
    conversation_paginator = Paginator(
        HelpMessage.objects.filter(user=request.user).select_related("sender"), 100
    )
    messages_page = conversation_paginator.get_page(
        request.GET.get("page") or conversation_paginator.num_pages
    )
    return render(request, "ideas/help.html", {"conversation": messages_page, "active": "help"})


@role_required()
def help_admin(request, user_id=None):
    """Admin inbox, with one conversation per user and replies in context."""
    User = get_user_model()
    latest_messages = HelpMessage.objects.filter(user_id=OuterRef("pk")).order_by("-created_at", "-pk")
    conversation_users = (
        User.objects.filter(help_messages__isnull=False)
        .annotate(
            latest_help_at=Subquery(latest_messages.values("created_at")[:1], output_field=DateTimeField()),
            latest_is_admin=Subquery(latest_messages.values("admin_response")[:1], output_field=BooleanField()),
        )
        .order_by("latest_is_admin", "-latest_help_at", "email", "username")
        .distinct()
    )
    conversation_page = Paginator(conversation_users, 50).get_page(request.GET.get("inbox_page"))

    selected_user = get_object_or_404(User, pk=user_id) if user_id is not None else None
    if request.method == "POST":
        if selected_user is None:
            raise Http404
        _save_help_message(request, selected_user)
        return redirect("ideas:help_admin_conversation", user_id=selected_user.pk)
    conversation = HelpMessage.objects.filter(user=selected_user).select_related("sender") if selected_user else HelpMessage.objects.none()
    conversation_paginator = Paginator(conversation, 100)
    messages_page = conversation_paginator.get_page(
        request.GET.get("page") or conversation_paginator.num_pages
    )
    return render(request, "ideas/help_admin.html", {"conversation_users": conversation_page, "selected_user": selected_user, "conversation": messages_page})
