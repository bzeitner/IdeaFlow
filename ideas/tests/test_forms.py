from django.test import TestCase

from ideas.forms import IdeaForm, ResearchEntryForm, ResearchEntryFormSet
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

    def test_blank_occurred_at_defaults_to_now_on_a_filled_row(self):
        form = ResearchEntryForm(data=self._data(occurred_at=""))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(form.cleaned_data["occurred_at"])


class ResearchEntryFormSetEmptyRowTests(TestCase):
    """The add-idea form ships one empty research row; submitting it untouched
    must save the idea with no research entry, not raise required-field errors."""

    def _post(self, idea, **row):
        prefix = ResearchEntryFormSet().prefix
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        # The RadioSelect star fields render pre-selected (model default 3), so a
        # real browser submits them even when the row is otherwise blank.
        row.setdefault("effort", "3")
        row.setdefault("quality", "3")
        for name, value in row.items():
            data[f"{prefix}-0-{name}"] = value
        return data

    def test_untouched_empty_row_saves_no_entry(self):
        idea = make_idea()
        formset = ResearchEntryFormSet(self._post(idea), instance=idea)
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.assertEqual(idea.research_entries.count(), 0)

    def test_filled_row_still_saves(self):
        idea = make_idea()
        model = make_ai_model()
        formset = ResearchEntryFormSet(
            self._post(idea, topic="Scan", model=model.id), instance=idea
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        entry = idea.research_entries.get()
        self.assertEqual(entry.topic, "Scan")
        self.assertIsNotNone(entry.occurred_at)
