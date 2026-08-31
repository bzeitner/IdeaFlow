import uuid
import hashlib

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models


class ApprovalStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    APPROVED = "approved", "Approved"
    SUPERSEDED = "superseded", "Superseded"
    REJECTED = "rejected", "Rejected"


class TraceStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class RunPurpose(models.TextChoices):
    GENERATION = "generation", "Generation"
    CLASSIFICATION = "classification", "Classification"
    EXTRACTION = "extraction", "Extraction"
    EVALUATION = "evaluation", "Evaluation"
    EMBEDDING = "embedding", "Embedding"


class MeasurementStatus(models.TextChoices):
    COMPLETE = "complete", "Complete"
    PARTIAL = "partial", "Partial"
    UNAVAILABLE = "unavailable", "Unavailable"


class ImmutableAfterCreateModel(models.Model):
    """Application-level guard for approved execution configuration records."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError(
                f"{type(self).__name__} records are immutable; create a new version."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            f"{type(self).__name__} records are retained for execution audit."
        )


class AuditRetainedModel(models.Model):
    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        raise ValidationError(
            f"{type(self).__name__} records are retained for execution audit."
        )


class ServicePrincipal(models.Model):
    name = models.CharField(max_length=120, unique=True)
    token_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def has_scope(self, required):
        return required in self.scopes or "execution:*" in self.scopes

    def __str__(self):
        return self.name


class WorkflowDefinition(models.Model):
    key = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.name


class WorkflowVersion(ImmutableAfterCreateModel):
    workflow = models.ForeignKey(
        WorkflowDefinition, related_name="versions", on_delete=models.PROTECT
    )
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.PROPOSED
    )
    configuration = models.JSONField(default=dict)
    prompt_revision_manifest = models.JSONField(default=list)
    content_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        related_name="workflow_versions_created", on_delete=models.SET_NULL,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        related_name="workflow_versions_approved", on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["workflow__key", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "version"], name="unique_workflow_version"
            )
        ]

    def __str__(self):
        return f"{self.workflow.key} v{self.version}"


class PricingVersion(ImmutableAfterCreateModel):
    provider = models.CharField(max_length=40)
    model_identifier = models.CharField(max_length=160)
    currency = models.CharField(max_length=3, default="USD")
    input_micros_per_million = models.PositiveBigIntegerField(default=0)
    output_micros_per_million = models.PositiveBigIntegerField(default=0)
    cached_micros_per_million = models.PositiveBigIntegerField(default=0)
    reasoning_micros_per_million = models.PositiveBigIntegerField(default=0)
    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(null=True, blank=True)
    source = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["provider", "model_identifier", "-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "model_identifier", "effective_from"],
                name="unique_model_pricing_effective_from",
            )
        ]

    def clean(self):
        if self.effective_until and self.effective_until <= self.effective_from:
            raise ValidationError({"effective_until": "Must follow effective_from."})

    def __str__(self):
        return f"{self.provider}/{self.model_identifier} from {self.effective_from}"


class ModelConfiguration(ImmutableAfterCreateModel):
    provider = models.CharField(max_length=40)
    model_identifier = models.CharField(max_length=160)
    capability = models.CharField(max_length=40, blank=True)
    settings = models.JSONField(default=dict)
    pricing_version = models.ForeignKey(
        PricingVersion, null=True, blank=True, related_name="model_configurations",
        on_delete=models.PROTECT,
    )
    content_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["provider", "model_identifier", "created_at"]

    def __str__(self):
        return f"{self.provider}/{self.model_identifier}"


class ExecutionTrace(AuditRetainedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow_version = models.ForeignKey(
        WorkflowVersion, related_name="traces", on_delete=models.PROTECT
    )
    subject_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.SET_NULL
    )
    subject_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")
    subject_label = models.CharField(max_length=240, blank=True)
    trigger = models.CharField(max_length=32)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        related_name="execution_traces", on_delete=models.SET_NULL,
    )
    actor_label = models.CharField(max_length=160, blank=True)
    status = models.CharField(
        max_length=16, choices=TraceStatus.choices, default=TraceStatus.QUEUED
    )
    correlation_key = models.CharField(max_length=160, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=200, blank=True)
    experiment_metadata = models.JSONField(default=dict, blank=True)
    queued_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workflow_version", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_workflow_trace_idempotency",
            )
        ]

    def __str__(self):
        return f"{self.workflow_version} · {self.id}"


class LLMRun(AuditRetainedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        ExecutionTrace, related_name="runs", on_delete=models.PROTECT
    )
    parent_run = models.ForeignKey(
        "self", null=True, blank=True, related_name="child_runs", on_delete=models.PROTECT
    )
    purpose = models.CharField(max_length=20, choices=RunPurpose.choices)
    attempt_number = models.PositiveIntegerField(default=1)
    model_configuration = models.ForeignKey(
        ModelConfiguration, related_name="runs", on_delete=models.PROTECT
    )
    prompt_revision_manifest = models.JSONField(default=list)
    rendered_input_ref = models.CharField(max_length=500, blank=True)
    rendered_input_hash = models.CharField(max_length=64)
    context_manifest = models.JSONField(default=dict)
    output_ref = models.CharField(max_length=500, blank=True)
    output_hash = models.CharField(max_length=64, blank=True)
    parsed_output = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=TraceStatus.choices, default=TraceStatus.QUEUED
    )
    finish_reason = models.CharField(max_length=80, blank=True)
    schema_valid = models.BooleanField(null=True)
    provider_request_id = models.CharField(max_length=200, blank=True, db_index=True)
    input_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    output_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    cached_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    reasoning_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    total_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    cost_micros = models.PositiveBigIntegerField(null=True, blank=True)
    cost_currency = models.CharField(max_length=3, default="USD")
    cost_source = models.CharField(max_length=24, blank=True)
    measurement_status = models.CharField(
        max_length=16, choices=MeasurementStatus.choices,
        default=MeasurementStatus.PARTIAL,
    )
    measurement_unavailable_reasons = models.JSONField(default=list, blank=True)
    queued_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    first_token_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_class = models.CharField(max_length=160, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_detail = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["trace", "attempt_number", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["trace", "attempt_number", "purpose"],
                name="unique_trace_attempt_purpose",
            ),
            models.UniqueConstraint(
                fields=["trace", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_run_idempotency_per_trace",
            ),
        ]

    def clean(self):
        if self.parent_run_id and self.parent_run.trace_id != self.trace_id:
            raise ValidationError({"parent_run": "Parent run must belong to this trace."})

    def __str__(self):
        return f"{self.trace_id} · {self.purpose} attempt {self.attempt_number}"


class ToolInvocation(AuditRetainedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(LLMRun, related_name="tool_invocations", on_delete=models.PROTECT)
    tool_name = models.CharField(max_length=160)
    tool_version = models.CharField(max_length=100, blank=True)
    mutating = models.BooleanField(default=False)
    request_ref = models.CharField(max_length=500, blank=True)
    request_hash = models.CharField(max_length=64, blank=True)
    response_ref = models.CharField(max_length=500, blank=True)
    response_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=TraceStatus.choices)
    idempotency_key = models.CharField(max_length=200, blank=True)
    affected_objects = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    error_class = models.CharField(max_length=160, blank=True)
    error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["started_at", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_tool_idempotency_per_run",
            )
        ]


class ExecutionEvent(models.Model):
    trace = models.ForeignKey(
        ExecutionTrace, related_name="events", on_delete=models.PROTECT
    )
    run = models.ForeignKey(
        LLMRun, null=True, blank=True, related_name="events", on_delete=models.PROTECT
    )
    sequence = models.PositiveBigIntegerField()
    event_type = models.CharField(max_length=100)
    occurred_at = models.DateTimeField()
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["trace", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["trace", "sequence"], name="unique_trace_event_sequence"
            )
        ]

    def clean(self):
        if self.run_id and self.run.trace_id != self.trace_id:
            raise ValidationError({"run": "Event run must belong to this trace."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Execution events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Execution events are retained for audit.")
