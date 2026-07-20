from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse

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
    interest_level = models.PositiveSmallIntegerField(
        choices=[(i, f"{i} star{'s' if i != 1 else ''}") for i in range(1, 6)],
        default=3,
    )
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
