import hashlib
import json
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt

from ideas.models import Idea, PromptRevisionStatus, PromptTemplate

from .models import (
    ExecutionTrace, LLMRun, MeasurementStatus, ModelConfiguration,
    RunPurpose, ServicePrincipal, ToolInvocation, TraceStatus,
    WorkflowVersion,
)
from .services import (
    append_event, canonical_hash, complete_run, complete_tool_invocation,
    complete_trace, fail_run, fail_tool_invocation, fail_trace, start_run,
    start_tool_invocation, start_trace,
)
from .storage import ExecutionPayloadStore


def _provided_token(request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return request.headers.get("X-Execution-Token", "").strip()


def require_execution_scope(scope):
    def decorate(view):
        @csrf_exempt
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not settings.IDEAFLOW_EXECUTION_FLAGS.get("instrumentation", False):
                return JsonResponse(
                    {"error": "Execution instrumentation is disabled."}, status=503
                )
            token = _provided_token(request)
            if not token:
                return JsonResponse({"error": "Missing execution token."}, status=401)
            token_hash = ServicePrincipal.hash_token(token)
            principal = ServicePrincipal.objects.filter(
                token_hash=token_hash, is_active=True, revoked_at=None
            ).first()
            if principal is None or not constant_time_compare(
                token_hash, principal.token_hash
            ):
                return JsonResponse({"error": "Invalid execution token."}, status=401)
            if not principal.has_scope(scope):
                return JsonResponse({"error": f"Missing required scope: {scope}"}, status=403)
            ServicePrincipal.objects.filter(pk=principal.pk).update(last_used_at=timezone.now())
            request.execution_principal = principal
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def _json_body(request):
    length = request.META.get("CONTENT_LENGTH")
    if length and int(length) > settings.IDEAFLOW_EXECUTION_API_MAX_BYTES:
        raise ValueError("Request body exceeds the execution API size limit.")
    try:
        value = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object.")
    return value


def _error(exc, status=400):
    messages = getattr(exc, "messages", None)
    return JsonResponse({"error": "; ".join(messages) if messages else str(exc)}, status=status)


def _trace_dict(trace):
    return {
        "id": str(trace.pk),
        "workflow": trace.workflow_version.workflow.key,
        "workflow_version": trace.workflow_version.version,
        "status": trace.status,
        "subject": {
            "type": trace.subject_content_type.model if trace.subject_content_type_id else None,
            "id": trace.subject_object_id,
            "label": trace.subject_label,
        },
        "queued_at": trace.queued_at.isoformat(),
        "started_at": trace.started_at.isoformat() if trace.started_at else None,
        "completed_at": trace.completed_at.isoformat() if trace.completed_at else None,
    }


def _run_dict(run):
    return {
        "id": str(run.pk),
        "trace_id": str(run.trace_id),
        "purpose": run.purpose,
        "attempt_number": run.attempt_number,
        "status": run.status,
        "provider": run.model_configuration.provider,
        "model": run.model_configuration.model_identifier,
        "measurement_status": run.measurement_status,
        "queued_at": run.queued_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _tool_dict(tool):
    return {
        "id": str(tool.pk),
        "run_id": str(tool.run_id),
        "tool_name": tool.tool_name,
        "tool_version": tool.tool_version,
        "mutating": tool.mutating,
        "status": tool.status,
        "started_at": tool.started_at.isoformat(),
        "completed_at": tool.completed_at.isoformat() if tool.completed_at else None,
    }


def _resolve_workflow(payload):
    key = str(payload.get("workflow") or "").strip()
    version = payload.get("workflow_version")
    versions = WorkflowVersion.objects.select_related("workflow").filter(
        workflow__key=key, status="approved", workflow__is_active=True
    )
    if version is not None:
        versions = versions.filter(version=version)
    workflow_version = versions.order_by("-version").first()
    if workflow_version is None:
        raise ValueError("No approved workflow version matches the request.")
    return workflow_version


def _resolve_subject(payload):
    subject = payload.get("subject")
    if not subject:
        return None
    if not isinstance(subject, dict) or subject.get("type") != "idea":
        raise ValueError("Phase 2 supports only idea subjects.")
    try:
        return Idea.objects.get(pk=int(subject.get("id")))
    except (Idea.DoesNotExist, TypeError, ValueError) as exc:
        raise ValueError("Unknown idea subject.") from exc


def _resolve_model_configuration(payload):
    provider = str(payload.get("provider") or "").strip()[:40]
    model = str(payload.get("model") or "").strip()[:160]
    model_settings = payload.get("settings") or {}
    if not provider or not model or not isinstance(model_settings, dict):
        raise ValueError("provider, model, and object-valued settings are required.")
    existing = ModelConfiguration.objects.filter(
        provider=provider, model_identifier=model, settings=model_settings
    ).order_by("created_at").first()
    if existing:
        return existing
    frozen = {"provider": provider, "model_identifier": model, "settings": model_settings}
    return ModelConfiguration.objects.create(
        provider=provider,
        model_identifier=model,
        settings=model_settings,
        content_hash=canonical_hash(frozen),
    )


def _prompt_manifest(keys):
    if not isinstance(keys, list):
        raise ValueError("prompt_keys must be an array.")
    manifest = []
    for key in keys:
        template = PromptTemplate.objects.filter(key=key, is_active=True).first()
        revision = (
            template.revisions.filter(status=PromptRevisionStatus.APPROVED)
            .order_by("-version").first()
            if template else None
        )
        if revision is None:
            raise ValueError(f"No approved prompt revision exists for {key}.")
        manifest.append(
            {
                "key": key,
                "version": revision.version,
                "sha256": hashlib.sha256(revision.content.encode("utf-8")).hexdigest(),
            }
        )
    return manifest


@require_execution_scope("execution:write")
def trace_collection(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = _json_body(request)
        workflow_version = _resolve_workflow(payload)
        subject = _resolve_subject(payload)
        trace, created = start_trace(
            workflow_version,
            trigger=str(payload.get("trigger") or "machine")[:32],
            subject=subject,
            subject_label=str(payload.get("subject_label") or "")[:240],
            actor_label=request.execution_principal.name,
            correlation_key=str(payload.get("correlation_key") or "")[:160],
            idempotency_key=str(payload.get("idempotency_key") or "")[:200],
        )
    except (ValueError, TypeError, ValidationError) as exc:
        return _error(exc)
    return JsonResponse(_trace_dict(trace), status=201 if created else 200)


@require_execution_scope("execution:write")
def run_collection(request, trace_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    trace = get_object_or_404(ExecutionTrace, pk=trace_id)
    try:
        payload = _json_body(request)
        configuration = _resolve_model_configuration(payload)
        prompt_manifest = _prompt_manifest(payload.get("prompt_keys") or [])
        input_hash = str(payload.get("rendered_input_hash") or "")
        if len(input_hash) != 64:
            raise ValueError("rendered_input_hash must be a SHA-256 hex digest.")
        input_ref = ""
        if settings.IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS and "rendered_input" in payload:
            stored = ExecutionPayloadStore().put(
                "prompt", payload["rendered_input"], content_type="text/plain"
            )
            if not constant_time_compare(stored.sha256, input_hash):
                raise ValueError("rendered_input does not match rendered_input_hash.")
            input_ref = stored.reference
        parent = None
        if payload.get("parent_run_id"):
            parent = get_object_or_404(LLMRun, pk=payload["parent_run_id"])
        purpose = str(payload.get("purpose") or RunPurpose.GENERATION)
        if purpose not in RunPurpose.values:
            raise ValueError("Unknown run purpose.")
        run, created = start_run(
            trace,
            configuration,
            purpose=purpose,
            prompt_revision_manifest=prompt_manifest,
            rendered_input_hash=input_hash,
            rendered_input_ref=input_ref,
            context_manifest=payload.get("context_manifest") or {},
            parent_run=parent,
            attempt_number=payload.get("attempt_number"),
            idempotency_key=str(payload.get("idempotency_key") or "")[:200],
        )
    except (ValueError, TypeError, ValidationError) as exc:
        return _error(exc)
    return JsonResponse(_run_dict(run), status=201 if created else 200)


def _usage(payload):
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError("usage must be an object.")
    allowed = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "total_tokens")
    result = {}
    for field in allowed:
        value = usage.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise ValueError(f"usage.{field} must be a non-negative integer.")
        result[field] = value
    return result


@require_execution_scope("execution:write")
def run_complete(request, run_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    run = get_object_or_404(LLMRun, pk=run_id)
    try:
        payload = _json_body(request)
        output_hash = str(payload.get("output_hash") or "")
        if len(output_hash) != 64:
            raise ValueError("output_hash must be a SHA-256 hex digest.")
        output_ref = ""
        if settings.IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS and "output" in payload:
            stored = ExecutionPayloadStore().put(
                "response", payload["output"], content_type="text/plain"
            )
            if not constant_time_compare(stored.sha256, output_hash):
                raise ValueError("output does not match output_hash.")
            output_ref = stored.reference
        completed, changed = complete_run(
            run,
            output_hash=output_hash,
            output_ref=output_ref,
            parsed_output=payload.get("parsed_output"),
            finish_reason=str(payload.get("finish_reason") or "")[:80],
            schema_valid=payload.get("schema_valid"),
            provider_request_id=str(payload.get("provider_request_id") or "")[:200],
            usage=_usage(payload),
            cost_micros=payload.get("cost_micros"),
            cost_currency=str(payload.get("cost_currency") or "USD")[:3],
            cost_source=str(payload.get("cost_source") or "")[:24],
            measurement_status=payload.get("measurement_status") or MeasurementStatus.PARTIAL,
            measurement_unavailable_reasons=payload.get("measurement_unavailable_reasons") or [],
        )
    except (ValueError, TypeError, ValidationError) as exc:
        return _error(exc)
    result = _run_dict(completed)
    result["changed"] = changed
    return JsonResponse(result)


@require_execution_scope("execution:write")
def run_fail(request, run_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    run = get_object_or_404(LLMRun, pk=run_id)
    try:
        payload = _json_body(request)
        failed, changed = fail_run(
            run,
            error_class=str(payload.get("error_class") or "ExecutionError")[:160],
            error_code=str(payload.get("error_code") or "")[:100],
            error_detail=str(payload.get("error_detail") or ""),
            measurement_status=payload.get("measurement_status") or MeasurementStatus.UNAVAILABLE,
            measurement_unavailable_reasons=payload.get("measurement_unavailable_reasons") or ["run_failed_before_usage"],
        )
    except (ValueError, TypeError) as exc:
        return _error(exc)
    result = _run_dict(failed)
    result["changed"] = changed
    return JsonResponse(result)


@require_execution_scope("execution:write")
def trace_complete(request, trace_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    trace = get_object_or_404(ExecutionTrace, pk=trace_id)
    try:
        trace = complete_trace(trace)
    except ValidationError as exc:
        return _error(exc, 409)
    return JsonResponse(_trace_dict(trace))


@require_execution_scope("execution:write")
def trace_fail(request, trace_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    trace = get_object_or_404(ExecutionTrace, pk=trace_id)
    try:
        payload = _json_body(request)
        trace, changed = fail_trace(trace, reason=payload.get("reason") or "")
    except (ValueError, TypeError) as exc:
        return _error(exc)
    result = _trace_dict(trace)
    result["changed"] = changed
    return JsonResponse(result)


@require_execution_scope("execution:write")
def run_event(request, run_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    run = get_object_or_404(LLMRun.objects.select_related("trace"), pk=run_id)
    try:
        payload = _json_body(request)
        event_type = str(payload.get("event_type") or "")[:100]
        if not event_type or not (
            event_type.startswith("provider.") or event_type == "run.first_token"
        ):
            raise ValueError("Unsupported compatibility event type.")
        event = append_event(run.trace, event_type, run=run, payload=payload.get("payload") or {})
        if event_type == "run.first_token" and run.first_token_at is None:
            LLMRun.objects.filter(pk=run.pk, first_token_at=None).update(first_token_at=event.occurred_at)
    except (ValueError, TypeError) as exc:
        return _error(exc)
    return JsonResponse({"sequence": event.sequence, "event_type": event.event_type}, status=201)


@require_execution_scope("execution:write")
def tool_collection(request, run_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    run = get_object_or_404(LLMRun, pk=run_id)
    try:
        payload = _json_body(request)
        tool_name = str(payload.get("tool_name") or "")[:160]
        if not tool_name:
            raise ValueError("tool_name is required.")
        tool, created = start_tool_invocation(
            run,
            tool_name=tool_name,
            tool_version=str(payload.get("tool_version") or "")[:100],
            mutating=payload.get("mutating") is True,
            request_ref=str(payload.get("request_ref") or "")[:500],
            request_hash=str(payload.get("request_hash") or "")[:64],
            idempotency_key=str(payload.get("idempotency_key") or "")[:200],
            affected_objects=payload.get("affected_objects") or [],
        )
    except (ValueError, TypeError, ValidationError) as exc:
        return _error(exc)
    return JsonResponse(_tool_dict(tool), status=201 if created else 200)


@require_execution_scope("execution:write")
def tool_complete(request, tool_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tool = get_object_or_404(ToolInvocation, pk=tool_id)
    try:
        payload = _json_body(request)
        tool, changed = complete_tool_invocation(
            tool,
            response_ref=str(payload.get("response_ref") or "")[:500],
            response_hash=str(payload.get("response_hash") or "")[:64],
            affected_objects=payload.get("affected_objects"),
        )
    except (ValueError, TypeError, ValidationError) as exc:
        return _error(exc)
    result = _tool_dict(tool)
    result["changed"] = changed
    return JsonResponse(result)


@require_execution_scope("execution:write")
def tool_fail(request, tool_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tool = get_object_or_404(ToolInvocation, pk=tool_id)
    try:
        payload = _json_body(request)
        tool, changed = fail_tool_invocation(
            tool,
            error_class=str(payload.get("error_class") or "ToolError")[:160],
            error_detail=str(payload.get("error_detail") or ""),
        )
    except (ValueError, TypeError, ValidationError) as exc:
        return _error(exc)
    result = _tool_dict(tool)
    result["changed"] = changed
    return JsonResponse(result)


urlpatterns = [
    path("v1/traces/", trace_collection, name="execution_trace_collection"),
    path("v1/traces/<uuid:trace_id>/runs/", run_collection, name="execution_run_collection"),
    path("v1/traces/<uuid:trace_id>/complete/", trace_complete, name="execution_trace_complete"),
    path("v1/traces/<uuid:trace_id>/fail/", trace_fail, name="execution_trace_fail"),
    path("v1/runs/<uuid:run_id>/events/", run_event, name="execution_run_event"),
    path("v1/runs/<uuid:run_id>/tools/", tool_collection, name="execution_tool_collection"),
    path("v1/runs/<uuid:run_id>/complete/", run_complete, name="execution_run_complete"),
    path("v1/runs/<uuid:run_id>/fail/", run_fail, name="execution_run_fail"),
    path("v1/tools/<uuid:tool_id>/complete/", tool_complete, name="execution_tool_complete"),
    path("v1/tools/<uuid:tool_id>/fail/", tool_fail, name="execution_tool_fail"),
]
