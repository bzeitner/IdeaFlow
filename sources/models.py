from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class SourceKind(models.TextChoices):
    RSS = "rss", "RSS / Atom"
    WEB = "web", "Web"
    API = "api", "API"


class Source(models.Model):
    canonical_url = models.URLField(max_length=1000, unique=True)
    kind = models.CharField(max_length=16, choices=SourceKind.choices, default=SourceKind.RSS)
    title = models.CharField(max_length=300, blank=True)
    fetch_policy = models.JSONField(default=dict, blank=True)
    trust_metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    legacy_feed = models.OneToOneField(
        "ideas.Feed", null=True, blank=True, related_name="phase3_source",
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "canonical_url"]

    def __str__(self):
        return self.title or self.canonical_url


class Subscription(models.Model):
    source = models.ForeignKey(Source, related_name="subscriptions", on_delete=models.PROTECT)
    idea = models.ForeignKey("ideas.Idea", related_name="source_subscriptions", on_delete=models.CASCADE)
    intent = models.CharField(max_length=120, default="evidence")
    relevance_prior = models.FloatField(default=0.5)
    item_budget = models.PositiveIntegerField(default=25)
    is_paused = models.BooleanField(default=False)
    legacy_idea_feed = models.OneToOneField(
        "ideas.IdeaFeed", null=True, blank=True, related_name="phase3_subscription",
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "idea", "intent"], name="unique_source_subscription_intent")
        ]


class SourceItem(models.Model):
    source = models.ForeignKey(Source, related_name="items", on_delete=models.PROTECT)
    external_id = models.CharField(max_length=500)
    url = models.URLField(max_length=1000, blank=True)
    title = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64)
    published_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(default=timezone.now)
    eligible_for_processing = models.BooleanField(
        default=True,
        help_text="False for historical imports so Phase 3 never enqueues the legacy backlog.",
    )
    legacy_feed_item = models.OneToOneField(
        "ideas.FeedItem", null=True, blank=True, related_name="phase3_source_item",
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["-published_at", "-ingested_at"]
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_source_external_item")
        ]


class EvidenceDecision(models.TextChoices):
    PENDING = "pending", "Pending"
    INCLUDED = "included", "Included"
    FILTERED = "filtered", "Filtered"
    DISMISSED = "dismissed", "Dismissed"


class EvidenceCandidate(models.Model):
    source_item = models.ForeignKey(SourceItem, related_name="candidates", on_delete=models.PROTECT)
    subscription = models.ForeignKey(Subscription, related_name="candidates", on_delete=models.PROTECT)
    idea = models.ForeignKey("ideas.Idea", related_name="evidence_candidates", on_delete=models.CASCADE)
    deterministic_score = models.FloatField(default=0.0)
    llm_score = models.FloatField(null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    scoring_run = models.ForeignKey(
        "executions.LLMRun", null=True, blank=True, related_name="evidence_candidates",
        on_delete=models.SET_NULL,
    )
    decision = models.CharField(max_length=16, choices=EvidenceDecision.choices, default=EvidenceDecision.PENDING)
    exposed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_item", "idea"], name="unique_source_item_idea_candidate")
        ]


class EvidenceExperiment(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"

    key = models.SlugField(max_length=100, unique=True)
    hypothesis = models.TextField()
    primary_metric = models.CharField(max_length=100, default="useful")
    treatment_percent = models.PositiveSmallIntegerField(default=50)
    salt = models.CharField(max_length=128)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    enrollment_started_at = models.DateTimeField(null=True, blank=True)
    minimum_sample_size = models.PositiveIntegerField(default=100)
    guardrails = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(treatment_percent__lte=100),
                name="evidence_treatment_percent_lte_100",
            )
        ]

    def clean(self):
        super().clean()
        if not 1 <= self.treatment_percent <= 99:
            raise ValidationError({"treatment_percent": "Use a value between 1 and 99."})
        if self.state == self.State.RUNNING:
            if self.enrollment_started_at is None:
                raise ValidationError({"enrollment_started_at": "Running experiments require a start time."})
            if EvidenceExperiment.objects.filter(state=self.State.RUNNING).exclude(pk=self.pk).exists():
                raise ValidationError("Only one evidence experiment may enroll at a time.")


class EvidenceAssignment(models.Model):
    class Variant(models.TextChoices):
        CONTROL = "control", "Control"
        TREATMENT = "treatment", "Treatment"

    experiment = models.ForeignKey(EvidenceExperiment, related_name="assignments", on_delete=models.PROTECT)
    candidate = models.ForeignKey(EvidenceCandidate, related_name="assignments", on_delete=models.PROTECT)
    randomization_key = models.CharField(max_length=200)
    variant = models.CharField(max_length=16, choices=Variant.choices)
    assignment_hash = models.CharField(max_length=64)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["experiment", "randomization_key"], name="sticky_evidence_assignment"),
            models.UniqueConstraint(fields=["experiment", "candidate"], name="unique_experiment_candidate_assignment"),
        ]


class EvidenceAction(models.Model):
    class Action(models.TextChoices):
        USEFUL = "useful", "Useful"
        IRRELEVANT = "irrelevant", "Irrelevant"
        SAVED = "saved", "Saved"
        CITED = "cited", "Cited"
        ACTION_CREATED = "action_created", "Action created"
        DISMISSED = "dismissed", "Dismissed"

    candidate = models.ForeignKey(EvidenceCandidate, null=True, blank=True, related_name="actions", on_delete=models.SET_NULL)
    source_item = models.ForeignKey(SourceItem, related_name="actions", on_delete=models.PROTECT)
    idea = models.ForeignKey("ideas.Idea", related_name="evidence_actions", on_delete=models.CASCADE)
    action = models.CharField(max_length=24, choices=Action.choices)
    value = models.FloatField(default=1.0)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    attributed_run = models.ForeignKey(
        "executions.LLMRun", null=True, blank=True, related_name="evidence_actions",
        on_delete=models.SET_NULL,
    )
    legacy_assessment = models.OneToOneField(
        "ideas.FeedItemAssessment", null=True, blank=True, related_name="phase3_action",
        on_delete=models.SET_NULL,
    )
    occurred_at = models.DateTimeField(default=timezone.now)


class EvidenceObservation(models.Model):
    assignment = models.ForeignKey(EvidenceAssignment, related_name="observations", on_delete=models.PROTECT)
    action = models.ForeignKey(EvidenceAction, related_name="observations", on_delete=models.PROTECT)
    metric = models.CharField(max_length=100)
    value = models.FloatField()
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assignment", "action"], name="unique_assignment_action_observation")
        ]


class LegacyEntitySnapshot(models.Model):
    entity_type = models.CharField(max_length=40)
    legacy_id = models.PositiveBigIntegerField()
    content_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    provenance = models.CharField(max_length=40, default="legacy_import")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entity_type", "legacy_id"], name="unique_legacy_entity_snapshot")
        ]
