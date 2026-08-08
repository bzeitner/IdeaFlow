from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import AIModel, Category, Idea, ResearchEntry, Resource, Stage


class IdeaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Offer only active options, but never drop the one already on this idea —
        # deactivating a category shouldn't silently rewrite existing ideas on save.
        for name, model in (("category", Category), ("stage", Stage)):
            current = getattr(self.instance, f"{name}_id", None)
            self.fields[name].queryset = model.objects.filter(
                Q(is_active=True) | Q(pk=current)
            )

    class Meta:
        model = Idea
        fields = [
            "title",
            "category",
            "summary",
            "interest_level",
            "status",
            "stage",
            "rank",
            "notes",
            "next_action",
        ]
        labels = {
            "title": "Idea Title",
            "summary": "Idea Summary",
            "interest_level": "Interest Level",
            "next_action": "Next Action",
        }
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "next_action": forms.Textarea(attrs={"rows": 2}),
            "interest_level": forms.RadioSelect,
        }


ResourceFormSet = inlineformset_factory(
    Idea,
    Resource,
    fields=["label", "url"],
    extra=3,
    can_delete=True,
    widgets={
        "label": forms.TextInput(attrs={"placeholder": "Label (optional)"}),
        "url": forms.URLInput(attrs={"placeholder": "https://…"}),
    },
)


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


ResearchEntryFormSet = inlineformset_factory(
    Idea,
    ResearchEntry,
    form=ResearchEntryForm,
    extra=1,
    can_delete=True,
)
