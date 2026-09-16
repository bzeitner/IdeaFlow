"""Operator-only A1/A2 services. No generation, projection, or schedule writes."""
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from executions.models import LLMRun
from executions.services import canonical_hash
from .deterministic import evaluate, validate_contract
from .models import EvaluationResult, EvaluatorVersion
from .payloads import read_evaluation_output
from .security import validate_metadata
from .validation import summarize


@transaction.atomic
def record_result(**values):
    """Immutable content-aware idempotency, including concurrent insertion."""
    if not settings.IDEAFLOW_EXECUTION_FLAGS.get("evaluators", False):
        raise ValidationError("Evaluation writes are disabled.")
    candidate = EvaluationResult(**values)
    # Never validate against a caller's unsaved changes or cached relations.
    candidate.evaluated_run = LLMRun.objects.select_for_update(of=("self",)).get(
        pk=candidate.evaluated_run_id
    )
    candidate.evaluator_version = EvaluatorVersion.objects.select_related(
        "metric", "evaluator"
    ).get(pk=candidate.evaluator_version_id)
    candidate.content_hash = candidate.fingerprint()
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    candidate.content_hash = candidate.fingerprint()
    key = candidate.idempotency_key
    existing = EvaluationResult.objects.filter(idempotency_key=key).first()
    if existing:
        if candidate.content_hash != existing.content_hash:
            raise ValidationError("Idempotency key already identifies different evaluation content.")
        return existing, False
    try:
        with transaction.atomic():
            candidate.save()
    except IntegrityError:
        existing = EvaluationResult.objects.filter(idempotency_key=key).first()
        if existing is None:
            raise
        if existing.content_hash != candidate.content_hash:
            raise ValidationError("Concurrent idempotency key conflict.")
        return existing, False
    return candidate, True


def _reference_observations(run, references):
    if not isinstance(references, list) or len(references) > 100:
        raise ValidationError("References must be a list of at most 100 internal objects.")
    observations = []
    trace = run.trace
    if references and (not trace.subject_content_type_id or trace.subject_content_type.app_label != "ideas" or trace.subject_content_type.model != "idea"):
        raise ValidationError("Internal reference checks require an idea-scoped trace.")
    for ref in references:
        if not isinstance(ref, dict) or set(ref) != {"model", "id"} or ref.get("model") not in {"ideas.researchentry", "ideas.artifact"} or type(ref.get("id")) is not int or ref["id"] < 1:
            raise ValidationError("Only positive research-entry/artifact references are supported.")
        model = apps.get_model(ref["model"])
        valid = model.objects.filter(pk=ref["id"], idea_id=trace.subject_object_id).exists()
        observations.append({**ref, "valid": valid})
    return observations


def evaluate_run(run, version, *, actor_label, idempotency_key, output=None,
                 contract=None, references=None, store=None):
    if not settings.IDEAFLOW_EXECUTION_FLAGS.get("evaluators", False):
        raise ValidationError("Evaluation writes are disabled; enable IDEAFLOW_EXECUTION_EVALUATORS for an operator canary.")
    validate_metadata({"actor_label": actor_label, "idempotency_key": idempotency_key,
                       "contract": contract, "references": references})
    version = EvaluatorVersion.objects.select_related("metric", "evaluator").get(pk=version.pk)
    if version.method != "deterministic" or version.implementation != "research-structure-v1":
        raise ValidationError("This command supports only the deterministic research-structure-v1 evaluator.")
    if not version.evaluator.is_active:
        raise ValidationError("Evaluator is inactive.")
    run = LLMRun.objects.select_related("trace__workflow_version__workflow", "trace__subject_content_type").get(pk=run.pk)
    if run.status != "succeeded" or not run.output_hash:
        raise ValidationError("A successful run with an output hash is required.")
    if run.trace.workflow_version.workflow.key not in version.applicability["workflows"]:
        raise ValidationError("Evaluator does not apply to this workflow.")
    # Text is the explicit default contract for research reports. Structured
    # expectations must be supplied; never infer a schema from the candidate.
    contract = {"format": "text"} if contract is None else contract
    references = [] if references is None else references
    validate_contract(contract)
    request_hash = canonical_hash({"run": str(run.pk), "output_hash": run.output_hash,
                                   "evaluator_hash": version.content_hash, "contract": contract,
                                   "references": references, "actor_label": actor_label})
    if output is not None and (not isinstance(output, bytes) or canonical_hash(output) != run.output_hash):
        raise ValidationError("Provided bytes do not match the run output hash.")
    existing = EvaluationResult.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.input_manifest.get("request_hash") != request_hash:
            raise ValidationError("Idempotency key already identifies different evaluation inputs.")
        return existing, False
    output_source = "operator_file" if output is not None else "execution_payload"
    if output is None:
        output = read_evaluation_output(run.pk, actor_label=actor_label, store=store)
    return _record_evaluation(run, version, actor_label=actor_label, idempotency_key=idempotency_key,
                              output=output, output_source=output_source, contract=contract,
                              references=references, request_hash=request_hash)


@transaction.atomic
def _record_evaluation(run, version, *, actor_label, idempotency_key, output,
                       output_source, contract, references, request_hash):
    expected_hash = run.output_hash
    run = LLMRun.objects.select_for_update(of=("self",)).select_related(
        "trace__workflow_version__workflow", "trace__subject_content_type"
    ).get(pk=run.pk)
    if run.output_hash != expected_hash:
        raise ValidationError("Evaluated output changed during the read.")
    existing = EvaluationResult.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.input_manifest.get("request_hash") != request_hash:
            raise ValidationError("Idempotency key already identifies different evaluation inputs.")
        return existing, False
    if len(output) > settings.IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES or canonical_hash(output) != run.output_hash:
        raise ValidationError("Output size or hash verification failed.")
    observations = _reference_observations(run, references)
    manifest = {
        "schema_version": 1, "evaluated_run": str(run.pk), "output_hash": run.output_hash,
        "evaluator_hash": version.content_hash, "request_hash": request_hash,
        "output_contract": contract, "finish_reason": run.finish_reason,
        "reference_observations": observations, "observed_at": timezone.now().isoformat(),
        "rubric_key": "research", "workflow_version": run.trace.workflow_version_id,
        "evidence": {"output": {"kind": "output", "hash": run.output_hash, "source": output_source,
                                "reference": run.output_ref if output_source == "execution_payload" else ""},
                     "contract": {"kind": "output_contract", "hash": canonical_hash(contract), "value": contract},
                     "run": {"kind": "run.finish_reason", "hash": canonical_hash(run.finish_reason), "value": run.finish_reason},
                     "references": {"kind": "reference_observations", "hash": canonical_hash(observations), "value": observations}},
    }
    rows = evaluate(output, manifest)
    return record_result(evaluator_version=version, evaluated_run=run,
                         output_hash=run.output_hash, input_manifest=manifest,
                         input_manifest_hash=canonical_hash(manifest), criterion_results=rows,
                         summary=summarize(version.rubric, rows), actor_label=actor_label,
                         idempotency_key=idempotency_key)
