"""Durably audited protected reads for trusted, local evaluation operators."""
import uuid

from django.core.exceptions import ValidationError
from django.db import connection, transaction

from executions.models import LLMRun
from executions.services import append_event, canonical_hash
from executions.storage import ExecutionPayloadStore
from .security import validate_metadata


def _audit(run, event_type, payload):
    # durable=True refuses application-owned outer transactions. Django's
    # TestCase wrapper is the sole framework-supported exception; transaction
    # tests below verify real commits and failure survival on both databases.
    if not connection.in_atomic_block and not connection.get_autocommit():
        raise ValidationError("Protected evaluation reads require autocommit.")
    try:
        with transaction.atomic(durable=True):
            append_event(run.trace, event_type, run=run, payload=payload)
    except RuntimeError as exc:
        raise ValidationError("Protected evaluation reads cannot run inside another transaction.") from exc


def read_evaluation_output(run_id, *, actor_label, store=None):
    if not isinstance(actor_label, str) or not actor_label.strip() or len(actor_label) > 160:
        raise ValidationError("A bounded operator identity is required for payload access.")
    validate_metadata({"actor_label": actor_label})
    run = LLMRun.objects.select_related("trace").get(pk=run_id)
    if not run.output_ref:
        raise ValidationError("Output was not captured; provide exact output bytes with a matching hash.")
    payload = {"kind": "output", "actor_label": actor_label, "access_id": str(uuid.uuid4()),
               "source": "evaluation_operator", "expected_sha256": run.output_hash}
    # Fail closed before any read if the access attempt cannot be committed.
    _audit(run, "payload.access_requested", payload)
    try:
        content = (store or ExecutionPayloadStore()).get(run.output_ref)
    except Exception:
        # Error details/paths can contain sensitive material; record no raw text.
        _audit(run, "payload.access_failed", {**payload, "reason": "storage_read_failed"})
        raise
    digest = canonical_hash(content)
    _audit(run, "payload.accessed", {**payload, "sha256": digest, "hash_verified": digest == run.output_hash})
    if digest != run.output_hash:
        raise ValidationError("Output hash verification failed.")
    return content
