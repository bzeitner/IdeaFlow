from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from executions.models import ExecutionEvent, PricingVersion
from executions.services import append_event, start_trace

from .helpers import make_workflow_version


class ImmutableConfigurationTests(TestCase):
    def test_pricing_version_cannot_be_changed_or_deleted(self):
        pricing = PricingVersion.objects.create(
            provider="test", model_identifier="one", effective_from=timezone.now()
        )
        pricing.model_identifier = "two"
        with self.assertRaises(ValidationError):
            pricing.save()
        with self.assertRaises(ValidationError):
            pricing.delete()

    def test_pricing_period_must_be_forward(self):
        now = timezone.now()
        pricing = PricingVersion(
            provider="test", model_identifier="one",
            effective_from=now, effective_until=now,
        )
        with self.assertRaises(ValidationError):
            pricing.full_clean()


class EventTests(TestCase):
    def test_events_are_ordered_and_append_only(self):
        trace, _ = start_trace(make_workflow_version(), trigger="test")
        event = append_event(trace, "custom")
        self.assertEqual(list(trace.events.values_list("sequence", flat=True)), [1, 2])
        event.event_type = "changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

    def test_database_rejects_duplicate_sequence(self):
        trace, _ = start_trace(make_workflow_version(), trigger="test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExecutionEvent.objects.create(
                trace=trace, sequence=1, event_type="duplicate", occurred_at=timezone.now()
            )
