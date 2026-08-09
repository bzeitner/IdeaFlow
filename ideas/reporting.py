"""Shared write-path: record a work-effort report against an idea.

An agent that has read an idea and acted on it (researched, spun up a repo,
wrote code) reports back through record_effort(), which lands a ResearchEntry
and, optionally, a result link and a stage/status move. Both the `log_effort`
command and the `/api/.../effort/` view call it, so the two paths behave
identically.
"""

from django.db import transaction
from django.utils import timezone

from .models import AIModel, ResearchEntry, Resource, Stage, Status

VALID_STATUS = {s.value for s in Status}


def resolve_ai_model(value):
    """An AIModel from an instance, slug, or (case-insensitive) name."""
    if isinstance(value, AIModel):
        return value
    if not value:
        raise ValueError("A model is required (pass an AIModel slug or name).")
    model = (
        AIModel.objects.filter(slug=value).first()
        or AIModel.objects.filter(name__iexact=value).first()
    )
    if model is None:
        known = ", ".join(AIModel.objects.values_list("slug", flat=True))
        raise LookupError(f"No AI model matches {value!r}. Known slugs: {known}.")
    return model


def resolve_stage(value):
    """A Stage from an instance, slug, or name; None/'' means 'no change asked'."""
    if value in (None, ""):
        return None
    if isinstance(value, Stage):
        return value
    stage = (
        Stage.objects.filter(slug=value).first()
        or Stage.objects.filter(name__iexact=value).first()
    )
    if stage is None:
        known = ", ".join(Stage.objects.values_list("slug", flat=True)) or "(none defined)"
        raise LookupError(f"No stage matches {value!r}. Known slugs: {known}.")
    return stage


def _star(value, field):
    value = int(value)
    if not 1 <= value <= 5:
        raise ValueError(f"{field} must be between 1 and 5, got {value}.")
    return value


@transaction.atomic
def record_effort(
    idea,
    *,
    topic,
    model="other",
    context="",
    focus="",
    effort=3,
    quality=3,
    tokens_used=None,
    occurred_at=None,
    resource_url=None,
    resource_label="",
    stage=None,
    status=None,
    next_action=None,
):
    """Create a ResearchEntry for `idea`, plus an optional result Resource and an
    optional stage/status move / next-action update. Returns
    (research_entry, resource_or_None).

    Raises ValueError for bad input and LookupError for an unknown model/stage;
    the transaction means a late failure (e.g. bad status) rolls the entry back.
    """
    if not topic:
        raise ValueError("topic is required.")
    entry = ResearchEntry.objects.create(
        idea=idea,
        topic=topic,
        focus=focus or "",
        context=context or "",
        occurred_at=occurred_at or timezone.now(),
        effort=_star(effort, "effort"),
        quality=_star(quality, "quality"),
        model=resolve_ai_model(model),
        tokens_used=tokens_used,
    )
    resource = None
    if resource_url:
        resource = Resource.objects.create(
            idea=idea, label=resource_label or "", url=resource_url
        )
    changed = []
    if stage is not None:
        idea.stage = resolve_stage(stage)
        changed.append("stage")
    if status is not None:
        if status not in VALID_STATUS:
            raise ValueError(f"status must be one of {sorted(VALID_STATUS)}.")
        idea.status = status
        changed.append("status")
    if next_action is not None:
        idea.next_action = next_action
        changed.append("next_action")
    # Every effort is an agent run; count it toward the pause-for-feedback limit.
    idea.agent_runs_since_feedback = (idea.agent_runs_since_feedback or 0) + 1
    changed.append("agent_runs_since_feedback")
    changed.append("updated_at")
    idea.save(update_fields=changed)
    return entry, resource
