"""R5A audit records. Database triggers additionally protect frozen rows."""
import math
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from executions.services import canonical_hash

HASH = RegexValidator(r"^[0-9a-f]{64}$", "Expected a SHA-256 hash.")


class FrozenQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Audit records are immutable; create a new version.")

    def delete(self):
        raise ValidationError("Audit records are retained.")

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("Use validated individual audit writes.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("Audit records are immutable.")


class FrozenRecord(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    actor_label = models.CharField(max_length=160)
    content_hash = models.CharField(max_length=64, editable=False, validators=[HASH])
    objects = FrozenQuerySet.as_manager()

    class Meta:
        abstract = True

    def audit_values(self):
        return {
            field.attname: (str(getattr(self, field.attname)) if isinstance(getattr(self, field.attname), UUID) else getattr(self, field.attname))
            for field in self._meta.concrete_fields
            if field.name not in {"id", "created_at", "content_hash"}
        }

    def fingerprint(self):
        from .security import validate_metadata
        values = self.audit_values()
        validate_metadata(values)
        return canonical_hash(values)

    def full_clean(self, *args, **kwargs):
        from .security import validate_metadata
        validate_metadata(self.audit_values())
        return super().full_clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit records are immutable; create a new version.")
        self.content_hash = self.fingerprint()
        self.full_clean(validate_unique=False, validate_constraints=False)
        self.content_hash = self.fingerprint()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit records are retained.")


class MetricDefinition(FrozenRecord):
    key = models.CharField(max_length=100)
    version = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    description = models.TextField()
    unit = models.CharField(max_length=32)
    direction = models.CharField(max_length=16, choices=[("higher", "Higher"), ("lower", "Lower"), ("diagnostic", "Diagnostic")])
    minimum = models.FloatField(null=True, blank=True)
    maximum = models.FloatField(null=True, blank=True)
    aggregation = models.CharField(max_length=80)
    applicability = models.JSONField(default=dict)
    missing_value_policy = models.CharField(max_length=160, default="Report missing separately; never impute a score.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key", "version"], name="eval_metric_key_version"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="eval_metric_positive_version"),
            models.CheckConstraint(condition=models.Q(minimum__isnull=True) | models.Q(maximum__isnull=True) | models.Q(maximum__gte=models.F("minimum")), name="eval_metric_range"),
        ]

    def clean(self):
        for value in (self.minimum, self.maximum):
            if value is not None and not math.isfinite(value):
                raise ValidationError("Metric bounds must be finite.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValidationError("Metric bounds are reversed.")

    def __str__(self):
        return f"{self.key}@{self.version}"


class EvaluatorDefinition(models.Model):
    key = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField()
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.pk:
            original_key = type(self).objects.filter(pk=self.pk).values_list("key", flat=True).first()
            if original_key is not None and self.key != original_key:
                raise ValidationError("Evaluator identity keys cannot be renamed.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.key


class EvaluatorVersion(FrozenRecord):
    evaluator = models.ForeignKey(EvaluatorDefinition, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    metric = models.ForeignKey(MetricDefinition, on_delete=models.PROTECT)
    method = models.CharField(max_length=20, choices=[("deterministic", "Deterministic"), ("human", "Human"), ("model", "Model"), ("outcome", "Outcome")])
    implementation = models.CharField(max_length=120)
    rubric = models.JSONField()
    applicability = models.JSONField()
    required_inputs = models.JSONField(default=list)
    aggregation = models.JSONField()
    model_configuration = models.ForeignKey("executions.ModelConfiguration", null=True, blank=True, on_delete=models.PROTECT)
    prompt_manifest = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["evaluator", "version"], name="eval_evaluator_key_version"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="eval_version_positive"),
        ]

    def clean(self):
        from .validation import validate_input_names, validate_rubric
        validate_rubric(self.rubric)
        if not isinstance(self.applicability, dict) or not self.applicability.get("workflows"):
            raise ValidationError("Explicit workflow applicability is required.")
        validate_input_names(self.required_inputs)
        if self.aggregation != {"strategy": "diagnostic", "critical_failure_blocks": True} and self.aggregation != {"strategy": "ordinal", "critical_failure_blocks": True}:
            raise ValidationError("Unsupported aggregation contract.")
        if self.metric.unit == "ordinal" and self.aggregation["strategy"] != "ordinal":
            raise ValidationError("Progress is ordinal, never a quality pass rate.")
        if self.metric.unit != "ordinal" and self.aggregation["strategy"] != "diagnostic":
            raise ValidationError("Quality diagnostics cannot become progress scores.")
        if self.method == "model" and (not self.model_configuration_id or not self.prompt_manifest):
            raise ValidationError("Model evaluator versions require frozen model and prompt configuration.")
        if self.method != "model" and (self.model_configuration_id or self.prompt_manifest):
            raise ValidationError("Non-model evaluators cannot declare provider configuration.")

    def __str__(self):
        return f"{self.evaluator.key}@{self.version}"


class EvaluatorApproval(FrozenRecord):
    evaluator_version = models.ForeignKey(EvaluatorVersion, on_delete=models.PROTECT, related_name="approvals")
    decision = models.CharField(max_length=20, choices=[("approved", "Approved"), ("rejected", "Rejected"), ("superseded", "Superseded")])
    reason = models.TextField()
    calibration_evidence = models.JSONField(default=dict, blank=True)

    def clean(self):
        if self.decision == "approved" and not self.calibration_evidence:
            raise ValidationError("Approval requires calibration evidence; seeds are not decision-grade.")


class EvaluationResult(FrozenRecord):
    evaluator_version = models.ForeignKey(EvaluatorVersion, on_delete=models.PROTECT, related_name="results")
    evaluated_run = models.ForeignKey("executions.LLMRun", on_delete=models.PROTECT, related_name="evaluations")
    output_hash = models.CharField(max_length=64, validators=[HASH])
    input_manifest = models.JSONField()
    input_manifest_hash = models.CharField(max_length=64, validators=[HASH])
    criterion_results = models.JSONField(default=list, blank=True)
    summary = models.JSONField()
    progress_score = models.PositiveSmallIntegerField(null=True, blank=True)
    grader_run = models.ForeignKey("executions.LLMRun", null=True, blank=True, on_delete=models.PROTECT, related_name="graded_evaluations")
    rationale = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(progress_score__isnull=True) | models.Q(progress_score__gte=1, progress_score__lte=5), name="eval_progress_one_to_five")]

    def full_clean(self, *args, **kwargs):
        # Django IntegerField coercion would otherwise turn True or 3.5 into an
        # apparently valid ordinal. Reject before normalizing field values.
        if self.progress_score is not None and type(self.progress_score) is not int:
            raise ValidationError("Progress requires an integer, not a coerced value.")
        return super().full_clean(*args, **kwargs)

    def clean(self):
        from .validation import summarize, validate_results
        from executions.models import LLMRun
        # Model-level validation also uses persisted identities, protecting
        # direct creates as well as writes through record_result().
        run = LLMRun.objects.select_related("trace__workflow_version__workflow").get(pk=self.evaluated_run_id)
        version = EvaluatorVersion.objects.select_related("metric", "evaluator").get(pk=self.evaluator_version_id)
        self.evaluated_run = run
        self.evaluator_version = version
        if run.status != "succeeded" or self.output_hash != run.output_hash:
            raise ValidationError("Evaluation must match a successful run's frozen output hash.")
        workflow = run.trace.workflow_version.workflow.key
        if workflow not in version.applicability["workflows"]:
            raise ValidationError("Evaluator does not apply to this workflow.")
        if not isinstance(self.input_manifest, dict) or self.input_manifest_hash != canonical_hash(self.input_manifest):
            raise ValidationError("Input manifest hash mismatch.")
        if self.input_manifest.get("evaluated_run") != str(run.pk) or self.input_manifest.get("output_hash") != self.output_hash:
            raise ValidationError("Input manifest identifies a different target.")
        if self.input_manifest.get("evaluator_hash") != version.content_hash:
            raise ValidationError("Input manifest identifies a different evaluator.")
        if self.input_manifest.get("rubric_key") != version.applicability.get("rubric_key"):
            raise ValidationError("Explicit rubric applicability does not match the evaluator.")
        validate_results(version.rubric, self.criterion_results, self.input_manifest, version.required_inputs, actor_label=self.actor_label)
        if self.summary != summarize(version.rubric, self.criterion_results):
            raise ValidationError("Summary does not match criterion results.")
        if version.metric.unit == "ordinal":
            required_ids = {c["id"] for c in version.rubric["criteria"] if c["severity"] != "optional"}
            completed_ids = {r["id"] for r in self.criterion_results if r["status"] in {"pass", "fail"}}
            judgeable = bool(required_ids) and required_ids <= completed_ids
            if not judgeable and self.progress_score is not None:
                raise ValidationError("An unjudgeable progress assessment must abstain without a score.")
            if judgeable and (type(self.progress_score) is not int or not 1 <= self.progress_score <= 5):
                raise ValidationError("Progress requires an integer score from 1 to 5.")
        elif self.progress_score is not None:
            raise ValidationError("Quality checks cannot produce progress scores.")
        if version.method == "model":
            if self.grader_run_id:
                self.grader_run = LLMRun.objects.get(pk=self.grader_run_id)
            if not self.grader_run_id or self.grader_run.purpose != "evaluation" or self.grader_run.status != "succeeded" or self.grader_run.parent_run_id != run.pk or self.grader_run.trace_id != run.trace_id:
                raise ValidationError("Model evaluation requires its successful measured child run.")
            if self.grader_run.model_configuration_id != version.model_configuration_id or self.grader_run.prompt_revision_manifest != version.prompt_manifest:
                raise ValidationError("Grader configuration does not match the evaluator version.")
        elif self.grader_run_id:
            raise ValidationError("Non-model evaluations must not fabricate grader runs.")
        if self.supersedes_id:
            self.supersedes = EvaluationResult.objects.select_related("evaluator_version").get(pk=self.supersedes_id)
            if self.supersedes.evaluated_run_id != run.pk or self.supersedes.evaluator_version.evaluator_id != version.evaluator_id:
                raise ValidationError("Corrections must retain the target and evaluator identity.")
