from django.test import TestCase

from ideas.forms import IdeaForm, ResearchEntryForm
from ideas.models import Idea

from .helpers import make_ai_model, make_category, make_idea


class IdeaFormActiveOptionsTests(TestCase):
    def test_inactive_category_is_excluded_for_a_new_idea(self):
        active = make_category(is_active=True)
        inactive = make_category(is_active=False)
        form = IdeaForm()
        queryset = form.fields["category"].queryset
        self.assertIn(active, queryset)
        self.assertNotIn(inactive, queryset)

    def test_inactive_category_already_on_the_idea_is_still_offered(self):
        inactive = make_category(is_active=False)
        idea = make_idea(category=inactive)
        form = IdeaForm(instance=idea)
        self.assertIn(inactive, form.fields["category"].queryset)

    def test_valid_data_saves(self):
        category = make_category()
        form = IdeaForm(
            data={
                "title": "New idea",
                "category": category.id,
                "summary": "",
                "interest_level": 3,
                "status": "current",
                "stage": "",
                "rank": 0,
                "notes": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        idea = form.save()
        self.assertEqual(Idea.objects.get(pk=idea.pk).title, "New idea")


class ResearchEntryFormTests(TestCase):
    def _data(self, **overrides):
        data = {
            "topic": "Landscape scan",
            "focus": "",
            "context": "",
            "occurred_at": "2026-07-20T14:30",
            "model": make_ai_model().id,
            "effort": 3,
            "quality": 4,
            "tokens_used": "",
        }
        data.update(overrides)
        return data

    def test_datetime_local_format_parses(self):
        form = ResearchEntryForm(data=self._data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["occurred_at"].strftime("%Y-%m-%dT%H:%M"), "2026-07-20T14:30")

    def test_inactive_model_excluded_unless_current(self):
        active = make_ai_model(is_active=True)
        inactive = make_ai_model(is_active=False)
        form = ResearchEntryForm()
        queryset = form.fields["model"].queryset
        self.assertIn(active, queryset)
        self.assertNotIn(inactive, queryset)

    def test_missing_topic_is_invalid(self):
        form = ResearchEntryForm(data=self._data(topic=""))
        self.assertFalse(form.is_valid())
        self.assertIn("topic", form.errors)
