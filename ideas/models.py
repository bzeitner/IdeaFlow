from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

STAR_CHOICES = [(i, f"{i} star{'s' if i != 1 else ''}") for i in range(1, 6)]

# The one email that's always fully provisioned — everyone else starts with no roles.
STANDING_ADMIN_EMAIL = "bzeitner@gmail.com"

# Feed caps per idea, and how many agent runs an idea gets before it pauses for
# a human (adding a next action or clicking "Continue work").
FEED_CAP = 5
RESEARCH_FEED_CAP = 10
AGENT_RUNS_BEFORE_FEEDBACK = 3
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
        help_text="The single next step to take, once the idea has been researched.",
    )
    exec_summary = models.TextField(
        blank=True,
        help_text="Executive summary of the effort's current state (kept up to "
        "date by the review agent).",
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
    def pr_url(self):
        """URL of a pull request linked to this idea (from a resource), if any —
        surfaced for manual review after an agent opens/reviews a PR."""
        for r in self.resources.all():
            if "/pull/" in r.url or "pr" in (r.label or "").lower():
                return r.url
        return ""


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
    occurred_at = models.DateTimeField(
        default=timezone.now, help_text="When the research happened."
    )
    effort = models.PositiveSmallIntegerField(choices=STAR_CHOICES, default=3)
    model = models.ForeignKey(
        AIModel, related_name="research_entries", on_delete=models.PROTECT
    )
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "url"]

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
    usefulness = models.PositiveSmallIntegerField(
        choices=STAR_CHOICES,
        null=True,
        blank=True,
        help_text="The ingesting agent's 1-5 usefulness rating.",
    )

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
        },
    )
