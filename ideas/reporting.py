"""Shared write-path: record a work-effort report against an idea.

An agent that has read an idea and acted on it (researched, spun up a repo,
wrote code) reports back through record_effort(), which lands a ResearchEntry
and, optionally, a result link and a stage/status move. Both the `log_effort`
command and the `/api/.../effort/` view call it, so the two paths behave
identically.
"""

import re
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from .models import AIModel, ResearchEntry, Resource, Stage, Status

VALID_STATUS = {s.value for s in Status}


def _github_pr_url(value):
    parts = urlparse(value or "")
    return bool(
        parts.scheme == "https"
        and parts.netloc.lower() == "github.com"
        and re.fullmatch(r"/[^/]+/[^/]+/pull/\d+/?", parts.path)
    )


def _question_key(question):
    """Normalize a question enough to recognize repeated human prompts."""
    return " ".join(str(question).split()).casefold()


def new_open_questions(idea, questions, *, exclude_entry=None):
    """Return unique questions that are not already open for this idea."""
    entries = ResearchEntry.objects.filter(idea=idea)
    if exclude_entry is not None:
        entries = entries.exclude(pk=exclude_entry.pk)

    already_open = set()
    for open_questions, question_answers in entries.values_list(
        "open_questions", "question_answers"
    ):
        answers = question_answers if isinstance(question_answers, dict) else {}
        for index, question in enumerate(open_questions if isinstance(open_questions, list) else []):
            if not str(answers.get(str(index), "")).strip():
                already_open.add(_question_key(question))

    result = []
    seen = set(already_open)
    for raw_question in questions:
        question = " ".join(str(raw_question).split())
        key = _question_key(question)
        if question and key not in seen:
            result.append(question)
            seen.add(key)
    return result


def extract_open_questions(context):
    """Extract bullet/numbered items from a Markdown Open Questions section."""
    questions = []
    in_section = False
    for line in (context or "").splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s+open questions?\s*:?\s*$", line, re.I):
            in_section = True
            continue
        if in_section and re.match(r"^\s{0,3}#{1,6}\s+", line):
            break
        if not in_section:
            continue
        match = re.match(r"^\s*(?:[-*+] |\d+[.)] )(.*\S)\s*$", line)
        if match:
            question = match.group(1).strip()
            if question.lower().rstrip(".") not in {"none", "n/a"}:
                questions.append(question)
    return questions


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
    execution_provider="",
    execution_model="",
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
    queued_next_actions=None,
    exec_summary=None,
    open_questions=None,
    produced_by_run=None,
):
    """Create a ResearchEntry for `idea`, plus an optional result Resource and an
    optional stage/status move / next-action update. Returns
    (research_entry, resource_or_None).

    Raises ValueError for bad input and LookupError for an unknown model/stage;
    the transaction means a late failure (e.g. bad status) rolls the entry back.
    """
    if not topic:
        raise ValueError("topic is required.")
    if queued_next_actions is not None and not isinstance(
        queued_next_actions, (list, tuple)
    ):
        raise ValueError("queued_next_actions must be a list.")
    if open_questions is not None and not isinstance(open_questions, (list, tuple)):
        raise ValueError("open_questions must be a list.")
    clean_questions = [str(question).strip() for question in (open_questions or []) if str(question).strip()]
    if not clean_questions:
        clean_questions = extract_open_questions(context)
    clean_questions = new_open_questions(idea, clean_questions)
    entry = ResearchEntry.objects.create(
        idea=idea,
        produced_by_run=produced_by_run,
        topic=topic,
        focus=focus or "",
        context=context or "",
        open_questions=clean_questions,
        occurred_at=occurred_at or timezone.now(),
        effort=_star(effort, "effort"),
        quality=_star(quality, "quality"),
        model=resolve_ai_model(model),
        execution_provider=(execution_provider or "")[:32],
        execution_model=(execution_model or "")[:100],
        tokens_used=tokens_used,
    )
    resource = None
    if resource_url:
        resource = Resource.objects.create(
            idea=idea, label=resource_label or "", url=resource_url
        )
    followup_actions = list(queued_next_actions or [])
    if _github_pr_url(resource_url):
        review_action = f"Critical PR review: {resource_url}"
        if next_action and next_action.strip() != review_action:
            followup_actions.insert(0, next_action)
        next_action = review_action
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
        idea.replace_active_next_action(next_action)
        changed.extend(["next_action", "next_actions"])
    for queued_action in followup_actions:
        if idea.enqueue_next_action(queued_action) and "next_actions" not in changed:
            changed.extend(["next_action", "next_actions"])
    if exec_summary is not None:
        idea.exec_summary = exec_summary
        changed.append("exec_summary")
    idea.last_meaningful_progress_at = timezone.now()
    changed.append("last_meaningful_progress_at")
    # Every effort is an agent run; count it toward the pause-for-feedback limit.
    idea.agent_runs_since_feedback = (idea.agent_runs_since_feedback or 0) + 1
    changed.append("agent_runs_since_feedback")
    changed.append("updated_at")
    idea.save(update_fields=changed)
    return entry, resource
