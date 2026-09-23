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


class InteractionTarget(FrozenRecord):
    """Opaque business/user IDs retain history without preventing deletion."""
    actor_user_id = models.PositiveBigIntegerField()
    target_kind = models.CharField(max_length=20, choices=[("research", "Research"), ("weekly_summary", "Weekly summary")])
    target_id = models.PositiveBigIntegerField()
    output_hash = models.CharField(max_length=64, validators=[HASH])
    producing_run = models.ForeignKey("executions.LLMRun", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    run_output_hash = models.CharField(max_length=64, blank=True, validators=[HASH])
    source = models.CharField(max_length=40, default="browser")
    idempotency_key = models.CharField(max_length=64, unique=True, validators=[HASH])

    class Meta:
        abstract = True

    def clean(self):
        if self.actor_label != f"user:{self.actor_user_id}":
            raise ValidationError("Interaction actor must match its authenticated user.")
        if self.producing_run_id:
            from executions.models import LLMRun
            run = LLMRun.objects.get(pk=self.producing_run_id)
            if run.status != "succeeded" or run.output_hash != self.run_output_hash:
                raise ValidationError("Producing run provenance does not match.")
        elif self.run_output_hash:
            raise ValidationError("Unknown producing runs cannot have an output hash.")
        if self.source not in {"research_view", "weekly_summary_view", "research_edit", "human_service"}:
            raise ValidationError("Unsupported human interaction source.")


class EvaluationExposure(InteractionTarget):
    view_session = models.UUIDField()

    class Meta:
        indexes = [models.Index(fields=["actor_user_id", "target_kind", "target_id", "output_hash", "created_at"], name="eval_exposure_actor_target")]


class HumanFeedback(InteractionTarget):
    ACTIONS = [(value, value.title()) for value in
               ("accept", "reject", "useful", "save", "cite", "action", "irrelevant", "dismiss", "edit")]
    action = models.CharField(max_length=20, choices=ACTIONS)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=2000, blank=True)
    exposure = models.ForeignKey(EvaluationExposure, null=True, blank=True, on_delete=models.PROTECT)
    supersedes = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="correction")
    before_hash = models.CharField(max_length=64, blank=True, validators=[HASH])
    after_hash = models.CharField(max_length=64, blank=True, validators=[HASH])

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(rating__isnull=True) | models.Q(rating__gte=1, rating__lte=5), name="feedback_rating_one_to_five")]
        indexes = [models.Index(fields=["actor_user_id", "target_kind", "target_id", "output_hash", "created_at"], name="eval_feedback_actor_target")]

    def full_clean(self, *args, **kwargs):
        if self.rating is not None and type(self.rating) is not int:
            raise ValidationError("Rating must be an integer.")
        return super().full_clean(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.rating is not None and not 1 <= self.rating <= 5:
            raise ValidationError("Rating must be between 1 and 5.")
        for relation in ("exposure", "supersedes"):
            if getattr(self, relation + "_id"):
                model = EvaluationExposure if relation == "exposure" else HumanFeedback
                linked = model.objects.get(pk=getattr(self, relation + "_id"))
                if any(getattr(linked, key) != getattr(self, key) for key in
                       ("actor_user_id", "target_kind", "target_id", "output_hash", "producing_run_id", "run_output_hash")):
                    raise ValidationError("Interaction links must retain actor and exact output identity.")
                if relation == "supersedes" and (linked.action == "edit" or self.action == "edit"):
                    raise ValidationError("Edit facts cannot be corrected as judgments.")
                if relation == "supersedes" and HumanFeedback.objects.filter(supersedes_id=linked.pk).exists():
                    raise ValidationError("Correct the latest feedback revision instead.")
        if self.action == "edit":
            if self.source != "research_edit" or self.before_hash != self.output_hash or not self.after_hash or self.after_hash == self.before_hash:
                raise ValidationError("Edit feedback requires a real content change.")
        elif self.before_hash or self.after_hash:
            raise ValidationError("Only actual edits may include edit hashes.")


class FeedbackOutcomeLink(FrozenRecord):
    feedback = models.ForeignKey(HumanFeedback, on_delete=models.PROTECT, related_name="outcome_links")
    outcome = models.ForeignKey("executions.OutcomeEvent", on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["feedback", "outcome"], name="feedback_outcome_unique")]

    def clean(self):
        from executions.models import OutcomeEvent
        feedback = HumanFeedback.objects.get(pk=self.feedback_id)
        outcome = OutcomeEvent.objects.get(pk=self.outcome_id)
        from ideas.models import ResearchEntry
        entry = ResearchEntry.objects.filter(pk=feedback.target_id).first() if feedback.target_kind == "research" else None
        if not entry or outcome.idea_id != entry.idea_id or not feedback.producing_run_id or outcome.run_id != feedback.producing_run_id:
            raise ValidationError("Outcome must identify the same idea and producing run.")
        if self.actor_label != feedback.actor_label:
            raise ValidationError("Only the feedback actor can link an outcome.")


class EvaluationDataset(FrozenRecord):
    key = models.SlugField(max_length=100, unique=True)
    purpose = models.CharField(max_length=500)
    owner_user_id = models.PositiveBigIntegerField()
    access_classification = models.CharField(max_length=32, default='restricted')
    eligibility_policy = models.JSONField()
    redaction_policy = models.CharField(max_length=100)
    retention_days = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        permissions = [('operate_datasets', 'Operate protected evaluation datasets')]
        constraints = [models.CheckConstraint(condition=models.Q(retention_days__gte=1), name='dataset_retention_positive')]

    def clean(self):
        policy = self.eligibility_policy
        if (self.access_classification != 'restricted' or not isinstance(policy, dict)
                or set(policy) != {'workflows', 'allow_legacy'} or policy['workflows'] != ['research']
                or type(policy['allow_legacy']) is not bool):
            raise ValidationError('Restricted research datasets require an explicit allow_legacy policy.')


class DatasetCase(FrozenRecord):
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.PROTECT)
    case_key = models.SlugField(max_length=100)
    revision = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    origin = models.JSONField()
    rubric_assignments = models.JSONField()
    cohorts = models.JSONField()
    split = models.CharField(max_length=16, choices=[('development', 'Development'), ('held_out', 'Held out')])
    evidence_cutoff = models.DateTimeField()
    expires_at = models.DateTimeField()
    payload_hash = models.CharField(max_length=64, validators=[HASH])
    approval_hash = models.CharField(max_length=64, validators=[HASH])
    idempotency_key = models.CharField(max_length=200, unique=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['dataset', 'case_key', 'revision'], name='dataset_case_revision'),
                       models.CheckConstraint(condition=models.Q(revision__gte=1), name='dataset_revision_positive')]

    def audit_values(self):
        from datetime import timezone as datetime_timezone
        values = super().audit_values()
        for name in ('evidence_cutoff', 'expires_at'):
            value = getattr(self, name)
            values[name] = value.astimezone(datetime_timezone.utc).isoformat() if value else None
        return values

    def clean(self):
        from .datasets import validate_assignments
        validate_assignments(self.rubric_assignments)
        if not isinstance(self.cohorts, list) or not self.cohorts or not all(isinstance(c, str) and c.strip() for c in self.cohorts):
            raise ValidationError('Explicit cohort tags are required.')
        if not isinstance(self.origin, dict) or set(self.origin) != {'target_kind', 'target_id', 'output_hash', 'producing_run_id', 'run_output_hash'} or self.origin['target_kind'] != 'research':
            raise ValidationError('A frozen research origin identity is required.')
        if type(self.origin['target_id']) is not int or self.origin['target_id'] < 1:
            raise ValidationError('Origin must identify a research entry.')
        HASH(self.origin['output_hash'])
        if self.origin['producing_run_id']:
            UUID(self.origin['producing_run_id'])
            HASH(self.origin['run_output_hash'])
        elif self.origin['run_output_hash']:
            raise ValidationError('Unavailable provenance must not invent a run hash.')
        from django.utils import timezone
        if (timezone.is_naive(self.evidence_cutoff) or timezone.is_naive(self.expires_at)
                or self.evidence_cutoff > timezone.now() or self.expires_at <= self.evidence_cutoff):
            raise ValidationError('Case timestamps must be aware and ordered; cutoff cannot be in the future.')
        if self.supersedes_id:
            prior = DatasetCase.objects.get(pk=self.supersedes_id)
            if (prior.dataset_id, prior.case_key, prior.revision + 1, prior.split, prior.origin['target_id']) != (self.dataset_id, self.case_key, self.revision, self.split, self.origin['target_id']):
                raise ValidationError('Revision lineage must retain dataset, case identity, and split.')
        elif self.revision != 1:
            raise ValidationError('Later revisions require explicit lineage.')


class DatasetContentQuerySet(FrozenQuerySet):
    def delete(self):
        # Database guards require an existing immutable tombstone.
        return models.QuerySet.delete(self)


class DatasetCaseContent(models.Model):
    """Deletable protected content; immutable metadata lives in DatasetCase."""
    case = models.OneToOneField(DatasetCase, on_delete=models.PROTECT, primary_key=True, related_name='protected_content')
    payload = models.JSONField()
    objects = DatasetContentQuerySet.as_manager()

    def save(self, *args, **kwargs):
        from .datasets import validate_payload
        validate_payload(self.payload)
        if not self._state.adding or DatasetCaseTombstone.objects.filter(case_id=self.case_id).exists():
            raise ValidationError('Frozen content cannot be rewritten or restored after deletion.')
        if canonical_hash(self.payload) != self.case.payload_hash:
            raise ValidationError('Frozen content hash mismatch.')
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)


class DatasetCaseTombstone(FrozenRecord):
    case = models.OneToOneField(DatasetCase, on_delete=models.PROTECT)
    reason = models.CharField(max_length=24, choices=[('expired', 'Expired'), ('required_deletion', 'Required deletion')])


class DatasetSnapshot(FrozenRecord):
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.PROTECT)
    manifest = models.JSONField()
    idempotency_key = models.CharField(max_length=200, unique=True)

    def clean(self):
        if not isinstance(self.manifest, dict) or set(self.manifest) != {'schema_version', 'dataset_hash', 'sampling_rules', 'redaction_policy', 'cases'}:
            raise ValidationError('Invalid snapshot manifest.')
        dataset = EvaluationDataset.objects.get(pk=self.dataset_id)
        m = self.manifest
        if m['schema_version'] != 1 or m['dataset_hash'] != dataset.content_hash or m['redaction_policy'] != dataset.redaction_policy or not isinstance(m['sampling_rules'], dict) or not m['sampling_rules']:
            raise ValidationError('Snapshot policy mismatch.')
        rows = m['cases']
        if not isinstance(rows, list) or not rows or len(rows) > 200:
            raise ValidationError('Snapshots require 1–200 ordered case revisions.')
        keys = set()
        for item in rows:
            if not isinstance(item, dict) or set(item) != {'id', 'hash', 'payload_hash'}:
                raise ValidationError('Invalid snapshot case descriptor.')
            case = DatasetCase.objects.get(pk=item['id'], dataset_id=self.dataset_id)
            if case.case_key in keys or item != {'id': case.pk, 'hash': case.content_hash, 'payload_hash': case.payload_hash}:
                raise ValidationError('Duplicate case identity or snapshot hash mismatch.')
            keys.add(case.case_key)


class HumanCalibrationLabel(FrozenRecord):
    case = models.ForeignKey(DatasetCase, on_delete=models.PROTECT)
    evaluator_version = models.ForeignKey(EvaluatorVersion, on_delete=models.PROTECT)
    reviewer_user_id = models.PositiveBigIntegerField()
    criterion_results = models.JSONField()
    progress_score = models.PositiveSmallIntegerField(null=True, blank=True)
    supporting_refs = models.JSONField(default=list, blank=True)
    adjudicates = models.JSONField(default=list, blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(progress_score__isnull=True) | models.Q(progress_score__gte=1, progress_score__lte=5), name='dataset_label_score_range')]

    def full_clean(self, *args, **kwargs):
        if self.progress_score is not None and type(self.progress_score) is not int:
            raise ValidationError('Progress must be an integer.')
        return super().full_clean(*args, **kwargs)

    def clean(self):
        from .datasets import validate_label
        validate_label(self)


class CalibrationPlan(FrozenRecord):
    snapshot = models.ForeignKey(DatasetSnapshot, on_delete=models.PROTECT)
    human_version = models.ForeignKey(EvaluatorVersion, on_delete=models.PROTECT, related_name='human_calibration_plans')
    grader_version = models.ForeignKey(EvaluatorVersion, on_delete=models.PROTECT, related_name='model_calibration_plans')
    reviewer_ids = models.JSONField()
    thresholds = models.JSONField()
    budget = models.JSONField()
    execution_binding = models.JSONField()
    idempotency_key = models.CharField(max_length=200, unique=True)
    supersedes = models.OneToOneField(
        'self', null=True, blank=True, related_name='superseded_by', on_delete=models.PROTECT,
    )

    def clean(self):
        from .calibration import validate_plan
        validate_plan(self)


class CalibrationReview(FrozenRecord):
    plan = models.ForeignKey(CalibrationPlan, on_delete=models.PROTECT)
    case = models.ForeignKey(DatasetCase, on_delete=models.PROTECT)
    label = models.OneToOneField(HumanCalibrationLabel, on_delete=models.PROTECT)
    assessment = models.JSONField()
    role = models.CharField(max_length=16, choices=[('independent', 'Independent'), ('adjudication', 'Adjudication')])
    review_mode = models.CharField(max_length=40, choices=[
        ('independent_blinded_v1', 'Independent blinded'),
        ('model_assisted_error_audit_v1', 'Model-assisted error audit'),
    ], default='independent_blinded_v1')
    assisted_result = models.ForeignKey(
        'CaseEvaluationResult', null=True, blank=True, on_delete=models.PROTECT,
        related_name='assisted_reviews',
    )
    difference_manifest = models.JSONField(default=dict, blank=True)
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=200, unique=True)

    def clean(self):
        from .calibration import validate_review
        validate_review(self)


class CalibrationAttempt(FrozenRecord):
    plan = models.ForeignKey(CalibrationPlan, on_delete=models.PROTECT)
    case = models.ForeignKey(DatasetCase, on_delete=models.PROTECT)
    run = models.OneToOneField('executions.LLMRun', on_delete=models.PROTECT)
    request_hash = models.CharField(max_length=64, validators=[HASH])
    reserved_tokens = models.PositiveBigIntegerField()
    reserved_cost_micros = models.PositiveBigIntegerField()
    idempotency_key = models.CharField(max_length=200, unique=True)

    def clean(self):
        if self.case_id not in [r['id'] for r in self.plan.snapshot.manifest['cases']]:
            raise ValidationError('Attempt case is outside the approved snapshot.')
        if self.run.purpose != 'evaluation' or self.run.model_configuration_id != self.plan.grader_version.model_configuration_id:
            raise ValidationError('Attempt requires the approved measured evaluation run.')


class CaseEvaluationResult(FrozenRecord):
    attempt = models.OneToOneField(CalibrationAttempt, on_delete=models.PROTECT)
    assessment = models.JSONField()
    summary = models.JSONField()

    def clean(self):
        from .calibration import validate_assessment
        from .validation import summarize
        attempt = self.attempt
        validate_assessment(attempt.case, attempt.plan.grader_version, self.assessment)
        if attempt.run.parsed_output != {'assessment_hash':canonical_hash(self.assessment)}:
            raise ValidationError('Assessment does not match the measured response fingerprint.')
        if attempt.run.status != 'succeeded' or not attempt.run.schema_valid:
            raise ValidationError('Case results require a successful schema-valid grader run.')
        if self.summary != summarize(attempt.plan.grader_version.rubric, self.assessment['criterion_results']):
            raise ValidationError('Result summary differs from criterion diagnostics.')


class CalibrationReport(FrozenRecord):
    plan = models.ForeignKey(CalibrationPlan, on_delete=models.PROTECT)
    input_manifest = models.JSONField()
    metrics = models.JSONField()
    eligible = models.BooleanField(default=False)


class EvaluatorApprovalSupersession(FrozenRecord):
    approval = models.OneToOneField(EvaluatorApproval, related_name='supersession', on_delete=models.PROTECT)
    plan = models.ForeignKey(CalibrationPlan, on_delete=models.PROTECT)
    review = models.ForeignKey(CalibrationReview, on_delete=models.PROTECT)
    reason = models.TextField()

    def clean(self):
        if (self.approval.decision != 'approved'
                or self.approval.evaluator_version_id != self.plan.grader_version_id
                or self.approval.calibration_evidence.get('plan_hash') != self.plan.content_hash
                or self.review.plan_id != self.plan_id):
            raise ValidationError('Approval supersession must match the changed calibration evidence.')
