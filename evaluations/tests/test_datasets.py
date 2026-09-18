import copy
import json
import os
import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from evaluations import datasets as api
from evaluations.models import (DatasetCase, DatasetCaseContent, DatasetCaseTombstone,
                               DatasetSnapshot, EvaluationDataset, HumanCalibrationLabel)
from evaluations.seeds import seed_evaluators
from executions.services import canonical_hash
from ideas.models import ResearchEntry
from ideas.tests.helpers import make_user, make_idea, make_ai_model


@override_settings(IDEAFLOW_EXECUTION_FLAGS={'datasets': True})
class DatasetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user('dataset-owner@example.test', roles=('role_current',))
        cls.owner.is_superuser = True
        cls.owner.save()
        cls.other = make_user('dataset-other@example.test', roles=('role_current',))
        cls.idea = make_idea(created_by=cls.owner)
        cls.entry = ResearchEntry.objects.create(idea=cls.idea, model=make_ai_model(), topic='Objective', context='Report')
        cls.progress, cls.quality, cls.structure = seed_evaluators()

    def setUp(self):
        self.dataset = api.create_dataset(self.owner, key='pilot', purpose='Research calibration pilot',
            eligibility_policy={'workflows': ['research'], 'allow_legacy': True},
            redaction_policy='reviewed-excerpts-v1', retention_days=30)
        origin = api.sample_research(self.owner, self.dataset.pk, [self.entry.pk])[0]['origin']
        self.proposal = {'case_key': 'research-one', 'origin': origin,
            'payload': {'objective': 'Objective', 'prior_state': None, 'output': 'Report',
                'evidence': [], 'unavailable': {'prior_state': 'No frozen prior state.', 'evidence': 'Not retained.'}, 'exclusions': []},
            'rubric_assignments': [{'id': self.progress.pk, 'hash': self.progress.content_hash,
                                   'rubric_key': self.progress.applicability['rubric_key']}],
            'cohorts': ['short', 'missing-inputs'], 'split': 'development',
            'evidence_cutoff': timezone.now().isoformat()}

    def freeze(self, proposal=None, key='freeze-one'):
        proposal = proposal or self.proposal
        preview = api.preview_case(self.owner, self.dataset.pk, proposal)
        return api.freeze_case(self.owner, self.dataset.pk, proposal,
            approval_token=preview['approval_token'], approved_hash=preview['approval_hash'], idempotency_key=key)

    def snapshot(self, case=None):
        if case is None:
            case, _ = self.freeze()
        return api.create_snapshot(self.owner, self.dataset.pk, [case.pk],
            {'method': 'operator_selected', 'representative': False}, idempotency_key='snapshot-one')[0]

    def test_freeze_snapshot_export_and_retry(self):
        case, created = self.freeze()
        self.assertTrue(created)
        self.assertEqual(self.freeze(), (case, False))
        snapshot = self.snapshot(case)
        exported = api.export_snapshot(self.owner, snapshot.pk)
        self.assertTrue(exported['reproducible'])
        self.assertEqual(exported['cases'][0]['payload'], self.proposal['payload'])
        self.assertEqual(canonical_hash(exported['cases'][0]['metadata']), case.content_hash)
        self.assertEqual(canonical_hash(exported['snapshot_metadata']), snapshot.content_hash)
        self.assertEqual(canonical_hash(exported['dataset_metadata']), self.dataset.content_hash)
        self.assertEqual(self.snapshot(case), snapshot)
        changed = copy.deepcopy(self.proposal)
        changed['payload']['output'] = 'Changed'
        with self.assertRaises(ValidationError):
            self.freeze(changed)

    def test_preview_approval_is_bound_to_exact_content_actor_dataset_and_time(self):
        preview = api.preview_case(self.owner, self.dataset.pk, self.proposal)
        changed = copy.deepcopy(self.proposal)
        changed['payload']['output'] = 'Different'
        with self.assertRaises(ValidationError):
            api.freeze_case(self.owner, self.dataset.pk, changed, approval_token=preview['approval_token'],
                            approved_hash=canonical_hash(changed), idempotency_key='new')
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 90000):
            with self.assertRaises(ValidationError):
                api.freeze_case(self.owner, self.dataset.pk, self.proposal, approval_token=preview['approval_token'],
                                approved_hash=preview['approval_hash'], idempotency_key='expired')
        self.assertFalse(DatasetCase.objects.exists())

    def test_source_change_between_preview_and_freeze_rejected(self):
        preview = api.preview_case(self.owner, self.dataset.pk, self.proposal)
        ResearchEntry.objects.filter(pk=self.entry.pk).update(context='New report')
        with self.assertRaises(ValidationError):
            api.freeze_case(self.owner, self.dataset.pk, self.proposal, approval_token=preview['approval_token'],
                approved_hash=preview['approval_hash'], idempotency_key='stale')

    def test_frozen_content_survives_source_edit_and_deletion(self):
        case, _ = self.freeze()
        snapshot = self.snapshot(case)
        ResearchEntry.objects.filter(pk=self.entry.pk).update(context='New report')
        ResearchEntry.objects.filter(pk=self.entry.pk).delete()
        exported = api.export_snapshot(self.owner, snapshot.pk)
        self.assertTrue(exported['reproducible'])
        self.assertEqual(exported['cases'][0]['payload']['output'], 'Report')
        self.assertIsNone(case.origin['producing_run_id'])

    def test_revisions_preserve_split_and_snapshot(self):
        first, _ = self.freeze()
        snapshot = self.snapshot(first)
        revised = copy.deepcopy(self.proposal)
        revised['payload']['output'] = 'Better redaction'
        second, _ = self.freeze(revised, 'revision-two')
        self.assertEqual((second.revision, second.supersedes_id), (2, first.pk))
        self.assertEqual(api.export_snapshot(self.owner, snapshot.pk)['cases'][0]['id'], first.pk)
        revised['split'] = 'held_out'
        with self.assertRaises(ValidationError):
            self.freeze(revised, 'split-change')
        with self.assertRaises(ValidationError):
            api.create_snapshot(self.owner, self.dataset.pk, [first.pk, second.pk], {'method': 'test'}, idempotency_key='duplicate')

    def test_permissions_revocation_and_flag_rollback(self):
        snapshot = self.snapshot()
        with self.assertRaises(PermissionDenied):
            api.sample_research(self.other, self.dataset.pk, [self.entry.pk])
        with self.assertRaises(PermissionDenied):
            api.export_snapshot(self.other, snapshot.pk)
        with override_settings(IDEAFLOW_EXECUTION_FLAGS={'datasets': False}):
            self.assertTrue(api.export_snapshot(self.owner, snapshot.pk)['reproducible'])
            with self.assertRaises(PermissionDenied):
                self.freeze()
        self.owner.is_active = False
        self.owner.save()
        with self.assertRaises(PermissionDenied):
            api.export_snapshot(self.owner, snapshot.pk)

    def test_credential_unknown_field_evidence_and_rubric_rejection(self):
        for field, value in [('output', 'password=not-allowed'), ('extra', 'unexpected')]:
            proposal = copy.deepcopy(self.proposal)
            proposal['payload'][field] = value
            with self.assertRaises(ValidationError):
                self.freeze(proposal)
        proposal = copy.deepcopy(self.proposal)
        proposal['rubric_assignments'][0]['hash'] = '0' * 64
        with self.assertRaises(ValidationError):
            self.freeze(proposal)
        proposal = copy.deepcopy(self.proposal)
        proposal['payload']['unavailable'] = {}
        with self.assertRaises(ValidationError):
            self.freeze(proposal)

    def test_content_hash_mismatch_fail_closed(self):
        case, _ = self.freeze()
        # Simulate corrupted bytes on read without disabling production guards.
        content = DatasetCaseContent(case=case, payload={**self.proposal['payload'], 'output': 'corruption'})
        with patch('evaluations.datasets.DatasetCaseContent.objects') as manager:
            manager.filter.return_value.first.return_value = content
            with self.assertRaises(ValidationError):
                api.case_content(case)

    def test_delete_tombstone_expiry_and_export_unavailable(self):
        case, _ = self.freeze()
        snapshot = self.snapshot(case)
        with self.assertRaises(ValidationError):
            api.delete_case_content(self.owner, case.pk, reason='expired')
        with patch('evaluations.datasets.timezone.now', return_value=case.expires_at + timedelta(seconds=1)):
            expired = api.export_snapshot(self.owner, snapshot.pk)
            self.assertFalse(expired['reproducible'])
            self.assertEqual(expired['cases'][0]['status'], 'expired')
            tombstone = api.delete_case_content(self.owner, case.pk, reason='expired')
        self.assertFalse(DatasetCaseContent.objects.filter(case=case).exists())
        self.assertEqual(api.delete_case_content(self.owner, case.pk, reason='required_deletion'), tombstone)
        exported = api.export_snapshot(self.owner, snapshot.pk)
        self.assertEqual(exported['cases'][0]['status'], 'deleted')
        self.assertIsNone(exported['cases'][0]['payload'])
        with self.assertRaises(ValidationError):
            DatasetCaseContent.objects.create(case=case, payload=self.proposal['payload'])

    def test_immutable_metadata_and_content_database_guards(self):
        case, _ = self.freeze()
        self.snapshot(case)
        with self.assertRaises(ValidationError):
            DatasetCase.objects.filter(pk=case.pk).update(split='held_out')
        statements = [
            ('UPDATE evaluations_datasetcase SET split=%s WHERE id=%s', ['held_out', case.pk]),
            ('DELETE FROM evaluations_datasetcase WHERE id=%s', [case.pk]),
            ('UPDATE evaluations_datasetcasecontent SET payload=%s WHERE case_id=%s', ['{}', case.pk]),
            ('DELETE FROM evaluations_datasetcasecontent WHERE case_id=%s', [case.pk]),
            ('DELETE FROM evaluations_datasetsnapshot', []),
        ]
        for sql, params in statements:
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
        api.delete_case_content(self.owner, case.pk, reason='required_deletion')
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO evaluations_datasetcasecontent (case_id, payload) VALUES (%s, %s)', [case.pk, '{}'])

    def test_atomic_content_failure_does_not_leave_case(self):
        with patch('evaluations.datasets.DatasetCaseContent.objects.create', side_effect=ValidationError('storage failure')):
            with self.assertRaises(ValidationError):
                self.freeze()
        self.assertFalse(DatasetCase.objects.exists())

    def test_label_schema_preserves_independent_labels(self):
        case, _ = self.freeze()
        values = dict(case=case, evaluator_version=self.progress,
            criterion_results=[{'id': c['id'], 'status': 'pass'} for c in self.progress.rubric['criteria']],
            progress_score=3, supporting_refs=['output'])
        first = HumanCalibrationLabel.objects.create(**values, actor_label=f'user:{self.owner.pk}', reviewer_user_id=self.owner.pk, idempotency_key='first')
        second = HumanCalibrationLabel.objects.create(**values, actor_label=f'user:{self.other.pk}', reviewer_user_id=self.other.pk, idempotency_key='second')
        judgment = HumanCalibrationLabel.objects.create(**values, actor_label=f'user:{self.owner.pk}', reviewer_user_id=self.owner.pk, idempotency_key='adjudication', adjudicates=[first.pk, second.pk])
        self.assertEqual(judgment.adjudicates, [first.pk, second.pk])
        with self.assertRaises(ValidationError):
            first.save()
        with self.assertRaises(ValidationError):
            HumanCalibrationLabel.objects.create(**{**values, 'progress_score': 3.5}, actor_label=f'user:{self.owner.pk}', reviewer_user_id=self.owner.pk, idempotency_key='fractional')

    def test_command_private_output_no_overwrite_or_stdout_content(self):
        with tempfile.TemporaryDirectory() as directory:
            request = os.path.join(directory, 'request.json')
            output = os.path.join(directory, 'preview.json')
            with open(request, 'w') as source:
                json.dump(self.proposal, source)
            stdout = StringIO()
            call_command('evaluation_dataset', 'preview', user_id=self.owner.pk, dataset_id=self.dataset.pk,
                         request_file=request, output_file=output, stdout=stdout)
            self.assertNotIn('Report', stdout.getvalue())
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            with self.assertRaises(CommandError):
                call_command('evaluation_dataset', 'preview', user_id=self.owner.pk, dataset_id=self.dataset.pk,
                             request_file=request, output_file=output)
            with self.assertRaises(CommandError):
                call_command('evaluation_dataset', 'sample', user_id=self.owner.pk, dataset_id=self.dataset.pk, request_file=request)

    def test_regular_operator_cannot_sample_another_owners_private_source(self):
        from django.contrib.auth.models import Permission
        self.owner.is_superuser = False
        self.owner.save()
        self.owner.user_permissions.add(Permission.objects.get(codename='operate_datasets'))
        foreign = ResearchEntry.objects.create(idea=make_idea(created_by=self.other), model=make_ai_model(), topic='Private', context='Private report')
        with self.assertRaises(PermissionDenied):
            api.sample_research(self.owner, self.dataset.pk, [foreign.pk])
        self.other.user_permissions.add(Permission.objects.get(codename='operate_datasets'))
        with self.assertRaises(PermissionDenied):
            api.sample_research(self.other, self.dataset.pk, [self.entry.pk])
        self.assertEqual(len(api.sample_research(self.owner, self.dataset.pk, [self.entry.pk])), 1)

    def test_eligibility_policy_enforced_and_same_source_cannot_cross_splits(self):
        restricted = api.create_dataset(self.owner, key='attributed-only', purpose='Measured research',
            eligibility_policy={'workflows': ['research'], 'allow_legacy': False},
            redaction_policy='review-v1', retention_days=30)
        with self.assertRaises(ValidationError):
            api.sample_research(self.owner, restricted.pk, [self.entry.pk])
        with self.assertRaises(ValidationError):
            api.preview_case(self.owner, restricted.pk, self.proposal)
        self.freeze()
        alternate = copy.deepcopy(self.proposal)
        alternate.update(case_key='another-key', split='held_out')
        with self.assertRaises(ValidationError):
            self.freeze(alternate, 'split-leak')

    def test_missing_content_is_unavailable_not_reconstructed(self):
        case, _ = self.freeze()
        with patch('evaluations.datasets.DatasetCaseContent.objects') as manager:
            manager.filter.return_value.first.return_value = None
            self.assertEqual(api.case_content(case), (None, 'missing'))

    def test_delete_audit_failure_preserves_content(self):
        case, _ = self.freeze()
        with patch('evaluations.datasets.DatasetCaseTombstone.objects.create', side_effect=ValidationError('audit failure')):
            with self.assertRaises(ValidationError):
                api.delete_case_content(self.owner, case.pk, reason='required_deletion')
        self.assertTrue(DatasetCaseContent.objects.filter(case=case).exists())


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connections
from django.test import skipUnlessDBFeature
from .base import AuditTransactionTestCase


@override_settings(IDEAFLOW_EXECUTION_FLAGS={'datasets': True})
class DatasetConcurrencyTests(AuditTransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_freeze_and_snapshot_retries_serialize_on_dataset(self):
        owner = make_user('dataset-concurrency@example.test', roles=('role_current',))
        owner.is_superuser = True
        owner.save()
        entry = ResearchEntry.objects.create(idea=make_idea(created_by=owner), model=make_ai_model(), topic='Concurrent', context='Report')
        progress, _, _ = seed_evaluators()
        dataset = api.create_dataset(owner, key='parallel', purpose='Concurrency test',
            eligibility_policy={'workflows': ['research'], 'allow_legacy': True},
            redaction_policy='test-v1', retention_days=30)
        origin = api.sample_research(owner, dataset.pk, [entry.pk])[0]['origin']
        proposal = {'case_key': 'parallel-case', 'origin': origin,
            'payload': {'objective': 'Objective', 'prior_state': None, 'output': 'Report',
                        'evidence': [], 'unavailable': {'prior_state': 'Not captured', 'evidence': 'Not captured'}, 'exclusions': []},
            'rubric_assignments': [{'id': progress.pk, 'hash': progress.content_hash, 'rubric_key': progress.applicability['rubric_key']}],
            'cohorts': ['short'], 'split': 'development', 'evidence_cutoff': timezone.now().isoformat()}
        preview = api.preview_case(owner, dataset.pk, proposal)
        barrier = Barrier(2)
        def worker():
            try:
                barrier.wait(timeout=10)
                case, created = api.freeze_case(owner, dataset.pk, proposal,
                    approval_token=preview['approval_token'], approved_hash=preview['approval_hash'], idempotency_key='parallel')
                snapshot, snapshot_created = api.create_snapshot(owner, dataset.pk, [case.pk], {'method': 'test'}, idempotency_key='parallel-snapshot')
                return case.pk, created, snapshot.pk, snapshot_created
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=20) for future in [pool.submit(worker) for _ in range(2)]]
        self.assertEqual(len({r[0] for r in results}), 1)
        self.assertEqual(sum(r[1] for r in results), 1)
        self.assertEqual(len({r[2] for r in results}), 1)
        self.assertEqual(sum(r[3] for r in results), 1)
