from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .feeds import is_http_url
from .forms import IdeaForm, ResearchEntryFormSet, ResourceFormSet
from .models import FeedItem, Idea, Profile, Status

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
    """Public landing page, or a dispatch to the first tab this user can see."""
    if not request.user.is_authenticated:
        return render(request, "ideas/landing.html")
    profile = request.user.profile
    for status, _label, route in TAB_SPEC:
        if profile.can_manage_status(status):
            return redirect(route)
    return render(request, "ideas/no_access.html")


def _tab_view(request, status, template):
    ideas = Idea.objects.filter(status=status).prefetch_related("resources")
    return render(
        request,
        template,
        {"ideas": ideas, "tabs": _tabs(request.user.profile), "active": status},
    )


@role_required("role_current")
def current(request):
    return _tab_view(request, Status.CURRENT, "ideas/current.html")


@role_required("role_tracking")
def tracking(request):
    return _tab_view(request, Status.TRACKING, "ideas/tracking.html")


@role_required("role_archive")
def archive(request):
    return _tab_view(request, Status.ARCHIVED, "ideas/archive.html")


@login_required
def detail(request, pk):
    idea = get_object_or_404(
        Idea.objects.prefetch_related(
            "resources", "research_entries", "research_entries__model"
        ),
        pk=pk,
    )
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    return render(
        request,
        "ideas/detail.html",
        {"idea": idea, "tabs": _tabs(request.user.profile), "active": idea.status},
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
        research_formset = ResearchEntryFormSet(request.POST, instance=idea)
        if form.is_valid() and formset.is_valid() and research_formset.is_valid():
            if idea is None:
                # role_add_ideas only grants creating ideas, not a target tab —
                # force new ideas into Current regardless of what "status" was
                # submitted, so a role_add_ideas-only user can't write directly
                # into a tab (e.g. archived) they have no role to manage.
                form.instance.status = Status.CURRENT
            saved = form.save()
            formset.instance = saved
            formset.save()
            research_formset.instance = saved
            research_formset.save()
            messages.success(request, f"Saved “{saved.title}”.")
            return redirect(saved)
    else:
        form = IdeaForm(instance=idea)
        formset = ResourceFormSet(instance=idea)
        research_formset = ResearchEntryFormSet(instance=idea)
    return render(
        request,
        "ideas/idea_form.html",
        {
            "form": form,
            "formset": formset,
            "research_formset": research_formset,
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
    """Set the idea's next action from its detail page (once research exists)."""
    if request.method != "POST":
        return redirect("ideas:detail", pk=pk)
    idea = get_object_or_404(Idea, pk=pk)
    denied = _require_status_role(request, idea.status)
    if denied:
        return denied
    idea.next_action = request.POST.get("next_action", "").strip()
    idea.save(update_fields=["next_action", "updated_at"])
    messages.success(request, "Next action saved.")
    return redirect("ideas:detail", pk=pk)


@role_required("role_current", "role_tracking", "role_archive")
def feeds(request):
    """Read the shared feed items and rate them (interest + info value)."""
    items = FeedItem.objects.select_related("feed", "summary_model")
    unrated = bool(request.GET.get("unrated"))
    if unrated:
        items = items.filter(interest__isnull=True)
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    rows = [
        {
            "item": item,
            # Only surface http(s) links as clickable — a feed could carry a
            # javascript:/data: link, which would be stored XSS if rendered.
            "link": item.link if is_http_url(item.link) else "",
            "interest_stars": _stars(item.interest),
            "info_value_stars": _stars(item.info_value),
            "usefulness_stars": _stars(item.usefulness),
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
