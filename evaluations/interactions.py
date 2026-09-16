"""Human-only services; projected output hashes are distinct from raw run hashes."""
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from executions.services import canonical_hash
from .models import EvaluationExposure, HumanFeedback, FeedbackOutcomeLink


def enabled():
    return settings.IDEAFLOW_EXECUTION_FLAGS.get("feedback", False)


def require_enabled():
    if not enabled():
        raise PermissionDenied("Feedback is disabled.")


def projected_hash(kind, obj):
    if kind == "research":
        content = {"topic": obj.topic, "focus": obj.focus, "context": obj.context}
    else:
        content = {"title": obj.title, "content": obj.content,
                   "period_start": str(obj.period_start), "period_end": str(obj.period_end)}
    return canonical_hash(content)


def target_for(user, kind, pk, *, lock=False, edit=False):
    from ideas.models import ResearchEntry, WeeklySummary
    if not user.is_authenticated or not user.pk:
        raise PermissionDenied
    # Re-read roles/identity; never accept an actor or permissions from POST data.
    actor = get_user_model().objects.get(pk=user.pk)
    if not actor.is_active:
        raise PermissionDenied
    profile = actor.profile
    if kind not in {"research", "weekly_summary"}:
        raise ValidationError("Unsupported output kind.")
    model = ResearchEntry if kind == "research" else WeeklySummary
    queryset = model.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    obj = queryset.get(pk=pk)
    if kind == "research":
        idea = obj.idea
        owner = idea.created_by_id == actor.pk
        admin = profile.role_admin or actor.is_superuser
        can_read = admin or (profile.can_manage_status(idea.status) and owner) or idea.is_public
        if not can_read or (edit and not (admin or (owner and profile.can_manage_status(idea.status)))):
            raise PermissionDenied
        source = "research_view"
    else:
        if edit or not profile.has_role("role_weekly_summary"):
            raise PermissionDenied
        source = "weekly_summary_view"
    run = obj.produced_by_run
    if run and (run.status != "succeeded" or not run.output_hash):
        run = None  # historical projection with unavailable successful provenance
    if run and kind == "research":
        trace = run.trace
        subject_type = trace.subject_content_type
        if (str(trace.subject_object_id) != str(obj.idea_id) or not subject_type or
                subject_type.app_label != "ideas" or subject_type.model != "idea"):
            run = None  # Do not expose an unrelated run through a bad projection link.
    if run and kind == "weekly_summary" and run.trace.workflow_version.workflow.key != "weekly_summary":
        run = None
    identity = {"target_kind": kind, "target_id": obj.pk, "output_hash": projected_hash(kind, obj),
                "producing_run_id": str(run.pk) if run else None,
                "run_output_hash": run.output_hash if run else ""}
    return obj, identity, source


def actor_values(user):
    return {"actor_user_id": user.pk, "actor_label": f"user:{user.pk}"}


def check_identity(actual, expected):
    if actual != expected:
        raise ValidationError("This output changed. Reload it before submitting feedback.")


def save_once(model, **values):
    candidate = model(**values)
    digest = candidate.fingerprint()
    existing = model.objects.filter(idempotency_key=values["idempotency_key"]).first()
    if existing:
        if existing.content_hash != digest:
            raise ValidationError("This request key already identifies different content.")
        return existing, False
    candidate.save()
    return candidate, True


@transaction.atomic
def record_exposure(user, expected, *, view_session):
    require_enabled()
    obj, identity, source = target_for(user, expected["target_kind"], expected["target_id"], lock=True)
    check_identity(identity, expected)
    session = uuid.UUID(str(view_session))
    key = canonical_hash({"actor": user.pk, "identity": identity, "session": str(session), "source": source})
    return save_once(EvaluationExposure, **actor_values(user), **identity, source=source,
                     view_session=session, idempotency_key=key)


@transaction.atomic
def record_feedback(user, expected, *, action, request_key, reason="", rating=None,
                    exposure_id=None, supersedes_id=None, source="human_service"):
    require_enabled()
    if action == "edit":
        raise ValidationError("Edit feedback is recorded only by the authorized edit service.")
    obj, identity, browser_source = target_for(user, expected["target_kind"], expected["target_id"], lock=True)
    check_identity(identity, expected)
    if source not in {"human_service", browser_source}:
        raise ValidationError("Invalid feedback source.")
    key = canonical_hash({"actor": user.pk, "request": str(uuid.UUID(str(request_key)))})
    return save_once(HumanFeedback, **actor_values(user), **identity, source=source,
                     action=action, reason=reason, rating=rating, exposure_id=exposure_id,
                     supersedes_id=supersedes_id, idempotency_key=key)


@transaction.atomic
def edit_research(user, expected, *, context, request_key):
    require_enabled()
    obj, identity, _ = target_for(user, expected["target_kind"], expected["target_id"], lock=True, edit=True)
    key = canonical_hash({"actor": user.pk, "edit_request": str(uuid.UUID(str(request_key)))})
    existing = HumanFeedback.objects.filter(idempotency_key=key).first()
    if not isinstance(context, str) or not context.strip() or len(context) > 200000:
        raise ValidationError("Research text must contain 1–200000 characters.")
    after = canonical_hash({"topic": obj.topic, "focus": obj.focus, "context": context})
    if existing:
        if (existing.output_hash != expected["output_hash"] or existing.after_hash != after or
                existing.target_kind != expected["target_kind"] or existing.target_id != expected["target_id"]):
            raise ValidationError("Edit retry conflicts with the recorded edit.")
        return existing, False
    check_identity(identity, expected)
    if after == identity["output_hash"]:
        raise ValidationError("No content change to save.")
    obj.context = context
    obj.save(update_fields=["context"])
    result = HumanFeedback.objects.create(**actor_values(user), **identity, source="research_edit", action="edit",
                                         before_hash=identity["output_hash"], after_hash=after, idempotency_key=key)
    return result, True


@transaction.atomic
def link_outcome(user, feedback_id, outcome_id):
    require_enabled()
    feedback = HumanFeedback.objects.select_for_update().get(pk=feedback_id)
    target_for(user, feedback.target_kind, feedback.target_id)
    if feedback.actor_user_id != user.pk:
        raise PermissionDenied
    existing = FeedbackOutcomeLink.objects.filter(feedback=feedback, outcome_id=outcome_id).first()
    if existing:
        return existing, False
    link = FeedbackOutcomeLink.objects.create(feedback=feedback, outcome_id=outcome_id, actor_label=f"user:{user.pk}")
    return link, True


def feedback_state(user, expected, *, since, until=None, telemetry_complete=False):
    """Absence is unknown unless caller has independent telemetry coverage evidence."""
    target_for(user, expected["target_kind"], expected["target_id"])
    until = until or timezone.now()
    if since > until:
        raise ValidationError("Invalid reporting window.")
    scope = {"actor_user_id": user.pk, **expected}
    all_rows = HumanFeedback.objects.filter(**scope, created_at__lte=until)
    corrected = all_rows.exclude(supersedes_id=None).values_list("supersedes_id", flat=True)
    actions = set(all_rows.filter(created_at__gte=since).exclude(pk__in=corrected).values_list("action", flat=True))
    positive = bool(actions & {"accept", "useful", "save", "cite", "action", "edit"})
    negative = bool(actions & {"reject", "irrelevant", "dismiss"})
    if positive or negative:
        return "mixed" if positive and negative else "positive" if positive else "negative"
    if EvaluationExposure.objects.filter(**scope, created_at__gte=since, created_at__lte=until).exists():
        return "exposed_without_feedback"
    return "not_exposed" if telemetry_complete else "unknown"
