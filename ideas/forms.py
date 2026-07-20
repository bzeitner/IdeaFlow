from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory

from .models import Category, Idea, Resource, Stage


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
