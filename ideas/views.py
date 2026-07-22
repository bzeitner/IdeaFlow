from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from .forms import IdeaForm, ResearchEntryFormSet, ResourceFormSet
from .models import Idea, Status

TAB_SPEC = [
    (Status.CURRENT, "Current", "ideas:current"),
    (Status.TRACKING, "Tracking", "ideas:tracking"),
    (Status.ARCHIVED, "Archive", "ideas:archive"),
]


def _tabs():
    """Tab definitions with live counts, ready for the template to iterate."""
    counts = dict(
        Idea.objects.values_list("status").annotate(n=Count("id")).values_list(
            "status", "n"
        )
    )
    return [
        {"value": value, "label": label, "route": route, "count": counts.get(value, 0)}
        for value, label, route in TAB_SPEC
    ]


def _tab_view(request, status, template):
    ideas = Idea.objects.filter(status=status).prefetch_related("resources")
    return render(
        request,
        template,
        {"ideas": ideas, "tabs": _tabs(), "active": status},
    )


def current(request):
    return _tab_view(request, Status.CURRENT, "ideas/current.html")


def tracking(request):
    return _tab_view(request, Status.TRACKING, "ideas/tracking.html")


def archive(request):
    return _tab_view(request, Status.ARCHIVED, "ideas/archive.html")


def detail(request, pk):
    idea = get_object_or_404(
        Idea.objects.prefetch_related(
            "resources", "research_entries", "research_entries__model"
        ),
        pk=pk,
    )
    return render(
        request,
        "ideas/detail.html",
        {"idea": idea, "tabs": _tabs(), "active": idea.status},
    )


def idea_form(request, pk=None):
    idea = get_object_or_404(Idea, pk=pk) if pk else None
    if request.method == "POST":
        form = IdeaForm(request.POST, instance=idea)
        formset = ResourceFormSet(request.POST, instance=idea)
        research_formset = ResearchEntryFormSet(request.POST, instance=idea)
        if form.is_valid() and formset.is_valid() and research_formset.is_valid():
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
            "tabs": _tabs(),
            "active": idea.status if idea else None,
        },
    )


def set_status(request, pk, status):
    """Move an idea between tabs from a list-row button."""
    if request.method != "POST":
        return redirect("ideas:current")
    idea = get_object_or_404(Idea, pk=pk)
    if status in {s.value for s in Status}:
        idea.status = status
        idea.save(update_fields=["status", "updated_at"])
        messages.success(
            request, f"Moved “{idea.title}” to {idea.get_status_display()}."
        )
    return redirect(request.POST.get("next") or "ideas:current")
