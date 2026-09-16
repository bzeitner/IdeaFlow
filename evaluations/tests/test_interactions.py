import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import date, timedelta
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, connections, transaction
from django.test import Client, TestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from evaluations.interactions import (target_for, record_exposure, record_feedback, edit_research,
                                      feedback_state, link_outcome)
from evaluations.models import EvaluationExposure, HumanFeedback, FeedbackOutcomeLink
from evaluations.views import panel_for
from executions.models import OutcomeEvent
from executions.services import complete_run, canonical_hash, start_run, start_trace
from executions.tests.helpers import make_workflow_version, make_configuration
from ideas.models import ResearchEntry, WeeklySummary
from ideas.tests.helpers import make_user, make_idea, make_ai_model, MODEL_BACKEND
from .base import AuditTransactionTestCase


@override_settings(IDEAFLOW_EXECUTION_FLAGS={"feedback": True})
class InteractionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user('owner@example.test', roles=('role_current', 'role_weekly_summary'))
        cls.other = make_user('other@example.test', roles=('role_current',))
        cls.admin = make_user('admin@example.test', roles=('role_admin',))
        cls.idea = make_idea(created_by=cls.owner)
        trace, _ = start_trace(make_workflow_version(), trigger='test', subject=cls.idea)
        cls.target_run, _ = start_run(trace, make_configuration(), rendered_input_hash=canonical_hash('input'))
        complete_run(cls.target_run, output_hash=canonical_hash('raw provider report'), finish_reason='stop',
                     measurement_status='unavailable', measurement_unavailable_reasons=['test'])
        cls.entry = ResearchEntry.objects.create(idea=cls.idea, model=make_ai_model(), topic='Question', context='Visible report', produced_by_run=cls.target_run)
        cls.summary = WeeklySummary.objects.create(period_start=date(2026, 9, 1), period_end=date(2026, 9, 7), title='Summary', content='Weekly report')

    def setUp(self):
        self.client.force_login(self.owner, backend=MODEL_BACKEND)
        self.identity = target_for(self.owner, 'research', self.entry.pk)[1]
        self.start = timezone.now() - timedelta(days=1)

    def feedback(self, **kwargs):
        return record_feedback(self.owner, self.identity, action=kwargs.pop('action', 'useful'),
                               request_key=kwargs.pop('request_key', uuid.uuid4()), **kwargs)

    def post_data(self, operation='feedback', **kwargs):
        panel = panel_for(self.owner, 'research', self.entry)
        return {'target': panel['token'], 'operation': operation, 'action': 'useful',
                'request_key': str(uuid.uuid4()), **kwargs}

    def test_get_and_prefetch_never_record_exposure(self):
        for headers in ({}, {'HTTP_SEC_PURPOSE': 'prefetch'}):
            response = self.client.get(reverse('ideas:view_research_entry', args=[self.idea.pk, self.entry.pk]), **headers)
            self.assertContains(response, 'feedback-form')
        self.client.get(reverse('ideas:weekly_summaries'))
        self.assertEqual(EvaluationExposure.objects.count(), 0)
        self.assertEqual(HumanFeedback.objects.count(), 0)

    def test_exposure_retry_same_session_later_session_retained(self):
        session = uuid.uuid4()
        first, created = record_exposure(self.owner, self.identity, view_session=session)
        self.assertTrue(created)
        self.assertEqual(record_exposure(self.owner, self.identity, view_session=session), (first, False))
        record_exposure(self.owner, self.identity, view_session=uuid.uuid4())
        self.assertEqual(EvaluationExposure.objects.count(), 2)
        self.assertEqual(first.run_output_hash, canonical_hash('raw provider report'))
        self.assertNotEqual(first.output_hash, first.run_output_hash)

    def test_feedback_does_not_invent_exposure_and_retry_conflict_rejected(self):
        key = uuid.uuid4()
        result, _ = self.feedback(request_key=key)
        self.assertIsNone(result.exposure_id)
        self.assertFalse(EvaluationExposure.objects.exists())
        self.assertEqual(self.feedback(request_key=key), (result, False))
        with self.assertRaises(ValidationError):
            self.feedback(request_key=key, action='reject')

    def test_cross_owner_denied_public_read_allowed_edit_denied(self):
        with self.assertRaises(PermissionDenied):
            record_exposure(self.other, self.identity, view_session=uuid.uuid4())
        self.idea.is_public = True
        self.idea.save(update_fields=['is_public'])
        record_exposure(self.other, self.identity, view_session=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            edit_research(self.other, self.identity, context='Changed', request_key=uuid.uuid4())
        target_for(self.admin, 'research', self.entry.pk, edit=True)

    def test_summary_requires_role_and_unknown_run_stays_unknown(self):
        _, identity, _ = target_for(self.owner, 'weekly_summary', self.summary.pk)
        row, _ = record_feedback(self.owner, identity, action='accept', request_key=uuid.uuid4())
        self.assertIsNone(row.producing_run_id)
        self.assertEqual(row.run_output_hash, '')
        with self.assertRaises(PermissionDenied):
            target_for(self.other, 'weekly_summary', self.summary.pk)
        WeeklySummary.objects.filter(pk=self.summary.pk).update(produced_by_run=self.target_run)
        self.assertIsNone(target_for(self.owner, 'weekly_summary', self.summary.pk)[1]['producing_run_id'])

    def test_permission_revocation_rechecked_after_page_render(self):
        data = self.post_data()
        self.owner.profile.role_current = False
        self.owner.profile.save()
        response = self.client.post(reverse('evaluations:interaction'), data)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(HumanFeedback.objects.exists())

    def test_token_actor_tampering_and_machine_requests_rejected(self):
        url = reverse('evaluations:interaction')
        data = self.post_data()
        self.client.force_login(self.other, backend=MODEL_BACKEND)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.owner, backend=MODEL_BACKEND)
        self.assertEqual(self.client.post(url, {**data, 'target': data['target'] + 'x'}).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.post(url, data, HTTP_AUTHORIZATION='Bearer machine-token').status_code, 302)
        self.assertFalse(HumanFeedback.objects.exists())

    def test_feedback_retry_remains_stable_when_exposure_arrives_later(self):
        data = self.post_data()
        url = reverse('evaluations:interaction')
        first = self.client.post(url, data)
        self.assertEqual(first.status_code, 200)
        self.assertIsNone(HumanFeedback.objects.get().exposure_id)
        self.assertEqual(self.client.post(url, {**data, 'operation': 'exposure', 'visible': 'true'}).status_code, 200)
        second = self.client.post(url, data)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()['created'])
        self.assertIsNone(HumanFeedback.objects.get().exposure_id)

    def test_browser_csrf_and_post_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner, backend=MODEL_BACKEND)
        url = reverse('evaluations:interaction')
        self.assertEqual(client.post(url, self.post_data()).status_code, 403)
        self.assertEqual(client.get(url).status_code, 405)
        response = client.get(reverse('ideas:view_research_entry', args=[self.idea.pk, self.entry.pk]))
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(url, self.post_data(), HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_browser_exposure_must_claim_visible_and_links_only_known_session(self):
        data = self.post_data('exposure')
        url = reverse('evaluations:interaction')
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(self.client.post(url, {**data, 'visible': 'true'}).status_code, 200)
        self.assertEqual(self.client.post(url, {**data, 'operation': 'feedback'}).status_code, 200)
        self.assertIsNotNone(HumanFeedback.objects.get().exposure_id)
        self.assertEqual(EvaluationExposure.objects.count(), 1)

    def test_panel_never_signs_new_version_for_stale_rendered_object(self):
        old = ResearchEntry.objects.get(pk=self.entry.pk)
        ResearchEntry.objects.filter(pk=self.entry.pk).update(context='New report')
        self.assertIsNone(panel_for(self.owner, 'research', old))
        old.refresh_from_db()
        self.assertIsNotNone(panel_for(self.owner, 'research', old))

    def test_changed_output_and_producer_reject_stale_feedback(self):
        ResearchEntry.objects.filter(pk=self.entry.pk).update(context='Changed projection')
        with self.assertRaises(ValidationError):
            self.feedback()
        ResearchEntry.objects.filter(pk=self.entry.pk).update(context=self.entry.context, produced_by_run=None)
        with self.assertRaises(ValidationError):
            self.feedback()

    def test_invalid_exposure_or_correction_identity_rejected(self):
        self.idea.is_public = True
        self.idea.save(update_fields=['is_public'])
        other, _ = record_exposure(self.other, self.identity, view_session=uuid.uuid4())
        with self.assertRaises(ValidationError):
            self.feedback(exposure_id=other.pk)
        previous, _ = record_feedback(self.other, self.identity, action='reject', request_key=uuid.uuid4())
        with self.assertRaises(ValidationError):
            self.feedback(supersedes_id=previous.pk)

    def test_correction_is_append_only_and_state_uses_current_judgment(self):
        previous, _ = self.feedback(action='reject')
        replacement, _ = self.feedback(action='accept', supersedes_id=previous.pk)
        previous.refresh_from_db()
        self.assertEqual(previous.action, 'reject')
        self.assertEqual(replacement.supersedes_id, previous.pk)
        self.assertEqual(feedback_state(self.owner, self.identity, since=self.start), 'positive')
        with self.assertRaises(ValidationError):
            self.feedback(supersedes_id=previous.pk)

    def test_feedback_reporting_window_unknown_exposure_and_mixed(self):
        self.assertEqual(feedback_state(self.owner, self.identity, since=self.start), 'unknown')
        self.assertEqual(feedback_state(self.owner, self.identity, since=self.start, telemetry_complete=True), 'not_exposed')
        record_exposure(self.owner, self.identity, view_session=uuid.uuid4())
        self.assertEqual(feedback_state(self.owner, self.identity, since=self.start), 'exposed_without_feedback')
        self.feedback()
        self.feedback(action='reject')
        self.assertEqual(feedback_state(self.owner, self.identity, since=self.start), 'mixed')
        self.assertEqual(feedback_state(self.owner, self.identity, since=timezone.now()), 'unknown')

    def test_edit_updates_content_and_audit_atomically_retry_is_idempotent(self):
        key = uuid.uuid4()
        result, _ = edit_research(self.owner, self.identity, context='Edited research', request_key=key)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.context, 'Edited research')
        self.assertEqual(result.before_hash, self.identity['output_hash'])
        self.assertEqual(result.after_hash, target_for(self.owner, 'research', self.entry.pk)[1]['output_hash'])
        self.assertEqual(edit_research(self.owner, self.identity, context='Edited research', request_key=key), (result, False))
        self.assertIn(result, panel_for(self.owner, 'research', self.entry)['history'])
        with self.assertRaises(ValidationError):
            self.feedback(action='edit')

    def test_edit_failure_rolls_back_content_and_noop_creates_no_feedback(self):
        with self.assertRaises(ValidationError):
            edit_research(self.owner, self.identity, context=self.entry.context, request_key=uuid.uuid4())
        with patch('evaluations.interactions.HumanFeedback.objects.create', side_effect=ValidationError('write failed')):
            with self.assertRaises(ValidationError):
                edit_research(self.owner, self.identity, context='Changed', request_key=uuid.uuid4())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.context, 'Visible report')
        self.assertFalse(HumanFeedback.objects.exists())

    def test_existing_outcome_link_is_idempotent_and_never_creates_outcomes(self):
        feedback, _ = self.feedback()
        outcome = OutcomeEvent.objects.create(idea=self.idea, run=self.target_run, event_type='test')
        first, _ = link_outcome(self.owner, feedback.pk, outcome.pk)
        self.assertEqual(link_outcome(self.owner, feedback.pk, outcome.pk), (first, False))
        wrong = OutcomeEvent.objects.create(idea=make_idea(), run=self.target_run, event_type='test')
        with self.assertRaises(ValidationError):
            link_outcome(self.owner, feedback.pk, wrong.pk)
        self.assertEqual(OutcomeEvent.objects.count(), 2)

    def test_disabled_writes_preserve_history(self):
        result, _ = self.feedback()
        with override_settings(IDEAFLOW_EXECUTION_FLAGS={'feedback': False}):
            with self.assertRaises(PermissionDenied):
                self.feedback()
            with self.assertRaises(PermissionDenied):
                record_exposure(self.owner, self.identity, view_session=uuid.uuid4())
            response = self.client.get(reverse('ideas:view_research_entry', args=[self.idea.pk, self.entry.pk]))
            self.assertContains(response, 'New feedback is currently disabled')
            self.assertNotContains(response, 'class="feedback-form"')
            self.assertIn(result, response.context['feedback_panel']['history'])

    def test_credentials_rejected_and_history_escaped(self):
        with self.assertRaises(ValidationError):
            self.feedback(reason='Bearer dummy-secret-123456789')
        self.feedback(reason='<script>alert(1)</script>')
        response = self.client.get(reverse('ideas:view_research_entry', args=[self.idea.pk, self.entry.pk]))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')

    def test_immutable_interactions_and_business_deletion(self):
        exposure, _ = record_exposure(self.owner, self.identity, view_session=uuid.uuid4())
        feedback, _ = self.feedback(exposure_id=exposure.pk)
        outcome = OutcomeEvent.objects.create(idea=self.idea, run=self.target_run, event_type='test')
        link, _ = link_outcome(self.owner, feedback.pk, outcome.pk)
        for row in (exposure, feedback, link):
            with self.assertRaises(ValidationError):
                row.save()
            with self.assertRaises(ValidationError):
                type(row).objects.filter(pk=row.pk).delete()
            table = connection.ops.quote_name(row._meta.db_table)
            for operation in (f'UPDATE {table} SET actor_label = %s WHERE id = %s', f'DELETE FROM {table} WHERE actor_label = %s AND id = %s'):
                with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(operation, [row.actor_label, row.pk])
            if connection.vendor == 'postgresql':
                with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(f'TRUNCATE {table} CASCADE')
        self.entry.delete()
        self.assertEqual(HumanFeedback.objects.get(pk=feedback.pk).target_id, feedback.target_id)

    def test_edit_browser_submission_and_stale_edit(self):
        url = reverse('evaluations:edit_research', args=[self.entry.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        data = dict(page.context['form'].initial)
        data['context'] = 'Browser-edited report'
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(HumanFeedback.objects.get().action, 'edit')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(HumanFeedback.objects.count(), 1)
        data.update(context='Conflicting edit', request_key=uuid.uuid4())
        response = self.client.post(url, data)
        self.assertContains(response, 'Edit could not be saved')
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.context, 'Browser-edited report')

    def test_rating_validation_rejects_coercions_and_out_of_range(self):
        for rating in (True, 3.5, 0, 6):
            with self.subTest(rating=rating), self.assertRaises(ValidationError):
                self.feedback(rating=rating)

    def test_outcome_endpoint_validates_exact_target_and_actor(self):
        feedback, _ = self.feedback()
        outcome = OutcomeEvent.objects.create(idea=self.idea, run=self.target_run, event_type='test')
        data = self.post_data('outcome', feedback_id=feedback.pk, outcome_id=outcome.pk)
        url = reverse('evaluations:interaction')
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertFalse(self.client.post(url, data).json()['created'])
        self.assertEqual(FeedbackOutcomeLink.objects.count(), 1)

    def test_unrelated_run_projection_cannot_expose_evaluations(self):
        from executions.models import ExecutionTrace
        ExecutionTrace.objects.filter(pk=self.target_run.trace_id).update(subject_object_id=999999)
        panel = panel_for(self.owner, 'research', self.entry)
        self.assertFalse(panel['results'])
        self.assertIsNone(target_for(self.owner, 'research', self.entry.pk)[1]['producing_run_id'])

    def test_history_and_evaluations_not_exposed_to_other_owner(self):
        self.feedback(reason='Only the owner should see this feedback')
        self.client.force_login(self.other, backend=MODEL_BACKEND)
        response = self.client.get(reverse('ideas:view_research_entry', args=[self.idea.pk, self.entry.pk]))
        self.assertIsNone(response.context['feedback_panel'])
        self.assertNotContains(response, 'Only the owner should see this feedback')


@override_settings(IDEAFLOW_EXECUTION_FLAGS={"feedback": True})
class InteractionConcurrencyTests(AuditTransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_exposure_and_feedback_are_idempotent(self):
        owner = make_user('parallel@example.test', roles=('role_current',))
        entry = ResearchEntry.objects.create(idea=make_idea(created_by=owner), model=make_ai_model(), topic='Concurrency', context='Report')
        identity = target_for(owner, 'research', entry.pk)[1]
        barrier = Barrier(2)
        session = uuid.uuid4()
        request_key = uuid.uuid4()
        def worker():
            try:
                barrier.wait(timeout=10)
                exposure, _ = record_exposure(owner, identity, view_session=session)
                feedback, created = record_feedback(owner, identity, action='useful', request_key=request_key, exposure_id=exposure.pk)
                return feedback.pk, created
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(len({pk for pk, _ in results}), 1)
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(EvaluationExposure.objects.count(), 1)
        self.assertEqual(HumanFeedback.objects.count(), 1)
