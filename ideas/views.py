from functools import wraps
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Q, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .feeds import is_http_url, recent_articles
from .forms import IdeaForm, IdeaRelationForm, ResearchEntryForm, ResourceFormSet
from .graph.projection import graph_projection
from .graph.capabilities import consume_capability, issue_capability
from .graph.export import graphml_export
from .models import AGENT_RUNS_BEFORE_FEEDBACK, Category, FeedItem, GraphAccessCapability, Idea, IdeaRelation, IdeaRelationSuggestion, Profile, RelationProvenance, RelationType, RepeatResult, RepeatResultStatus, ResearchEntry, Stage, Status, SuggestionStatus, WeeklySummary
from .presentation import render_research_context
from .weekly_metrics import metric_comparison_rows

STAR_RANGE = [1, 2, 3, 4, 5]


def _stars(value):
    """[(n, filled), ...] for rendering a 1-5 star row from an optional value."""
    filled_to = value or 0
    return [(n, n <= filled_to) for n in STAR_RANGE]

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
    section_specs = (
        ("Tasks by type", "tasks_by_type"),
        ("Pull requests", "prs"),
        ("Tokens by task", "tokens_by_task"),
        ("Tokens by model", "tokens_by_model"),
        ("Tokens by category", "tokens_by_category"),
    )
    for index, summary in enumerate(summaries):
        previous = summaries[index + 1].metrics if index + 1 < len(summaries) else {}
        summary.metric_sections = [
            {
                "title": title,
                "rows": metric_comparison_rows(
                    (summary.metrics or {}).get(key), (previous or {}).get(key)
                ),
            }
            for title, key in section_specs
        ]
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
            "suggestions": IdeaRelationSuggestion.objects.filter(status=SuggestionStatus.PENDING).select_related("analyzed_idea", "source", "target"),
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


def home(request):
    """Public marketing page for anonymous visitors; for anyone signed in, the
    home page lists the public projects (viewable by all, editable by none from
    here). Tab links in the top bar take role-holders to their workspace."""
    if not request.user.is_authenticated:
        return render(request, "ideas/landing.html")
    profile = request.user.profile
    public = Idea.objects.filter(is_public=True).select_related("created_by").prefetch_related("resources")
    return render(
        request,
        "ideas/home.html",
        {
            "ideas": public,
            "tabs": _tabs(profile),
            "can_manage": False,
            "has_any_role": profile.has_role(
                "role_current", "role_tracking", "role_archive", "role_add_ideas"
            ),
        },
    )


def _tab_view(request, status, template):
    owner_filter = request.GET.get("owner", "")
    ideas = Idea.objects.filter(status=status).select_related("created_by").prefetch_related("resources")
    if owner_filter == "mine":
        ideas = ideas.filter(created_by=request.user)
    return render(
        request,
        template,
        {
            "ideas": ideas,
            "tabs": _tabs(request.user.profile),
            "active": status,
            "can_manage": True,
            "owner_filter": owner_filter,
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
    owner_filter = request.GET.get("owner", "")
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
    if attention == "paused":
        ideas = ideas.filter(
            agent_runs_since_feedback__gte=AGENT_RUNS_BEFORE_FEEDBACK
        )
    elif attention == "no-next-action":
        ideas = ideas.filter(next_action="")

    ideas = ideas.annotate(
        tracking_child_count=Count(
            "children",
            filter=Q(children__status=Status.TRACKING),
            distinct=True,
        )
    )

    sort = request.GET.get("sort", "questions")
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
    return render(
        request,
        "ideas/tracking.html",
        {
            "ideas": ideas,
            "tabs": _tabs(request.user.profile),
            "active": Status.TRACKING,
            "can_manage": True,
            "categories": Category.objects.filter(is_active=True),
            "stages": Stage.objects.filter(is_active=True),
            "filters": {
                "q": query,
                "category": category,
                "stage": stage,
                "attention": attention,
                "owner": owner_filter,
                "sort": sort,
            },
        },
    )


@role_required("role_archive")
def archive(request):
    return _tab_view(request, Status.ARCHIVED, "ideas/archive.html")


@login_required
def detail(request, pk):
    idea = get_object_or_404(
        Idea.objects.select_related("parent", "created_by").prefetch_related(
            "resources", "research_entries", "research_entries__model", "children", "repeat_results"
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
        entry.rendered_context = render_research_context(
            entry.context, research_entry_ids
        )
    return render(
        request,
        "ideas/detail.html",
        {
            "idea": idea,
            "tabs": _tabs(request.user.profile),
            "active": idea.status,
            "can_manage": can_manage,
            "idea_feeds": idea_feeds,
            "articles": recent_articles(idea),
            "repeat_result_statuses": RepeatResultStatus.choices,
            "research_with_open_questions": research_with_open_questions,
            "research_entries": research_entries,
            "suggested_children": [
                line for line in idea.suggested_children.splitlines() if line.strip()
            ],
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
        form = IdeaForm(request.POST, instance=idea)
        formset = ResourceFormSet(request.POST, instance=idea)
        if form.is_valid() and formset.is_valid():
            if idea is None:
                # role_add_ideas only grants creating ideas, not a target tab —
                # force new ideas into Current regardless of what "status" was
                # submitted, so a role_add_ideas-only user can't write directly
                # into a tab (e.g. archived) they have no role to manage.
                form.instance.status = Status.CURRENT
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
        form = IdeaForm(instance=idea, initial=initial or None)
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
    idea.save(
        update_fields=[
            "next_action",
            "next_actions",
            "agent_runs_since_feedback",
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
    idea.save(
        update_fields=[
            "next_action",
            "next_actions",
            "agent_runs_since_feedback",
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
    idea.save(update_fields=["agent_runs_since_feedback", "updated_at"])
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
        if field == "rank" and value.isdigit():
            idea.rank = int(value)
            idea.save(update_fields=["rank", "updated_at"])
        elif field == "stage":
            stage = Stage.objects.filter(pk=value, is_active=True).first() if value else None
            if value == "" or stage:
                idea.stage = stage
                idea.save(update_fields=["stage", "updated_at"])
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
        messages.success(request, f"Updated “{idea.title}”.")
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


@role_required("role_current", "role_tracking", "role_archive")
def feeds(request):
    """Read the shared feed items and rate them (interest + info value)."""
    items = FeedItem.objects.select_related("feed", "summary_model").prefetch_related(
        "assessments__idea"
    )
    unrated = bool(request.GET.get("unrated"))
    if unrated:
        # Summarized only: an unsummarized item has nothing to read but its
        # title, and the summarized rows are otherwise buried thousands deep.
        items = items.filter(interest__isnull=True, summarized_at__isnull=False)
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    rows = [
        {
            "item": item,
            # Only surface http(s) links as clickable — a feed could carry a
            # javascript:/data: link, which would be stored XSS if rendered.
            "link": item.link if is_http_url(item.link) else "",
            "interest_stars": _stars(item.interest),
            "info_value_stars": _stars(item.info_value),
            "assessments": [
                {
                    "idea": assessment.idea,
                    "stars": _stars(assessment.usefulness),
                    "note": assessment.relevance_note,
                }
                for assessment in item.assessments.all()
            ],
        }
        for item in page
    ]
    querystring = f"?{request.GET.urlencode()}" if request.GET else ""
    return render(
        request,
        "ideas/feeds.html",
        {
            "rows": rows,
            "page": page,
            "unrated": unrated,
            "querystring": querystring,
            "tabs": _tabs(request.user.profile),
        },
    )


@role_required("role_current", "role_tracking", "role_archive")
def rate_feed_item(request, pk):
    """Set one of the personal ratings (interest / info_value) from the feed page."""
    if request.method != "POST":
        return redirect("ideas:feeds")
    item = get_object_or_404(FeedItem, pk=pk)
    for field in ("interest", "info_value"):
        if field in request.POST:
            try:
                value = int(request.POST[field])
            except (TypeError, ValueError):
                break
            if 1 <= value <= 5:
                setattr(item, field, value)
                item.save(update_fields=[field])
            break
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


@require_POST
@role_required()
def reassign_idea(request, pk):
    idea = get_object_or_404(Idea, pk=pk)
    owner = get_object_or_404(get_user_model(), pk=request.POST.get("created_by"))
    idea.created_by = owner
    idea.save(update_fields=["created_by", "updated_at"])
    messages.success(request, f"Reassigned “{idea.title}” to {owner.email or owner.username}.")
    return redirect("ideas:idea_ownership")
