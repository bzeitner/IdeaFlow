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
    interest_level = models.PositiveSmallIntegerField(choices=STAR_CHOICES, default=3)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.CURRENT
    )
    stage = models.ForeignKey(
        Stage, on_delete=models.PROTECT, related_name="ideas", null=True, blank=True
    )
    rank = models.PositiveIntegerField(
        default=0, help_text="Manual ordering within a tab. Lower sorts first."
    )
    notes = models.TextField(blank=True)
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
