import copy
import json
import tempfile
from datetime import timedelta
from unittest.mock import Mock, patch
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from evaluations import calibration as api
from evaluations.datasets import create_dataset, sample_research, preview_case, freeze_case, create_snapshot
from evaluations.grading import grade_case, abandon_attempt, build_prompt
from evaluations.models import (CalibrationPlan,CalibrationAttempt,CalibrationReview,CalibrationReport,
    CaseEvaluationResult,HumanCalibrationLabel,EvaluatorApproval,EvaluatorApprovalSupersession)
from evaluations.seeds import seed_evaluators
from executions.models import ModelConfiguration,PricingVersion,LLMRun
from executions.services import canonical_hash,start_trace,start_run,complete_run
from executions.tests.helpers import make_workflow_version,make_configuration
from ideas.models import ResearchEntry
from ideas.tests.helpers import make_user,make_idea,make_ai_model

FLAGS={'datasets':True,'evaluators':True,'model_graders':True}


@override_settings(IDEAFLOW_EXECUTION_FLAGS=FLAGS)
class CalibrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner=make_user('calibration-owner@example.test',roles=('role_current',))
        cls.owner.is_superuser=True
        cls.owner.save()
        cls.reviewer1=make_user('reviewer1@example.test')
        cls.reviewer2=make_user('reviewer2@example.test')
        cls.other=make_user('unassigned@example.test')
        cls.human,cls.quality,_=seed_evaluators()
        pricing=PricingVersion.objects.create(provider='anthropic',model_identifier='test-exact-model',
            input_micros_per_million=1000000,output_micros_per_million=2000000,
            effective_from=timezone.now()-timedelta(days=1),source='https://example.test/approved-price')
        cls.config=ModelConfiguration.objects.create(provider='anthropic',model_identifier='test-exact-model',
            settings={},pricing_version=pricing,content_hash=canonical_hash('grader-config'))
        cls.grader,_=api.publish_grader(cls.owner,cls.human.pk,cls.config.pk,2)
        cls.dataset=create_dataset(cls.owner,key='calibration',purpose='Test pilot',
            eligibility_policy={'workflows':['research'],'allow_legacy':True},redaction_policy='test-v1',retention_days=30)
        workflow=make_workflow_version()
        generation_config=make_configuration()
        cls.cases=[]
        cls.parents=[]
        for index in range(api.PILOT_CASE_COUNT):
            idea=make_idea(created_by=cls.owner)
            parent=None
            if index != api.PILOT_CASE_COUNT - 1:
                trace,_=start_trace(workflow,trigger='test',subject=idea)
                parent,_=start_run(trace,generation_config,rendered_input_hash=canonical_hash('generation input'))
                complete_run(parent,output_hash=canonical_hash('original output'),measurement_status='partial',
                    measurement_unavailable_reasons=['test'],cost_micros=10,cost_source='test',finalize_trace=True)
                parent.refresh_from_db()
            entry=ResearchEntry.objects.create(idea=idea,model=make_ai_model(),topic='Question',context='Report',produced_by_run=parent)
            origin=sample_research(cls.owner,cls.dataset.pk,[entry.pk])[0]['origin']
            proposal={'case_key':f'case-{index}','origin':origin,'payload':{'objective':'Question','prior_state':None,
                'output':'Report: supported finding','evidence':[{'ref':'source-1','excerpt':'Frozen supporting evidence','hash':canonical_hash('Frozen supporting evidence')}],
                'unavailable':{'prior_state':'Not captured'},'exclusions':[]},
                'rubric_assignments':[{'id':cls.human.pk,'hash':cls.human.content_hash,'rubric_key':'research'}],
                'cohorts':['short','decisive'],'split':'development' if index==0 else 'held_out','evidence_cutoff':timezone.now().isoformat()}
            preview=preview_case(cls.owner,cls.dataset.pk,proposal)
            case,_=freeze_case(cls.owner,cls.dataset.pk,proposal,approval_token=preview['approval_token'],approved_hash=preview['approval_hash'],idempotency_key=f'case-{index}')
            cls.cases.append(case)
            cls.parents.append(parent)
        cls.snapshot,_=create_snapshot(cls.owner,cls.dataset.pk,[c.pk for c in cls.cases],
            {'method':'approved pilot','calibration_eligible':True},idempotency_key='snapshot')
        cls.spec={'snapshot_id':cls.snapshot.pk,'human_version_id':cls.human.pk,'grader_version_id':cls.grader.pk,
            'reviewer_ids':[cls.reviewer1.pk,cls.reviewer2.pk],
            'thresholds':{'min_held_out_cases':api.PILOT_CASE_COUNT-1,'min_agreement':1,'min_coverage':1,'max_progress_mae':0,
                'max_critical_misses':0,'min_critical_failures':1},
            'budget':{'max_calls':40,'max_input_bytes':65536,'max_output_tokens':2000,'max_total_tokens':1000000,
                'max_cost_micros':10000000,'timeout_seconds':60,'max_attempts_per_case':2},
            'execution_binding':api.execution_binding(cls.grader)}
        cls.plan,_=api.create_plan(cls.owner,**cls.spec,approved_hash=canonical_hash(cls.spec),idempotency_key='plan')

    def setUp(self):
        self.storage=tempfile.TemporaryDirectory()
        self.override=override_settings(IDEAFLOW_EXECUTION_PAYLOAD_ROOT=self.storage.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.storage.cleanup)

    def assessment(self,score=3,status='pass'):
        return {'criterion_results':[{'id':'progress.objective','status':status,'reason':'Supported by the frozen case.',
            'evidence_refs':['objective','output','source-1'] if status in {'pass','fail'} else []}],
            'progress_score':score if status in {'pass','fail'} else None}

    def response(self,assessment=None,**kwargs):
        return json.dumps({'id':'request-test','model':'test-exact-model','stop_reason':'end_turn',
            'usage':{'input_tokens':100,'output_tokens':200},
            'content':[{'type':'text','text':json.dumps(assessment or self.assessment())}],**kwargs}).encode()

    def grade(self,index=1,adapter=None,key=None):
        return grade_case(self.owner,self.plan.pk,self.cases[index].pk,idempotency_key=key or f'grade-{index}',adapter=adapter or Mock(return_value=self.response()))

    def review(self,reviewer,index=1,score=3,key=None,adjudication=False):
        return api.submit_review(reviewer,self.plan.pk,self.cases[index].pk,self.assessment(score),
            human_attested=True,idempotency_key=key or f'review-{index}',adjudication=adjudication)

    def test_measured_child_does_not_reopen_parent_trace_and_retry_never_calls_provider(self):
        parent=self.parents[1]
        before=type(parent.trace).objects.values().get(pk=parent.trace_id)
        provider=Mock(return_value=self.response())
        result,created=self.grade(adapter=provider)
        run=result.attempt.run
        self.assertTrue(created)
        self.assertEqual((run.parent_run_id,run.trace_id,run.purpose),(parent.pk,parent.trace_id,'evaluation'))
        self.assertEqual(run.total_tokens,300)
        self.assertEqual(run.cost_micros,500)
        self.assertEqual(run.parsed_output,{'assessment_hash':canonical_hash(self.assessment())})
        self.assertEqual(type(parent.trace).objects.values().get(pk=parent.trace_id),before)
        self.assertEqual(self.grade(adapter=provider),(result,False))
        self.assertEqual(provider.call_count,1)
        with self.assertRaises(ValidationError):
            self.grade(key='repeat-success')

    def test_parent_start_run_stays_closed(self):
        with self.assertRaises(ValidationError):
            start_run(self.parents[1].trace,self.config,rendered_input_hash=canonical_hash('x'))

    def test_timeout_is_failed_execution_not_quality_and_reservation_is_retained(self):
        provider=Mock(side_effect=TimeoutError)
        with self.assertRaises(ValidationError):
            self.grade(adapter=provider)
        attempt=CalibrationAttempt.objects.get(plan=self.plan)
        self.assertEqual(attempt.run.status,'failed')
        self.assertGreater(attempt.reserved_cost_micros,0)
        self.assertFalse(CaseEvaluationResult.objects.exists())
        with self.assertRaises(ValidationError):
            self.grade(adapter=provider)
        self.assertEqual(provider.call_count,1)
        result,_=self.grade(key='explicit-retry')
        self.assertEqual(CalibrationAttempt.objects.count(),2)
        self.assertEqual(result.attempt.run.attempt_number,2)

    def test_malformed_output_preserves_response_usage_and_records_failure(self):
        provider=Mock(return_value=self.response(content=[{'type':'text','text':'not json'}]))
        with self.assertRaises(ValidationError):
            self.grade(adapter=provider)
        run=CalibrationAttempt.objects.get().run
        self.assertEqual(run.status,'failed')
        self.assertTrue(run.output_ref)
        self.assertEqual((run.total_tokens,run.cost_micros),(300,500))
        self.assertFalse(CaseEvaluationResult.objects.exists())

    def test_provider_model_mismatch_and_truncation_rejected(self):
        for index,changes in [(1,{'model':'unexpected-model'}),(2,{'stop_reason':'max_tokens'})]:
            with self.assertRaises(ValidationError):
                self.grade(index,adapter=Mock(return_value=self.response(**changes)))
        self.assertFalse(CaseEvaluationResult.objects.exists())

    def test_disabled_model_grader_never_reserves_or_calls(self):
        provider=Mock()
        with override_settings(IDEAFLOW_EXECUTION_FLAGS={**FLAGS,'model_graders':False}):
            with self.assertRaises(PermissionDenied):
                self.grade(adapter=provider)
        provider.assert_not_called()
        self.assertFalse(CalibrationAttempt.objects.exists())

    def test_budget_is_reserved_before_call_and_usage_excess_stops_further_work(self):
        with patch('evaluations.grading.estimate_cost_micros',return_value=10000001):
            provider=Mock()
            with self.assertRaises(ValidationError):
                self.grade(adapter=provider)
            provider.assert_not_called()
        provider=Mock(return_value=self.response(usage={'input_tokens':100,'output_tokens':2001}))
        with self.assertRaises(ValidationError):
            self.grade(adapter=provider)
        self.assertEqual(CalibrationAttempt.objects.get().run.error_code,'budget_exceeded')
        provider.reset_mock()
        with self.assertRaises(ValidationError):
            self.grade(2,adapter=provider)
        provider.assert_not_called()

    def test_blinded_prompt_and_independent_packets_do_not_expose_judgments(self):
        self.review(self.reviewer1)
        self.grade()
        packet=api.review_packet(self.reviewer2,self.plan.pk,self.cases[1].pk)
        self.assertNotIn('independent_reviews',packet)
        self.assertNotIn('model_results',packet)
        prompt=build_prompt(self.cases[1],self.grader)
        self.assertNotIn(str(self.parents[1].pk),prompt)
        self.assertNotIn('held_out',prompt)
        self.assertNotIn('test-exact-model',prompt)
        with self.assertRaises(PermissionDenied):
            api.review_packet(self.other,self.plan.pk,self.cases[1].pk)
        with self.assertRaises(PermissionDenied):
            api.review_packet(AnonymousUser(),self.plan.pk,self.cases[1].pk)
        with self.assertRaises(ValidationError):
            api.adjudication_packet(self.owner,self.plan.pk,self.cases[1].pk)

    def test_human_reviews_idempotency_corrections_and_stale_adjudication(self):
        first,_=self.review(self.reviewer1,score=2)
        self.assertEqual(self.review(self.reviewer1,score=2),(first,False))
        second,_=self.review(self.reviewer2,score=4)
        self.assertIsNone(api.gold_assessment(self.plan,self.cases[1])[0])
        adjudicated,_=self.review(self.owner,score=3,adjudication=True)
        self.assertEqual(set(adjudicated.label.adjudicates),{first.label_id,second.label_id})
        self.assertEqual(api.gold_assessment(self.plan,self.cases[1])[0]['progress_score'],3)
        corrected,_=self.review(self.reviewer1,score=1,key='correction')
        self.assertEqual(corrected.supersedes_id,first.pk)
        self.assertIsNone(api.gold_assessment(self.plan,self.cases[1])[0])
        self.assertEqual(HumanCalibrationLabel.objects.count(),4)

    def test_no_human_impersonation_or_invented_missing_evidence(self):
        with self.assertRaises(ValidationError):
            api.submit_review(self.reviewer1,self.plan.pk,self.cases[1].pk,self.assessment(),human_attested=False,idempotency_key='x')
        with self.assertRaises(PermissionDenied):
            self.review(self.other)
        assessment=self.assessment()
        assessment['criterion_results'][0]['evidence_refs']=['output']
        with self.assertRaises(ValidationError):
            api.validate_assessment(self.cases[1],self.human,assessment)
        api.validate_assessment(self.cases[1],self.human,self.assessment(status='insufficient_evidence'))

    def test_report_gate_incomplete_then_passing_and_explicit_approval(self):
        report,_=api.create_report(self.owner,self.plan.pk)
        self.assertFalse(report.eligible)
        with self.assertRaises(ValidationError):
            api.approve_report(self.owner,report.pk,reason='pilot use')
        for index in range(1,api.PILOT_CASE_COUNT):
            self.grade(index)
            self.review(self.reviewer1,index)
            self.review(self.reviewer2,index)
        report,_=api.create_report(self.owner,self.plan.pk)
        self.assertFalse(report.eligible)
        self.assertEqual(report.metrics['blocking_reasons'],['missing_labels_adjudication_or_model_results'])
        self.grade(0)
        self.review(self.reviewer1,0)
        self.review(self.reviewer2,0)
        report,_=api.create_report(self.owner,self.plan.pk)
        self.assertTrue(report.eligible,report.metrics)
        self.assertEqual(report.metrics['held_out']['progress_mae'],0)
        self.assertEqual(report.metrics['grader_cost']['known_micros'],15000)
        self.assertEqual(report.metrics['generation_cost']['known_micros'],290)
        self.assertEqual(report.metrics['generation_cost']['unknown_count'],1)
        self.assertIsNone(report.metrics['combined_cost_micros'])
        approval,created=api.approve_report(self.owner,report.pk,reason='Only this pilot scope')
        self.assertTrue(created)
        self.assertEqual(approval.evaluator_version_id,self.grader.pk)
        self.assertEqual(list(api.effective_approvals(self.grader,plan=self.plan)),[approval])
        self.review(self.reviewer1,1,score=1,key='later-correction')
        self.assertFalse(api.effective_approvals(self.grader,plan=self.plan).exists())
        supersession=EvaluatorApprovalSupersession.objects.get()
        self.assertEqual(supersession.approval_id,approval.pk)
        with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as cursor:
            cursor.execute('UPDATE evaluations_evaluatorapprovalsupersession SET actor_label=%s WHERE id=%s',['tampered',supersession.pk])
        with self.assertRaises(ValidationError):
            api.approve_report(self.owner,report.pk,reason='stale report')

    def test_overlapping_plan_requires_explicit_evidence_free_supersession(self):
        snapshot,_=create_snapshot(self.owner,self.dataset.pk,[c.pk for c in self.cases],
            {'method':'replacement pilot','calibration_eligible':True},idempotency_key='replacement-snapshot')
        spec={**self.spec,'snapshot_id':snapshot.pk}
        with self.assertRaises(ValidationError):
            api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),idempotency_key='parallel-plan')
        spec['supersedes_plan_id']=self.plan.pk
        replacement,created=api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),idempotency_key='replacement-plan')
        self.assertTrue(created)
        self.assertEqual(replacement.supersedes_id,self.plan.pk)
        with self.assertRaises(ValidationError):
            api.review_packet(self.reviewer1,self.plan.pk,self.cases[1].pk)

    def test_plan_with_collected_evidence_cannot_be_superseded(self):
        self.review(self.reviewer1)
        snapshot,_=create_snapshot(self.owner,self.dataset.pk,[c.pk for c in self.cases],
            {'method':'late replacement','calibration_eligible':True},idempotency_key='late-replacement-snapshot')
        spec={**self.spec,'snapshot_id':snapshot.pk,'supersedes_plan_id':self.plan.pk}
        with self.assertRaises(ValidationError):
            api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),idempotency_key='late-replacement')

    def test_model_and_price_drift_rejected_and_plan_immutable(self):
        ModelConfiguration.objects.filter(pk=self.config.pk).update(model_identifier='different-model')
        provider=Mock()
        with self.assertRaises(ValidationError):
            self.grade(adapter=provider)
        provider.assert_not_called()
        with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as cursor:
            cursor.execute('UPDATE evaluations_calibrationplan SET actor_label=%s WHERE id=%s',['tampered',self.plan.pk])

    def test_threshold_changes_cannot_follow_held_out_results(self):
        self.grade()
        spec=copy.deepcopy(self.spec)
        spec['thresholds']['max_progress_mae']=4
        with self.assertRaises(ValidationError):
            api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),idempotency_key='tuned-after-results')

    def test_convenience_storage_canary_cannot_be_used_for_calibration(self):
        snapshot,_=create_snapshot(self.owner,self.dataset.pk,[c.pk for c in self.cases],
            {'method':'storage canary','calibration_eligible':False},idempotency_key='storage-only')
        spec={**self.spec,'snapshot_id':snapshot.pk}
        with self.assertRaises(ValidationError):
            api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),idempotency_key='no')

    def test_pilot_requires_exact_case_count_and_both_splits(self):
        short_snapshot,_=create_snapshot(self.owner,self.dataset.pk,[c.pk for c in self.cases[:-1]],
            {'method':'short pilot','calibration_eligible':True},idempotency_key='short-pilot')
        spec=copy.deepcopy(self.spec)
        spec['snapshot_id']=short_snapshot.pk
        spec['thresholds']['min_held_out_cases']=api.PILOT_CASE_COUNT-2
        with self.assertRaises(ValidationError):
            api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),idempotency_key='short-pilot')

        from evaluations.datasets import case_content
        all_held_dataset=create_dataset(self.owner,key='all-held',purpose='Invalid split test',
            eligibility_policy={'workflows':['research'],'allow_legacy':True},redaction_policy='test-v1',retention_days=30)
        all_held_cases=[]
        for index,source in enumerate(self.cases):
            proposal={'case_key':f'all-held-{index}','origin':source.origin,'payload':case_content(source)[0],
                'rubric_assignments':source.rubric_assignments,'cohorts':['all-held'],'split':'held_out',
                'evidence_cutoff':timezone.now().isoformat()}
            preview=preview_case(self.owner,all_held_dataset.pk,proposal)
            case,_=freeze_case(self.owner,all_held_dataset.pk,proposal,approval_token=preview['approval_token'],
                approved_hash=preview['approval_hash'],idempotency_key=f'all-held-{index}')
            all_held_cases.append(case)
        all_held_snapshot,_=create_snapshot(self.owner,all_held_dataset.pk,[c.pk for c in all_held_cases],
            {'method':'invalid all-held pilot','calibration_eligible':True},idempotency_key='all-held')
        spec={**self.spec,'snapshot_id':all_held_snapshot.pk}
        with self.assertRaises(ValidationError):
            api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),idempotency_key='all-held')

    def test_optional_communication_does_not_offset_critical_miss(self):
        version=self.quality
        gold={'criterion_results':[{'id':c['id'],'status':'fail' if c['severity']=='critical' else 'pass'} for c in version.rubric['criteria']],'progress_score':None}
        model={'criterion_results':[{'id':c['id'],'status':'pass'} for c in version.rubric['criteria']],'progress_score':None}
        counts=api._scores([{'gold':gold,'model':model,'two_reviews':True,'human_agree':True}],version)
        self.assertEqual(counts['critical_failure_misses'],2)
        self.assertEqual(counts['critical_failures_in_gold'],2)
        self.assertEqual(counts['criterion_comparisons'],7)

    def test_overdue_attempt_recovery_retains_budget_and_generation_trace(self):
        from evaluations.grading import _reserve
        attempt,_,_=_reserve(self.owner,self.plan.pk,self.cases[1].pk,'overdue')
        with self.assertRaises(ValidationError):
            abandon_attempt(self.owner,attempt.pk)
        parent_before=type(attempt.run.trace).objects.values().get(pk=attempt.run.trace_id)
        with patch('evaluations.grading.timezone.now',return_value=timezone.now()+timedelta(minutes=10)):
            abandon_attempt(self.owner,attempt.pk)
        attempt.run.refresh_from_db()
        self.assertEqual(attempt.run.status,'failed')
        self.assertGreater(attempt.reserved_tokens,0)
        self.assertEqual(type(attempt.run.trace).objects.values().get(pk=attempt.run.trace_id),parent_before)

    def test_legacy_case_gets_dedicated_evaluation_trace_without_fabricated_parent(self):
        case=self.cases[-1]
        result,_=grade_case(self.owner,self.plan.pk,case.pk,idempotency_key='legacy',adapter=Mock(return_value=self.response()))
        run=result.attempt.run
        self.assertIsNone(run.parent_run_id)
        self.assertEqual(run.trace.workflow_version.workflow.key,'evaluation')
        self.assertEqual(run.trace.status,'succeeded')
        self.assertEqual(str(run.trace.subject_object_id),str(case.pk))

    def test_payload_audit_retention_never_outlives_case(self):
        result,_=self.grade()
        from executions.storage import ExecutionPayloadStore
        store=ExecutionPayloadStore()
        for reference in [result.attempt.run.rendered_input_ref,result.attempt.run.output_ref]:
            with store.storage.open(store._name(reference)+'.meta') as source:
                metadata=json.load(source)
            from django.utils.dateparse import parse_datetime
            self.assertLessEqual(parse_datetime(metadata['expires_at']),self.cases[1].expires_at)

    def test_network_transport_forwards_no_tools_and_bounds_response(self):
        from evaluations.grader_transport import _AnthropicRequest
        response=Mock(status=200)
        response.read1.side_effect=[self.response(),b'']
        conn=Mock()
        conn.getresponse.return_value=response
        with patch('evaluations.grader_transport.http.client.HTTPSConnection',return_value=conn),patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test-only-not-a-real-key'}):
            raw=_AnthropicRequest()(model='test-exact-model',prompt='Frozen case',max_output_tokens=1000,timeout_seconds=30,system='Instructions')
        self.assertEqual(raw,self.response())
        request=json.loads(conn.request.call_args.kwargs['body'])
        self.assertEqual(request['max_tokens'],1000)
        self.assertEqual(request['service_tier'],'standard_only')
        self.assertNotIn('temperature',request)
        self.assertNotIn('top_p',request)
        self.assertNotIn('top_k',request)
        self.assertNotIn('tools',request)
        self.assertEqual(request['messages'][0]['content'],'Frozen case')
        conn.close.assert_called_once()

    def test_command_packets_private_and_no_unattested_review(self):
        from io import StringIO
        from pathlib import Path
        from django.core.management import call_command,CommandError
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'packet.json'
            output=StringIO()
            call_command('calibrate_research','packet',user_id=self.reviewer1.pk,plan_id=self.plan.pk,
                         case_id=self.cases[1].pk,output_file=str(path),stdout=output)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertNotIn('supported finding',output.getvalue())
            with self.assertRaises(CommandError):
                call_command('calibrate_research','review',user_id=self.reviewer1.pk,plan_id=self.plan.pk,
                    case_id=self.cases[1].pk,request_file=str(path),idempotency_key='not-human')


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from django.db import connections
from django.test import skipUnlessDBFeature
from .base import AuditTransactionTestCase


@override_settings(IDEAFLOW_EXECUTION_FLAGS=FLAGS)
class CalibrationConcurrencyTests(AuditTransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_same_request_cannot_make_two_provider_calls(self):
        CalibrationTests.setUpTestData.__func__(type(self))
        barrier=Barrier(2)
        body=json.dumps({'id':'request','model':'test-exact-model','stop_reason':'end_turn',
            'usage':{'input_tokens':100,'output_tokens':100},'content':[{'type':'text','text':json.dumps({
            'criterion_results':[{'id':'progress.objective','status':'pass','reason':'Supported','evidence_refs':['objective','output','source-1']}],
            'progress_score':3})}]}).encode()
        provider=Mock(return_value=body)
        with tempfile.TemporaryDirectory() as directory,override_settings(IDEAFLOW_EXECUTION_PAYLOAD_ROOT=directory):
            def worker():
                try:
                    barrier.wait(timeout=10)
                    try:
                        result,_=grade_case(self.owner,self.plan.pk,self.cases[1].pk,idempotency_key='concurrent',adapter=provider)
                        return result.pk
                    except ValidationError:
                        return None  # Other worker may observe a reserved in-flight attempt.
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(worker) for _ in range(2)]
                results=[f.result(timeout=20) for f in futures]
        self.assertEqual(provider.call_count,1)
        self.assertEqual(CalibrationAttempt.objects.count(),1)
        self.assertEqual(CaseEvaluationResult.objects.count(),1)
        self.assertTrue(any(r is not None for r in results))

    @skipUnlessDBFeature('has_select_for_update')
    def test_plan_replacement_cannot_race_with_first_review(self):
        CalibrationTests.setUpTestData.__func__(type(self))
        snapshot,_=create_snapshot(self.owner,self.dataset.pk,[c.pk for c in self.cases],
            {'method':'concurrent replacement','calibration_eligible':True},idempotency_key='concurrent-replacement')
        spec={**self.spec,'snapshot_id':snapshot.pk,'supersedes_plan_id':self.plan.pk}
        prior_locked=Event()
        replacement_started=Event()

        def reviewer():
            try:
                with transaction.atomic():
                    CalibrationPlan.objects.select_for_update().get(pk=self.plan.pk)
                    prior_locked.set()
                    if not replacement_started.wait(timeout=10):
                        return None
                    review,_=api.submit_review(self.reviewer1,self.plan.pk,self.cases[1].pk,
                        CalibrationTests.assessment(self),human_attested=True,idempotency_key='racing-review')
                    return review.pk
            finally:
                connections.close_all()

        def replace():
            try:
                if not prior_locked.wait(timeout=10):
                    return 'not-started'
                replacement_started.set()
                try:
                    api.create_plan(self.owner,**spec,approved_hash=canonical_hash(spec),
                        idempotency_key='racing-replacement')
                except ValidationError:
                    return 'rejected'
                return 'created'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            review_future=pool.submit(reviewer)
            replacement_future=pool.submit(replace)
            self.assertIsNotNone(review_future.result(timeout=20))
            self.assertEqual(replacement_future.result(timeout=20),'rejected')
        self.assertEqual(CalibrationPlan.objects.count(),1)
