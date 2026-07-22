from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory

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
        ]
        labels = {
            "title": "Idea Title",
            "summary": "Idea Summary",
            "interest_level": "Interest Level",
        }
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 3}),
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
    occurred_at = forms.DateTimeField(
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
