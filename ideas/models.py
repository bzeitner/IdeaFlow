from datetime import timedelta
from string import Formatter

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from pgvector.django import VectorField

STAR_CHOICES = [(i, f"{i} star{'s' if i != 1 else ''}") for i in range(1, 6)]

# The one email that's always fully provisioned — everyone else starts with no roles.
STANDING_ADMIN_EMAIL = "bzeitner@gmail.com"

# Feed caps per idea, and how many agent runs an idea gets before it pauses for
# a human (adding a next action or clicking "Continue work").
FEED_CAP = 5
RESEARCH_FEED_CAP = 10
AGENT_RUNS_BEFORE_FEEDBACK = 2
# Most child ideas an agent may create under one parent (it can suggest more
# to a human beyond that).
AGENT_CHILD_LIMIT = 5

hex_color = RegexValidator(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    "Enter a hex color such as #24509b.",
)


class Status(models.TextChoices):
    """Structural — each value is a tab with its own route and template."""

    CURRENT = "current", "Current"
    TRACKING = "tracking", "Tracking"
    ARCHIVED = "archived", "Archived"


class RelationType(models.TextChoices):
    RELATED_TO = "related_to", "Related to"
    DEPENDS_ON = "depends_on", "Depends on"
    ENABLES = "enables", "Enables"
    ALTERNATIVE_TO = "alternative_to", "Alternative to"
    COMPETES_WITH = "competes_with", "Competes with"
    SUPPORTS = "supports", "Supports"
    CONTRADICTS = "contradicts", "Contradicts"
    DUPLICATES = "duplicates", "Duplicates"
    INSPIRED_BY = "inspired_by", "Inspired by"


class RelationProvenance(models.TextChoices):
    HUMAN = "human", "Human"
    AGENT = "agent", "Agent"
    IMPORTED = "imported", "Imported"


class LookupBase(models.Model):
    """Shared behaviour for the admin-managed dropdown lists."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True, blank=True)
    color = models.CharField(
        max_length=7,
        default="#44506a",
        validators=[hex_color],
        help_text="Hex color for this option's pill, e.g. #24509b.",
    )
    order = models.PositiveIntegerField(
        default=0, help_text="Position in the dropdown. Lower shows first."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive options stay on existing ideas but drop out of the dropdown.",
    )

    class Meta:
        abstract = True
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            self.slug = slugify(self.name)[:60]
        super().save(*args, **kwargs)

    @property
    def tint(self):
        """Translucent version of `color`, for pill backgrounds."""
        return f"{self.color}1f"


class Category(LookupBase):
    """Project, Side Project, Passive Income, Research Effort, Focus Project — editable in admin."""

    is_research = models.BooleanField(
        default=False,
        help_text="Research-type categories let their ideas track more feeds "
        "(10 instead of 5).",
    )

    class Meta(LookupBase.Meta):
        verbose_name_plural = "categories"


class Stage(LookupBase):
    """Where an idea sits in its lifecycle — the 'current state' on the tracking tab."""

    pass


class Idea(models.Model):
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="ideas_created",
        on_delete=models.SET_NULL,
        help_text="Owner responsible for this idea.",
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="ideas"
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.SET_NULL,
        help_text="Optional parent idea this is a sub-idea of (e.g. Passive "
        "Income → a specific SaaS or rental).",
    )
    interest_level = models.PositiveSmallIntegerField(choices=STAR_CHOICES, default=3)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.CURRENT
    )
    is_public = models.BooleanField(
        default=False,
        help_text="Public ideas are listed on the home page and readable by any "
        "signed-in user (they still can't edit without the tab's role).",
    )
    stage = models.ForeignKey(
        Stage, on_delete=models.PROTECT, related_name="ideas", null=True, blank=True
    )
    rank = models.PositiveIntegerField(
        default=0, help_text="Manual ordering within a tab. Lower sorts first."
    )
    notes = models.TextField(blank=True)
    next_action = models.TextField(
        blank=True,
        help_text="The active (first) item in the queued next actions.",
    )
    next_actions = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered queue of next actions; the first item is active.",
    )
    exec_summary = models.TextField(
        blank=True,
        help_text="Human-readable summary of the latest effort's outcome and "
        "recommended next steps (kept current by agents).",
    )
    repo = models.CharField(
        max_length=200,
        blank=True,
        help_text="Target GitHub repo (owner/name or URL) an agent may branch "
        "and open PRs against.",
    )
    agent_runs_since_feedback = models.PositiveIntegerField(
        default=0,
        help_text="Agent runs logged since the last human feedback; the idea "
        "pauses once it reaches the limit.",
    )
    feed_limit_override = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Manually cap how many feeds this idea keeps. Leave blank to "
        "use the default (5, or 10 for research categories).",
    )
    repeat_enabled = models.BooleanField(default=False)
    repeat_paused = models.BooleanField(default=False)
    repeat_goal = models.TextField(
        blank=True,
        help_text="Measurable goal for each repeat run, such as finding local job leads.",
    )
    repeat_target_count = models.PositiveSmallIntegerField(default=5)
    repeat_interval_days = models.PositiveSmallIntegerField(default=1)
    last_repeat_run_at = models.DateTimeField(null=True, blank=True)
    proposed_by_agent = models.BooleanField(
        default=False,
        help_text="Created by a research agent (child ideas). Counts toward the "
        "per-parent agent child limit.",
    )
    suggested_children = models.TextField(
        blank=True,
        help_text="Child ideas an agent suggested for a human to create (used "
        "when the agent isn't allowed to create them itself).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank", "-interest_level", "-updated_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("ideas:detail", args=[self.pk])

    @property
    def stars(self):
        return "★" * self.interest_level + "☆" * (5 - self.interest_level)

    @property
    def feed_cap(self):
        """How many feeds this idea keeps: a manual override if set, otherwise
        more for research-type categories."""
        if self.feed_limit_override:
            return self.feed_limit_override
        return RESEARCH_FEED_CAP if self.category.is_research else FEED_CAP

    @property
    def is_paused(self):
        """True once agents have worked it enough times without human feedback."""
        return self.agent_runs_since_feedback >= AGENT_RUNS_BEFORE_FEEDBACK

    @property
    def is_archived(self):
        return self.status == Status.ARCHIVED

    @property
    def open_question_count(self):
        """Number of research questions still awaiting human input."""
        return sum(
            len(entry.unanswered_question_items)
            for entry in self.research_entries.all()
        )

    @property
    def next_action_queue(self):
        queue = [str(item).strip() for item in self.next_actions if str(item).strip()]
        if not queue and self.next_action.strip():
            return [self.next_action.strip()]
        return queue

    @property
    def repeat_is_due(self):
        if not self.repeat_enabled or self.repeat_paused or self.is_archived:
            return False
        if not self.last_repeat_run_at:
            return True
        return self.last_repeat_run_at <= timezone.now() - timedelta(
            days=self.repeat_interval_days
        )

    def replace_active_next_action(self, value):
        """Replace the queue head while retaining actions queued behind it."""
        value = (value or "").strip()
        queue = self.next_action_queue
        if value:
            if queue:
                queue[0] = value
            else:
                queue = [value]
        elif queue:
            queue.pop(0)
        self.next_actions = queue
        self.next_action = queue[0] if queue else ""

    def enqueue_next_action(self, value):
        value = (value or "").strip()
        if not value:
            return False
        queue = self.next_action_queue
        if value in queue:
            return False
        queue.append(value)
        self.next_actions = queue
        self.next_action = queue[0]
        return True

    @property
    def pr_url(self):
        """URL of a pull request linked to this idea (from a resource), if any —
        surfaced for manual review after an agent opens/reviews a PR."""
        for r in self.resources.all():
            if "/pull/" in r.url or "pr" in (r.label or "").lower():
                return r.url
        return ""


class IdeaRelation(models.Model):
    SYMMETRIC_TYPES = {
        RelationType.RELATED_TO,
        RelationType.ALTERNATIVE_TO,
        RelationType.COMPETES_WITH,
        RelationType.CONTRADICTS,
        RelationType.DUPLICATES,
    }
    source = models.ForeignKey(Idea, related_name="outgoing_relations", on_delete=models.CASCADE)
    target = models.ForeignKey(Idea, related_name="incoming_relations", on_delete=models.CASCADE)
    relation_type = models.CharField(max_length=24, choices=RelationType.choices)
    description = models.TextField(blank=True)
    confidence = models.PositiveSmallIntegerField(choices=STAR_CHOICES, default=5)
    provenance = models.CharField(max_length=16, choices=RelationProvenance.choices, default=RelationProvenance.HUMAN)
    created_by = models.ForeignKey(User, null=True, blank=True, related_name="idea_relations_created", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source_id", "relation_type", "target_id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "target", "relation_type"], name="unique_typed_idea_relation"),
            models.CheckConstraint(condition=~models.Q(source=models.F("target")), name="idea_relation_not_self"),
        ]

    def __str__(self):
        return f"{self.source} {self.get_relation_type_display()} {self.target}"

    def clean(self):
        super().clean()
        if self.source_id == self.target_id:
            raise ValidationError("An idea cannot relate to itself.")
        if self.relation_type == RelationType.DEPENDS_ON and self._creates_dependency_cycle():
            raise ValidationError("This dependency would create a cycle.")

    def _creates_dependency_cycle(self):
        if not self.source_id or not self.target_id:
            return False
        frontier, visited = {self.target_id}, set()
        while frontier:
            if self.source_id in frontier:
                return True
            visited.update(frontier)
            frontier = set(
                IdeaRelation.objects.filter(source_id__in=frontier, relation_type=RelationType.DEPENDS_ON)
                .exclude(pk=self.pk).exclude(target_id__in=visited)
                .values_list("target_id", flat=True)
            )
        return False

    def save(self, *args, **kwargs):
        if self.relation_type in self.SYMMETRIC_TYPES and self.source_id and self.target_id:
            if self.source_id > self.target_id:
                self.source_id, self.target_id = self.target_id, self.source_id
        self.full_clean()
        return super().save(*args, **kwargs)


class GraphRevision(models.Model):
    revision = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class GraphAccessCapability(models.Model):
    """Short-lived, read-only authorization for the isolated Graph Lab UI."""

    token_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(User, related_name="graph_capabilities", on_delete=models.CASCADE)
    scope = models.CharField(max_length=32, default="graph:read")
    filters = models.JSONField(default=dict, blank=True)
    graph_revision = models.PositiveBigIntegerField(default=0)
    request_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["expires_at"], name="graph_cap_expiry_idx")]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()


class SemanticStatus(models.TextChoices):
    STALE = "stale", "Stale"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class SemanticGraphSettings(models.Model):
    """Singleton settings editable by administrators for semantic enrichment."""

    auto_accept_confidence_percent = models.PositiveSmallIntegerField(
        default=90,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Relationships with confidence strictly greater than this percentage are accepted automatically.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Semantic graph settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        settings_row, _created = cls.objects.get_or_create(pk=1)
        return settings_row


class PromptRevisionStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"


class PromptTemplate(models.Model):
    key = models.SlugField(max_length=100, unique=True, help_text="Stable identifier used by agent code to load this prompt.")
    name = models.CharField(max_length=200, help_text="Human-readable name shown in prompt management.")
    description = models.TextField(blank=True, help_text="What this prompt controls, when it runs, and its intended outcome.")
    variables = models.JSONField(default=list, blank=True, help_text="Documented placeholder names supplied by the runtime.")
    is_active = models.BooleanField(default=True, help_text="Inactive prompts remain archived but cannot be loaded for execution.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "key"]

    def __str__(self):
        return self.name

    @property
    def approved_revision(self):
        return self.revisions.filter(status=PromptRevisionStatus.APPROVED).order_by("-version").first()


class PromptRevision(models.Model):
    template = models.ForeignKey(PromptTemplate, related_name="revisions", on_delete=models.CASCADE, help_text="Prompt whose history this immutable revision belongs to.")
    version = models.PositiveIntegerField(blank=True, help_text="Monotonically increasing version number within this prompt.")
    content = models.TextField(help_text="Complete prompt text. Approved text is what agents execute.")
    status = models.CharField(max_length=16, choices=PromptRevisionStatus.choices, default=PromptRevisionStatus.PROPOSED, help_text="Only approved revisions are eligible for agent execution.")
    change_summary = models.TextField(blank=True, help_text="Why this change is proposed and its expected behavioral impact.")
    created_by = models.ForeignKey(User, null=True, blank=True, related_name="prompt_revisions_created", on_delete=models.SET_NULL)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, related_name="prompt_revisions_reviewed", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["template__name", "-version"]
        constraints = [models.UniqueConstraint(fields=["template", "version"], name="unique_prompt_revision_version")]

    def __str__(self):
        return f"{self.template.name} v{self.version} ({self.get_status_display()})"

    def clean(self):
        super().clean()
        try:
            used = {
                field_name.split(".", 1)[0].split("[", 1)[0]
                for _literal, field_name, _format_spec, _conversion in Formatter().parse(self.content)
                if field_name
            }
        except ValueError as exc:
            raise ValidationError({"content": f"Invalid prompt placeholder syntax: {exc}"}) from exc
        allowed = set(self.template.variables or [])
        unknown = sorted(used - allowed)
        if unknown:
            raise ValidationError({"content": f"Undocumented placeholder(s): {', '.join(unknown)}."})

    def save(self, *args, **kwargs):
        if self.pk:
            original = PromptRevision.objects.get(pk=self.pk)
            if original.content != self.content or original.template_id != self.template_id:
                raise ValidationError("Prompt revision content and ownership are immutable; create a proposal instead.")
        elif not self.version:
            latest = PromptRevision.objects.filter(template=self.template).order_by("-version").values_list("version", flat=True).first() or 0
            self.version = latest + 1
        super().save(*args, **kwargs)

    @transaction.atomic
    def approve(self, user):
        if self.status != PromptRevisionStatus.PROPOSED:
            raise ValidationError("Only proposed prompt revisions can be approved.")
        self.full_clean()
        PromptRevision.objects.filter(template=self.template, status=PromptRevisionStatus.APPROVED).update(status=PromptRevisionStatus.SUPERSEDED)
        self.status = PromptRevisionStatus.APPROVED
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    def reject(self, user):
        if self.status != PromptRevisionStatus.PROPOSED:
            raise ValidationError("Only proposed prompt revisions can be rejected.")
        self.status = PromptRevisionStatus.REJECTED
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "reviewed_at"])


class IdeaSemanticState(models.Model):
    idea = models.OneToOneField(Idea, related_name="semantic_state", on_delete=models.CASCADE)
    content_hash = models.CharField(max_length=64, blank=True)
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    embedding_model = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=SemanticStatus.choices, default=SemanticStatus.STALE)
    error = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class SuggestionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"


class IdeaRelationSuggestion(models.Model):
    analyzed_idea = models.ForeignKey(Idea, related_name="semantic_analyses", on_delete=models.CASCADE)
    source = models.ForeignKey(Idea, related_name="outgoing_relation_suggestions", on_delete=models.CASCADE)
    target = models.ForeignKey(Idea, related_name="incoming_relation_suggestions", on_delete=models.CASCADE)
    relation_type = models.CharField(max_length=24, choices=RelationType.choices)
    description = models.TextField(blank=True)
    evidence = models.TextField(blank=True)
    confidence = models.FloatField(default=0.5)
    similarity = models.FloatField(default=0.0)
    status = models.CharField(max_length=16, choices=SuggestionStatus.choices, default=SuggestionStatus.PENDING)
    source_content_hash = models.CharField(max_length=64)
    target_content_hash = models.CharField(max_length=64)
    classifier_model = models.CharField(max_length=100)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, related_name="relation_suggestions_reviewed", on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    accepted_relation = models.ForeignKey(IdeaRelation, null=True, blank=True, related_name="originating_suggestions", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-confidence", "-similarity", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["source", "target", "relation_type"], name="unique_typed_relation_suggestion"),
            models.CheckConstraint(condition=~models.Q(source=models.F("target")), name="relation_suggestion_not_self"),
        ]

    def __str__(self):
        return f"{self.source} {self.get_relation_type_display()} {self.target} ({self.get_status_display()})"


class Resource(models.Model):
    idea = models.ForeignKey(Idea, related_name="resources", on_delete=models.CASCADE)
    label = models.CharField(max_length=200, blank=True)
    url = models.URLField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.label or self.url

    @property
    def display(self):
        return self.label or self.url


class AIModel(LookupBase):
    """The AI model used for a research entry — editable in admin."""

    TIER_CHOICES = [
        ("light", "Light"),
        ("standard", "Standard"),
        ("heavy", "Heavy"),
    ]
    tier = models.CharField(
        max_length=10,
        choices=TIER_CHOICES,
        default="standard",
        help_text="Rough capability/cost tier, for routing cheap tasks (e.g. "
        "summaries) to lighter models.",
    )

    class Meta(LookupBase.Meta):
        verbose_name = "AI model"


class ResearchEntry(models.Model):
    idea = models.ForeignKey(
        Idea, related_name="research_entries", on_delete=models.CASCADE
    )
    topic = models.CharField(max_length=200)
    focus = models.CharField(max_length=200, blank=True)
    context = models.TextField(blank=True)
    open_questions = models.JSONField(
        default=list,
        blank=True,
        help_text="Specific questions the next agent run needs a human to answer.",
    )
    question_answers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Human answers keyed by the open question's zero-based index.",
    )
    occurred_at = models.DateTimeField(
        default=timezone.now, help_text="When the research happened."
    )
    effort = models.PositiveSmallIntegerField(choices=STAR_CHOICES, default=3)
    model = models.ForeignKey(
        AIModel, related_name="research_entries", on_delete=models.PROTECT
    )
    execution_provider = models.CharField(max_length=32, blank=True)
    execution_model = models.CharField(max_length=100, blank=True)
    quality = models.PositiveSmallIntegerField(choices=STAR_CHOICES, default=3)
    tokens_used = models.PositiveIntegerField(
        null=True, blank=True, help_text="Approximate tokens used, if known."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name_plural = "research entries"

    def __str__(self):
        return self.topic

    @property
    def effort_stars(self):
        return "★" * self.effort + "☆" * (5 - self.effort)

    @property
    def quality_stars(self):
        return "★" * self.quality + "☆" * (5 - self.quality)

    @property
    def open_question_items(self):
        answers = self.question_answers if isinstance(self.question_answers, dict) else {}
        return [
            {"index": index, "question": question, "answer": answers.get(str(index), "")}
            for index, question in enumerate(self.open_questions)
            if str(question).strip()
        ]

    @property
    def unanswered_question_items(self):
        return [item for item in self.open_question_items if not item["answer"].strip()]


class RepeatResultStatus(models.TextChoices):
    NEW = "new", "New"
    INTERESTED = "interested", "Interested"
    ACTIONED = "actioned", "Applied / Actioned"
    DISMISSED = "dismissed", "Dismissed"


class RepeatResult(models.Model):
    idea = models.ForeignKey(Idea, related_name="repeat_results", on_delete=models.CASCADE)
    title = models.CharField(max_length=300)
    url = models.URLField(max_length=1000, blank=True)
    details = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=RepeatResultStatus.choices, default=RepeatResultStatus.NEW
    )
    found_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-found_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["idea", "url"],
                condition=~models.Q(url=""),
                name="unique_repeat_result_url_per_idea",
            )
        ]

    def __str__(self):
        return self.title


class WeeklySummary(models.Model):
    period_start = models.DateField()
    period_end = models.DateField()
    title = models.CharField(max_length=200)
    content = models.TextField()
    model = models.CharField(max_length=100, blank=True)
    execution_provider = models.CharField(max_length=32, blank=True)
    tokens_used = models.PositiveIntegerField(null=True, blank=True)
    metrics = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured task, pull-request, and token metrics for the reporting period.",
    )
    generated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["period_start", "period_end"],
                name="unique_weekly_summary_period",
            )
        ]

    def __str__(self):
        return self.title


class Feed(models.Model):
    """An RSS/Atom feed tracked once and shared across ideas, so it's downloaded
    and its entries summarized a single time regardless of how many ideas
    reference it or how often the ingesting agent runs."""

    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(
        default=True, help_text="Inactive feeds are skipped by refresh_feeds."
    )
    # Idea associations live on the IdeaFeed model (with a per-idea rating);
    # reach them via feed.idea_feeds / idea.idea_feeds.
    # Conditional-GET bookkeeping so an unchanged feed isn't re-downloaded.
    etag = models.CharField(max_length=300, blank=True)
    last_modified = models.CharField(max_length=100, blank=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    backfill_cutoff = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Entries published before this are skipped on every ingest, not "
            "just the first. Set once, at creation, to 30 days back."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "url"]

    def save(self, *args, **kwargs):
        if self.pk is None and self.backfill_cutoff is None:
            self.backfill_cutoff = timezone.now() - timedelta(days=30)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title or self.url


class FeedItem(models.Model):
    """One entry in a feed. (feed, guid) is unique, so each entry is ingested —
    and summarized — exactly once."""

    feed = models.ForeignKey(Feed, related_name="items", on_delete=models.CASCADE)
    guid = models.CharField(
        max_length=500, help_text="Stable per-entry id (feed guid/id, else link)."
    )
    link = models.URLField(max_length=500, blank=True)
    title = models.CharField(max_length=300, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    content = models.TextField(
        blank=True,
        help_text=(
            "The entry body as published, truncated on ingest. Kept so a "
            "scoring agent can judge the item without re-fetching the page."
        ),
    )

    # Filled once, by the ingesting agent.
    summary = models.TextField(blank=True)
    summary_model = models.ForeignKey(
        AIModel,
        related_name="feed_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    summarized_at = models.DateTimeField(null=True, blank=True)
    # Your personal ratings, set from the admin (or the feed UI).
    interest = models.PositiveSmallIntegerField(
        choices=STAR_CHOICES,
        null=True,
        blank=True,
        help_text="Your 1-5 personal-interest rating.",
    )
    info_value = models.PositiveSmallIntegerField(
        choices=STAR_CHOICES,
        null=True,
        blank=True,
        help_text="Your 1-5 information-value rating.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["feed", "guid"], name="unique_feed_guid")
        ]

    def __str__(self):
        return self.title or self.guid

    @property
    def is_summarized(self):
        return self.summarized_at is not None

    @property
    def safe_link(self):
        """The link only if it's an http(s) URL — feed content is untrusted, so
        never render a javascript:/data: scheme as a clickable href."""
        from urllib.parse import urlsplit

        return self.link if urlsplit(self.link or "").scheme in ("http", "https") else ""


class FeedItemAssessment(models.Model):
    """An agent's idea-specific judgment of one globally summarized item."""

    idea = models.ForeignKey(
        Idea, related_name="feed_item_assessments", on_delete=models.CASCADE
    )
    item = models.ForeignKey(
        FeedItem, related_name="assessments", on_delete=models.CASCADE
    )
    usefulness = models.PositiveSmallIntegerField(
        choices=STAR_CHOICES,
        help_text="How useful this feed item is to this specific idea (1-5).",
    )
    relevance_note = models.TextField(
        blank=True,
        help_text="Optional idea-specific explanation; the global summary stays neutral.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-usefulness", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["idea", "item"], name="unique_idea_feed_item_assessment"
            )
        ]

    def __str__(self):
        return f"{self.idea} · {self.item} ({self.usefulness}/5)"


class IdeaFeed(models.Model):
    """Association of a feed to an idea, with a per-idea relevance rating. Each
    idea keeps only its top `feed_cap` feeds by this rating (see prune logic)."""

    idea = models.ForeignKey(Idea, related_name="idea_feeds", on_delete=models.CASCADE)
    feed = models.ForeignKey(Feed, related_name="idea_feeds", on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(
        choices=STAR_CHOICES,
        null=True,
        blank=True,
        help_text="How relevant this feed is to this idea (1-5); unrated sorts last.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-rating", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["idea", "feed"], name="unique_idea_feed")
        ]

    def __str__(self):
        return f"{self.idea} · {self.feed}"


class Profile(models.Model):
    """Per-user role flags. One tab role + admin + add-ideas, all independent."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="profile", on_delete=models.CASCADE
    )
    role_admin = models.BooleanField(
        default=False,
        help_text="Full access: Django admin, user management, and every tab.",
    )
    role_current = models.BooleanField(
        default=False, help_text="View and manage ideas in the Current tab."
    )
    role_tracking = models.BooleanField(
        default=False, help_text="View and manage ideas in the Tracking tab."
    )
    role_archive = models.BooleanField(
        default=False, help_text="View and manage ideas in the Archive tab."
    )
    role_add_ideas = models.BooleanField(
        default=False, help_text="Create new ideas."
    )
    role_graph = models.BooleanField(default=False, help_text="View the knowledge graph.")
    role_weekly_summary = models.BooleanField(
        default=False, help_text="View weekly portfolio executive summaries."
    )

    STATUS_ROLE = {
        Status.CURRENT: "role_current",
        Status.TRACKING: "role_tracking",
        Status.ARCHIVED: "role_archive",
    }

    def __str__(self):
        return self.user.get_username()

    def has_role(self, *names):
        return self.role_admin or any(getattr(self, name) for name in names)

    def can_manage_status(self, status):
        return self.has_role(self.STATUS_ROLE[status])

    @property
    def can_read_feeds(self):
        """Anyone who manages ideas can read + rate the shared feed items."""
        return self.has_role("role_current", "role_tracking", "role_archive")

    def save(self, *args, **kwargs):
        # role_admin is the only role with Django-admin implications, so keep
        # is_staff/is_superuser mirroring it instead of managing them separately.
        if self.user.is_staff != self.role_admin or self.user.is_superuser != self.role_admin:
            self.user.is_staff = self.role_admin
            self.user.is_superuser = self.role_admin
            self.user.save(update_fields=["is_staff", "is_superuser"])
        super().save(*args, **kwargs)


@receiver(post_save, sender=User)
def provision_profile(sender, instance, created, **kwargs):
    if not created:
        return
    # `manage.py createsuperuser` already set is_superuser — respect that as
    # admin intent too, so Profile.save()'s sync doesn't immediately undo it.
    is_admin = (
        instance.is_superuser
        or instance.email.strip().lower() == STANDING_ADMIN_EMAIL
    )
    Profile.objects.get_or_create(
        user=instance,
        defaults={
            "role_admin": is_admin,
            "role_current": is_admin,
            "role_tracking": is_admin,
            "role_archive": is_admin,
            "role_add_ideas": is_admin,
            "role_graph": is_admin,
        },
    )
