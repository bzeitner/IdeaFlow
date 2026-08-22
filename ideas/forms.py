from django import forms
from django.conf import settings
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import Artifact, AIModel, Category, Idea, IdeaRelation, ResearchEntry, Resource, Stage, Status


class ParentIdeaSelect(forms.Select):
    """Expose candidate state so the browser can filter without another request."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-archived"] = str(instance.status == Status.ARCHIVED).lower()
            option["attrs"]["data-has-parent"] = str(bool(instance.parent_id)).lower()
        return option


class IdeaForm(forms.ModelForm):
    repeat_target_count = forms.IntegerField(
        required=False, min_value=1, initial=5, label="Target results per run"
    )
    repeat_interval_days = forms.IntegerField(
        required=False, min_value=1, initial=1, label="Run every (days)"
    )
    persona_stall_days = forms.IntegerField(
        required=False, min_value=1, initial=14, label="Review after no progress (days)"
    )
    include_archived_parents = forms.BooleanField(
        required=False, label="Include archived"
    )
    include_child_parents = forms.BooleanField(
        required=False, label="Include children"
    )

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Offer only active options, but never drop the one already on this idea —
        # deactivating a category shouldn't silently rewrite existing ideas on save.
        for name, model in (("category", Category), ("stage", Stage)):
            current = getattr(self.instance, f"{name}_id", None)
            self.fields[name].queryset = model.objects.filter(
                Q(is_active=True) | Q(pk=current)
            )
        # A parent can be any other idea except this one or its own descendants
        # (which would create a cycle).
        parents = Idea.objects.all()
        if self.instance and self.instance.pk:
            parents = parents.exclude(pk__in=self._descendant_ids(self.instance))
        self.fields["parent"].queryset = parents.order_by("title")
        self.fields["parent"].widget = ParentIdeaSelect(
            choices=self.fields["parent"].choices
        )
        current_parent = getattr(self.instance, "parent", None)
        if not self.is_bound and current_parent:
            self.fields["include_archived_parents"].initial = current_parent.status == Status.ARCHIVED
            self.fields["include_child_parents"].initial = bool(current_parent.parent_id)
        owner_id = getattr(owner, "pk", owner) or getattr(self.instance, "created_by_id", None)
        artifacts = Artifact.objects.none()
        if owner_id:
            artifacts = Artifact.objects.filter(idea__created_by_id=owner_id)
            if self.instance.pk:
                artifacts = artifacts.exclude(idea_id=self.instance.pk)
        self.fields["referenced_artifacts"].queryset = artifacts.select_related("idea")
        self.fields["referenced_artifacts"].label_from_instance = (
            lambda artifact: f"{artifact.idea.title} — {artifact.title}"
        )

    def clean(self):
        cleaned = super().clean()
        cleaned["repeat_target_count"] = cleaned.get("repeat_target_count") or 5
        cleaned["repeat_interval_days"] = cleaned.get("repeat_interval_days") or 1
        cleaned["persona_stall_days"] = cleaned.get("persona_stall_days") or 14
        if cleaned.get("repeat_enabled") and not (cleaned.get("repeat_goal") or "").strip():
            self.add_error("repeat_goal", "A repeatable task requires a measurable goal.")
        parent = cleaned.get("parent")
        if not parent:
            return cleaned
        if parent.status == Status.ARCHIVED and not self.cleaned_data.get("include_archived_parents"):
            self.add_error("parent", "Select “Include archived” to use an archived parent.")
        if parent.parent_id and not self.cleaned_data.get("include_child_parents"):
            self.add_error("parent", "Select “Include children” to use an idea that already has a parent.")
        return cleaned

    def save(self, commit=True):
        idea = super().save(commit=False)
        if "next_action" in self.cleaned_data:
            idea.replace_active_next_action(self.cleaned_data["next_action"])
        if commit:
            idea.save()
            self.save_m2m()
        return idea

    @staticmethod
    def _descendant_ids(idea):
        """The idea's own id plus every id beneath it, so none can be its parent."""
        ids = {idea.pk}
        frontier = [idea.pk]
        while frontier:
            kids = list(
                Idea.objects.filter(parent_id__in=frontier)
                .exclude(pk__in=ids)
                .values_list("pk", flat=True)
            )
            ids.update(kids)
            frontier = kids
        return ids

    class Meta:
        model = Idea
        fields = [
            "title",
            "category",
            "parent",
            "summary",
            "interest_level",
            "status",
            "stage",
            "rank",
            "notes",
            "next_action",
            "exec_summary",
            "repo",
            "is_public",
            "feed_limit_override",
            "repeat_enabled",
            "repeat_paused",
            "repeat_goal",
            "repeat_target_count",
            "repeat_interval_days",
            "persona_review_enabled",
            "persona_stall_days",
            "referenced_artifacts",
        ]
        labels = {
            "title": "Idea Title",
            "summary": "Idea Summary",
            "interest_level": "Interest Level",
            "next_action": "Active Next Action",
            "exec_summary": "Latest Effort Summary",
            "repo": "Target repo (owner/name or URL)",
            "is_public": "Public (visible to everyone signed in)",
            "feed_limit_override": "Feed limit override",
            "repeat_enabled": "Repeat this task",
            "repeat_paused": "Pause repeat runs",
            "repeat_goal": "Goal for each run",
            "repeat_target_count": "Target results per run",
            "repeat_interval_days": "Run every (days)",
            "persona_review_enabled": "Enable stalled persona reviews",
            "persona_stall_days": "Review after no progress (days)",
            "referenced_artifacts": "Referenced artifacts",
        }
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "next_action": forms.Textarea(attrs={"rows": 2}),
            "exec_summary": forms.Textarea(attrs={"rows": 5}),
            "repeat_goal": forms.Textarea(attrs={"rows": 3}),
            "interest_level": forms.RadioSelect,
            "referenced_artifacts": forms.CheckboxSelectMultiple,
        }


ResourceFormSet = inlineformset_factory(
    Idea,
    Resource,
    fields=["label", "url"],
    extra=1,
    can_delete=True,
    widgets={
        "label": forms.TextInput(attrs={"placeholder": "Label (optional)"}),
        "url": forms.URLInput(attrs={"placeholder": "https://…"}),
    },
)


class IdeaRelationForm(forms.ModelForm):
    class Meta:
        model = IdeaRelation
        fields = ["source", "relation_type", "target", "description", "confidence"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("source") == cleaned.get("target"):
            raise forms.ValidationError("An idea cannot relate to itself.")
        return cleaned


class ResearchEntryForm(forms.ModelForm):
    # Declared explicitly (rather than via Meta.widgets) because the datetime-local
    # input submits "YYYY-MM-DDTHH:MM", which isn't in Django's default input_formats.
    # Optional: a blank row defaults to "now" on save (see clean), so users aren't
    # forced to fill a timestamp on every entry.
    occurred_at = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Same active-options-plus-current-value rule as IdeaForm, so retiring
        # a model in admin doesn't silently rewrite entries already logged against it.
        current = getattr(self.instance, "model_id", None)
        self.fields["model"].queryset = AIModel.objects.filter(
            Q(is_active=True) | Q(pk=current)
        )
        # A new ResearchEntry() carries a full-precision `occurred_at` default from
        # the model, but the datetime-local widget only round-trips to the minute.
        # That mismatch makes an untouched extra row look "changed", so the formset
        # stops treating it as empty and demands topic/model — the add-idea bug.
        # Blank the initial for unsaved rows so an empty one stays genuinely empty.
        if self.instance.pk is None:
            self.initial["occurred_at"] = None

    def clean(self):
        cleaned = super().clean()
        # Optional field, but the model column is NOT NULL — supply the default
        # here (only reached for rows the formset considers non-empty).
        if not cleaned.get("occurred_at"):
            cleaned["occurred_at"] = timezone.now()
        return cleaned

    class Meta:
        model = ResearchEntry
        fields = [
            "topic",
            "focus",
            "context",
            "occurred_at",
            "model",
            "effort",
            "quality",
            "tokens_used",
        ]
        widgets = {
            "context": forms.Textarea(attrs={"rows": 2}),
            "effort": forms.RadioSelect,
            "quality": forms.RadioSelect,
        }


class ArtifactForm(forms.ModelForm):
    generated_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )

    def __init__(self, *args, idea, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["research_entry"].queryset = idea.research_entries.all()
        self.fields["research_entry"].label_from_instance = (
            lambda entry: f"#{entry.pk} — {entry.topic}"
        )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("file") and not cleaned.get("url"):
            raise forms.ValidationError("Upload a file or provide an external link.")
        uploaded = cleaned.get("file")
        if uploaded and uploaded.size > settings.IDEAFLOW_ARTIFACT_MAX_BYTES:
            self.add_error("file", "Artifact files must be 10 MB or smaller.")
        return cleaned

    class Meta:
        model = Artifact
        fields = [
            "title", "kind", "description", "file", "url", "generated_at", "research_entry"
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


ResearchEntryFormSet = inlineformset_factory(
    Idea,
    ResearchEntry,
    form=ResearchEntryForm,
    extra=1,
    can_delete=True,
)
