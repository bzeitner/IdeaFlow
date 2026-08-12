from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, F, IntegerField, Q, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .feeds import is_http_url, recent_articles
from .forms import IdeaForm, ResearchEntryForm, ResourceFormSet
from .models import Category, FeedItem, Idea, Profile, Stage, Status

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
    return [
        {"value": value, "label": label, "route": route, "count": counts.get(value, 0)}
        for value, label, route in TAB_SPEC
        if profile.can_manage_status(value)
    ]


def home(request):
    """Public marketing page for anonymous visitors; for anyone signed in, the
    home page lists the public projects (viewable by all, editable by none from
    here). Tab links in the top bar take role-holders to their workspace."""
    if not request.user.is_authenticated:
        return render(request, "ideas/landing.html")
    profile = request.user.profile
    public = Idea.objects.filter(is_public=True).prefetch_related("resources")
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
    ideas = Idea.objects.filter(status=status).prefetch_related("resources")
    return render(
        request,
        template,
        {
            "ideas": ideas,
            "tabs": _tabs(request.user.profile),
            "active": status,
            "can_manage": True,
        },
    )


@role_required("role_current")
def current(request):
    return _tab_view(request, Status.CURRENT, "ideas/current.html")


@role_required("role_tracking")
def tracking(request):
    ideas = Idea.objects.filter(status=Status.TRACKING).prefetch_related("resources")
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    stage = request.GET.get("stage", "")
    attention = request.GET.get("attention", "")
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
    if attention == "paused":
        ideas = ideas.filter(agent_runs_since_feedback__gte=3)
    elif attention == "no-next-action":
        ideas = ideas.filter(next_action="")

    sort = request.GET.get("sort", "family")
    orderings = {
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
        sort = "family"
    if sort == "family":
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
        Idea.objects.select_related("parent").prefetch_related(
            "resources", "research_entries", "research_entries__model", "children"
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
        messages.success(
            request, f"Moved “{idea.title}” to {idea.get_status_display()}."
        )
    return redirect(request.POST.get("next") or "ideas:home")


@login_required
def set_next_action(request, pk):
    """Set the idea's next action from its detail page."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    idea.next_action = request.POST.get("next_action", "").strip()
    # A human next action is feedback — clear the pause counter.
    idea.agent_runs_since_feedback = 0
    idea.save(update_fields=["next_action", "agent_runs_since_feedback", "updated_at"])
    messages.success(request, "Next action saved.")
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
            idea.next_action = value
            idea.agent_runs_since_feedback = 0
            idea.save(update_fields=["next_action", "agent_runs_since_feedback", "updated_at"])
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
    profiles = Profile.objects.select_related("user").order_by("user__email")
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
