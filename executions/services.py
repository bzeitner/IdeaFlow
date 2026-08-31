import hashlib
import json
import re

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import (
    ExecutionEvent, ExecutionTrace, LLMRun, MeasurementStatus, RunPurpose,
    ToolInvocation, TraceStatus,
)


TERMINAL_STATUSES = {TraceStatus.SUCCEEDED, TraceStatus.FAILED, TraceStatus.CANCELLED}
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


def canonical_hash(value):
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def redact_error_detail(value, limit=8000):
    redacted = str(value or "")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", redacted)
    return redacted[:limit]


@transaction.atomic
def append_event(trace, event_type, *, run=None, payload=None, occurred_at=None):
    locked_trace = ExecutionTrace.objects.select_for_update().get(pk=trace.pk)
    if run and run.trace_id != locked_trace.pk:
        raise ValidationError("Event run must belong to the supplied trace.")
    last_sequence = (
        ExecutionEvent.objects.filter(trace=locked_trace)
        .aggregate(value=Max("sequence"))["value"] or 0
    )
    return ExecutionEvent.objects.create(
        trace=locked_trace,
        run=run,
        sequence=last_sequence + 1,
        event_type=event_type,
        occurred_at=occurred_at or timezone.now(),
        payload=payload or {},
    )


@transaction.atomic
def start_trace(
    workflow_version, *, trigger, subject=None, subject_label="", actor=None,
    actor_label="", correlation_key="", idempotency_key="", experiment_metadata=None,
):
    if idempotency_key:
        existing = ExecutionTrace.objects.filter(
            workflow_version=workflow_version, idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, False
    now = timezone.now()
    content_type = None
    object_id = None
    if subject is not None:
        if subject.pk is None:
            raise ValidationError("Trace subjects must be saved before execution.")
        content_type = ContentType.objects.get_for_model(subject, for_concrete_model=False)
        object_id = subject.pk
        subject_label = subject_label or str(subject)
    trace = ExecutionTrace.objects.create(
        workflow_version=workflow_version,
        subject_content_type=content_type,
        subject_object_id=object_id,
        subject_label=subject_label,
        trigger=trigger,
        actor=actor,
        actor_label=actor_label,
        correlation_key=correlation_key,
        idempotency_key=idempotency_key,
        experiment_metadata=experiment_metadata or {},
        queued_at=now,
    )
    append_event(trace, "trace.queued", occurred_at=now)
    return trace, True


@transaction.atomic
def start_run(
    trace, model_configuration, *, purpose=RunPurpose.GENERATION,
    prompt_revision_manifest=None, rendered_input_hash, rendered_input_ref="",
    context_manifest=None, parent_run=None, attempt_number=None, idempotency_key="",
):
    locked_trace = ExecutionTrace.objects.select_for_update().get(pk=trace.pk)
    if locked_trace.status in TERMINAL_STATUSES:
        raise ValidationError("Cannot start a run for a terminal trace.")
    if idempotency_key:
        existing = LLMRun.objects.filter(
            trace=locked_trace, idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, False
    if parent_run and parent_run.trace_id != locked_trace.pk:
        raise ValidationError("Parent run must belong to this trace.")
    if attempt_number is None:
        attempt_number = (
            LLMRun.objects.filter(trace=locked_trace, purpose=purpose)
            .aggregate(value=Max("attempt_number"))["value"] or 0
        ) + 1
    now = timezone.now()
    run = LLMRun.objects.create(
        trace=locked_trace,
        parent_run=parent_run,
        purpose=purpose,
        attempt_number=attempt_number,
        model_configuration=model_configuration,
        prompt_revision_manifest=prompt_revision_manifest or [],
        rendered_input_ref=rendered_input_ref,
        rendered_input_hash=rendered_input_hash,
        context_manifest=context_manifest or {},
        status=TraceStatus.RUNNING,
        idempotency_key=idempotency_key,
        queued_at=now,
        started_at=now,
    )
    if locked_trace.status == TraceStatus.QUEUED:
        locked_trace.status = TraceStatus.RUNNING
        locked_trace.started_at = now
        locked_trace.save(update_fields=["status", "started_at"])
    append_event(locked_trace, "run.started", run=run, occurred_at=now)
    return run, True


def _validate_measurement(status, reasons):
    reasons = reasons or []
    if status == MeasurementStatus.COMPLETE and reasons:
        raise ValidationError("Complete measurements cannot have unavailable reasons.")
    if status != MeasurementStatus.COMPLETE and not reasons:
        raise ValidationError("Partial or unavailable measurements require explicit reasons.")
    return reasons


@transaction.atomic
def complete_run(
    run, *, output_hash, output_ref="", parsed_output=None, finish_reason="",
    schema_valid=None, provider_request_id="", usage=None, cost_micros=None,
    cost_currency="USD", cost_source="", measurement_status=MeasurementStatus.COMPLETE,
    measurement_unavailable_reasons=None, first_token_at=None, completed_at=None,
    finalize_trace=False,
):
    locked = LLMRun.objects.select_for_update().select_related("trace").get(pk=run.pk)
    if locked.status in TERMINAL_STATUSES:
        return locked, False
    reasons = _validate_measurement(measurement_status, measurement_unavailable_reasons)
    usage = usage or {}
    now = completed_at or timezone.now()
    for field in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "total_tokens"):
        setattr(locked, field, usage.get(field))
    locked.output_hash = output_hash
    locked.output_ref = output_ref
    locked.parsed_output = parsed_output
    locked.finish_reason = finish_reason
    locked.schema_valid = schema_valid
    locked.provider_request_id = provider_request_id
    locked.cost_micros = cost_micros
    locked.cost_currency = cost_currency
    locked.cost_source = cost_source
    locked.measurement_status = measurement_status
    locked.measurement_unavailable_reasons = reasons
    locked.first_token_at = first_token_at
    locked.completed_at = now
    locked.status = TraceStatus.SUCCEEDED
    locked.save()
    append_event(locked.trace, "run.succeeded", run=locked, occurred_at=now)
    if finalize_trace:
        complete_trace(locked.trace, completed_at=now)
    return locked, True


@transaction.atomic
def fail_run(
    run, *, error_class, error_code="", error_detail="", completed_at=None,
    measurement_status=MeasurementStatus.UNAVAILABLE,
    measurement_unavailable_reasons=None,
):
    locked = LLMRun.objects.select_for_update().select_related("trace").get(pk=run.pk)
    if locked.status in TERMINAL_STATUSES:
        return locked, False
    reasons = _validate_measurement(measurement_status, measurement_unavailable_reasons)
    now = completed_at or timezone.now()
    locked.status = TraceStatus.FAILED
    locked.error_class = error_class
    locked.error_code = error_code
    locked.error_detail = redact_error_detail(error_detail)
    locked.completed_at = now
    locked.measurement_status = measurement_status
    locked.measurement_unavailable_reasons = reasons
    locked.save()
    append_event(locked.trace, "run.failed", run=locked, occurred_at=now)
    return locked, True


@transaction.atomic
def complete_trace(trace, *, completed_at=None):
    trace = ExecutionTrace.objects.select_for_update().get(pk=trace.pk)
    if trace.status == TraceStatus.SUCCEEDED:
        return trace
    if trace.runs.exclude(status__in=TERMINAL_STATUSES).exists():
        raise ValidationError("Cannot complete a trace with non-terminal runs.")
    if not trace.runs.filter(status=TraceStatus.SUCCEEDED).exists():
        raise ValidationError("A successful trace requires at least one successful run.")
    completed_at = completed_at or timezone.now()
    trace.status = TraceStatus.SUCCEEDED
    trace.completed_at = completed_at
    trace.save(update_fields=["status", "completed_at"])
    append_event(trace, "trace.succeeded", occurred_at=completed_at)
    return trace


@transaction.atomic
def start_tool_invocation(
    run, *, tool_name, tool_version="", mutating=False, request_ref="",
    request_hash="", idempotency_key="", affected_objects=None,
):
    locked_run = LLMRun.objects.select_for_update().select_related("trace").get(pk=run.pk)
    if locked_run.status != TraceStatus.RUNNING:
        raise ValidationError("Tool invocations require a running LLM run.")
    if idempotency_key:
        existing = ToolInvocation.objects.filter(
            run=locked_run, idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, False
    now = timezone.now()
    invocation = ToolInvocation.objects.create(
        run=locked_run,
        tool_name=tool_name,
        tool_version=tool_version,
        mutating=mutating,
        request_ref=request_ref,
        request_hash=request_hash,
        idempotency_key=idempotency_key,
        affected_objects=affected_objects or [],
        status=TraceStatus.RUNNING,
        started_at=now,
    )
    append_event(
        locked_run.trace, "tool.started", run=locked_run,
        payload={"tool_invocation_id": str(invocation.pk), "tool_name": tool_name},
        occurred_at=now,
    )
    return invocation, True


@transaction.atomic
def complete_tool_invocation(
    invocation, *, response_ref="", response_hash="", affected_objects=None,
    completed_at=None,
):
    locked = ToolInvocation.objects.select_for_update().select_related("run__trace").get(
        pk=invocation.pk
    )
    if locked.status in TERMINAL_STATUSES:
        return locked, False
    now = completed_at or timezone.now()
    locked.status = TraceStatus.SUCCEEDED
    locked.response_ref = response_ref
    locked.response_hash = response_hash
    if affected_objects is not None:
        locked.affected_objects = affected_objects
    locked.completed_at = now
    locked.save()
    append_event(
        locked.run.trace, "tool.succeeded", run=locked.run,
        payload={"tool_invocation_id": str(locked.pk), "tool_name": locked.tool_name},
        occurred_at=now,
    )
    return locked, True


@transaction.atomic
def fail_tool_invocation(
    invocation, *, error_class, error_detail="", completed_at=None,
):
    locked = ToolInvocation.objects.select_for_update().select_related("run__trace").get(
        pk=invocation.pk
    )
    if locked.status in TERMINAL_STATUSES:
        return locked, False
    now = completed_at or timezone.now()
    locked.status = TraceStatus.FAILED
    locked.error_class = error_class
    locked.error_detail = redact_error_detail(error_detail)
    locked.completed_at = now
    locked.save()
    append_event(
        locked.run.trace, "tool.failed", run=locked.run,
        payload={"tool_invocation_id": str(locked.pk), "tool_name": locked.tool_name},
        occurred_at=now,
    )
    return locked, True


@transaction.atomic
def attach_projection(projection, run, *, field_name="produced_by_run"):
    """Attach generated state to its run without silently re-attributing it."""
    if not hasattr(projection, field_name):
        raise ValidationError(f"Projection has no {field_name} provenance field.")
    existing_id = getattr(projection, f"{field_name}_id")
    if existing_id and existing_id != run.pk:
        raise ValidationError("Projection is already attributed to another run.")
    trace = run.trace
    projection_idea_id = getattr(projection, "idea_id", None)
    if (
        projection_idea_id
        and trace.subject_content_type_id
        and trace.subject_content_type.model == "idea"
        and trace.subject_object_id != projection_idea_id
    ):
        raise ValidationError("Projection idea does not match the trace subject.")
    setattr(projection, field_name, run)
    projection.save(update_fields=[field_name])
    append_event(
        trace, "projection.attached", run=run,
        payload={
            "model": projection._meta.label_lower,
            "object_id": str(projection.pk),
            "field": field_name,
        },
    )
    return projection


def estimate_cost_micros(model_configuration, usage):
    pricing = model_configuration.pricing_version
    if pricing is None:
        return None
    components = (
        ("input_tokens", pricing.input_micros_per_million),
        ("output_tokens", pricing.output_micros_per_million),
        ("cached_tokens", pricing.cached_micros_per_million),
        ("reasoning_tokens", pricing.reasoning_micros_per_million),
    )
    numerator = sum((usage.get(field) or 0) * rate for field, rate in components)
    return (numerator + 999_999) // 1_000_000
