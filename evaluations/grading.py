"""Bounded operator grading. Provider calls happen only after durable reservation."""
import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from executions.models import LLMRun, WorkflowDefinition, WorkflowVersion
from executions.services import (canonical_hash, start_trace, start_run, start_posthoc_evaluation,
    complete_run, fail_run, fail_trace, estimate_cost_micros, append_event)
from executions.storage import ExecutionPayloadStore
from .calibration import (GRADER_SYSTEM, require_writes, evidence_map, validate_assessment,
    _case_in_plan, ensure_active_plan)
from .datasets import authorize, verified
from .models import CalibrationPlan, CalibrationAttempt, CaseEvaluationResult
from .validation import summarize


from .grader_transport import AnthropicGrader


def build_prompt(case, version):
    # Deliberately exclude origin, producing model/run, treatment, cohorts, split,
    # reviewer identities, previous labels, and earlier model judgments.
    return json.dumps({'rubric':version.rubric,'metric_unit':version.metric.unit,
        'required_inputs':version.required_inputs,'evidence':evidence_map(case)},ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _store(case,kind,content):
    remaining=(case.expires_at-timezone.now()).total_seconds()
    days=min(settings.IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS,int(remaining//86400))
    if days<1:
        raise ValidationError('Case expires too soon to retain a one-day grader audit payload.')
    return ExecutionPayloadStore().put(kind,content,retention_days=days)


@transaction.atomic(durable=True)
def _reserve(user,plan_id,case_id,key):
    require_writes()
    if not settings.IDEAFLOW_EXECUTION_FLAGS.get('model_graders',False):
        raise PermissionDenied('Model graders are disabled.')
    plan=ensure_active_plan(verified(CalibrationPlan.objects.select_for_update().get(pk=plan_id)))
    user=authorize(user,plan.snapshot.dataset,write=True)
    plan.clean()
    case=_case_in_plan(plan,case_id)
    version=verified(plan.grader_version)
    if not version.evaluator.is_active:
        raise ValidationError('Grader evaluator is inactive.')
    prompt=build_prompt(case,version)
    prompt_bytes=prompt.encode()
    budget=plan.budget
    if len(prompt_bytes)>budget['max_input_bytes']:
        raise ValidationError('Frozen grader input exceeds the approved byte limit.')
    if not isinstance(key,str) or not key.strip() or len(key)>160:
        raise ValidationError('A bounded request key is required.')
    request_hash=canonical_hash({'plan':plan.content_hash,'case':case.content_hash,'prompt':canonical_hash(prompt)})
    request_key=canonical_hash({'plan':plan.pk,'actor':user.pk,'request':key})
    existing=CalibrationAttempt.objects.filter(idempotency_key=request_key).first()
    if existing:
        if verified(existing).request_hash!=request_hash:
            raise ValidationError('Grader request key conflicts with frozen inputs.')
        return existing,prompt,False
    attempts=CalibrationAttempt.objects.filter(plan=plan)
    if attempts.filter(case=case,run__status='succeeded').exists():
        raise ValidationError('Case already has a successful judgment; successful outputs cannot be cherry-picked by retrying.')
    if attempts.filter(case=case,run__status__in=['queued','running']).exists():
        raise ValidationError('A prior case attempt is still running or needs operator recovery.')
    if attempts.count()>=budget['max_calls'] or attempts.filter(case=case).count()>=budget['max_attempts_per_case']:
        raise ValidationError('Approved call/attempt budget exhausted.')
    pricing=version.model_configuration.pricing_version
    if pricing.effective_from>timezone.now() or (pricing.effective_until and pricing.effective_until<=timezone.now()):
        raise ValidationError('Frozen pricing is not effective for this request.')
    # UTF-8 byte count plus protocol margin is a conservative input-token bound.
    input_bound=len(prompt_bytes)+len(GRADER_SYSTEM.encode())+2048
    tokens=input_bound+budget['max_output_tokens']
    cost=estimate_cost_micros(version.model_configuration,{'input_tokens':input_bound,'output_tokens':budget['max_output_tokens']})
    totals=attempts.aggregate(tokens=Sum('reserved_tokens'),cost=Sum('reserved_cost_micros'))
    if (totals['tokens'] or 0)+tokens>budget['max_total_tokens'] or (totals['cost'] or 0)+cost>budget['max_cost_micros']:
        raise ValidationError('Approved token/cost reservation budget exhausted.')
    if attempts.filter(run__error_code='budget_exceeded').exists():
        raise ValidationError('A prior provider response exceeded its reservation; stop for review.')
    stored=_store(case,'calibration-input',{'system':GRADER_SYSTEM,'user':prompt})
    manifest={'calibration_plan':plan.pk,'plan_hash':plan.content_hash,'case':case.pk,
        'case_hash':case.content_hash,'case_payload_hash':case.payload_hash,'snapshot_hash':plan.snapshot.content_hash,
        'evaluator_hash':version.content_hash,'origin':case.origin,'blinding':'metadata-only-v1'}
    parent_id=case.origin.get('producing_run_id')
    if parent_id:
        parent=LLMRun.objects.get(pk=parent_id)
        if parent.output_hash!=case.origin['run_output_hash']:
            raise ValidationError('Frozen producing-run provenance no longer matches.')
        run,_=start_posthoc_evaluation(parent,version.model_configuration,rendered_input_hash=stored.sha256,
            rendered_input_ref=stored.reference,prompt_revision_manifest=version.prompt_manifest,
            context_manifest=manifest,idempotency_key=request_key)
    else:
        workflow,_=WorkflowDefinition.objects.get_or_create(key='evaluation',defaults={'name':'Frozen calibration','description':'Dedicated legacy-case evaluation trace'})
        config={'purpose':'frozen-case-calibration-v1'}
        workflow_version,_=WorkflowVersion.objects.get_or_create(workflow=workflow,version=1,
            defaults={'status':'approved','configuration':config,'content_hash':canonical_hash(config)})
        if workflow_version.configuration!=config:
            raise ValidationError('Dedicated evaluation workflow configuration drift.')
        trace,_=start_trace(workflow_version,trigger='operator',subject=case,actor=user,
            actor_label=f'user:{user.pk}',idempotency_key=request_key,experiment_metadata={'case_hash':case.content_hash})
        run,_=start_run(trace,version.model_configuration,purpose='evaluation',rendered_input_hash=stored.sha256,
            rendered_input_ref=stored.reference,prompt_revision_manifest=version.prompt_manifest,
            context_manifest=manifest,idempotency_key=request_key)
    attempt=CalibrationAttempt.objects.create(plan=plan,case=case,run=run,request_hash=request_hash,
        reserved_tokens=tokens,reserved_cost_micros=cost,idempotency_key=request_key,actor_label=f'user:{user.pk}')
    return attempt,prompt,True


def _telemetry(body):
    usage=body.get('usage',{})
    if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in ['input_tokens','output_tokens']):
        raise ValidationError('Provider usage is missing or invalid.')
    if usage.get('cache_creation_input_tokens',0) or usage.get('cache_read_input_tokens',0):
        raise ValidationError('Unexpected cached usage from an uncached grader request.')
    return {'input_tokens':usage['input_tokens'],'output_tokens':usage['output_tokens'],
            'total_tokens':usage['input_tokens']+usage['output_tokens']}


def grade_case(user,plan_id,case_id,*,idempotency_key,adapter=None):
    attempt,prompt,created=_reserve(user,plan_id,case_id,idempotency_key)
    if not created:
        result=CaseEvaluationResult.objects.filter(attempt=attempt).first()
        if result:
            return result,False
        raise ValidationError('Attempt is already reserved or failed. Inspect it; never silently repeat a provider call.')
    run=attempt.run
    version=attempt.plan.grader_version
    budget=attempt.plan.budget
    failure_code='grader_failed'
    try:
        raw=(adapter or AnthropicGrader())(model=version.model_configuration.model_identifier,prompt=prompt,
            max_output_tokens=budget['max_output_tokens'],timeout_seconds=budget['timeout_seconds'])
        if not isinstance(raw,bytes) or len(raw)>1048576:
            raise ValidationError('Provider response must be bounded bytes.')
        stored=_store(attempt.case,'calibration-output',raw)
        # Retain the exact provider response before interpreting its assessment.
        with transaction.atomic():
            locked=LLMRun.objects.select_for_update().get(pk=run.pk)
            locked.output_hash=stored.sha256
            locked.output_ref=stored.reference
            locked.save(update_fields=['output_hash','output_ref'])
            append_event(locked.trace,'evaluation.response_captured',run=locked,payload={'sha256':stored.sha256})
        body=json.loads(raw)
        if not isinstance(body,dict) or body.get('model') != version.model_configuration.model_identifier:
            raise ValidationError('Provider response model differs from the frozen configuration.')
        usage=_telemetry(body)
        cost=estimate_cost_micros(version.model_configuration,usage)
        with transaction.atomic():
            locked=LLMRun.objects.select_for_update().get(pk=run.pk)
            for name,value in usage.items():
                setattr(locked,name,value)
            locked.cost_micros=cost
            locked.cost_source='estimated'
            locked.cost_currency='USD'
            locked.provider_request_id=body.get('id','')
            locked.finish_reason=body.get('stop_reason','')
            locked.save()
        if usage['total_tokens']>attempt.reserved_tokens or cost>attempt.reserved_cost_micros or usage['output_tokens']>budget['max_output_tokens']:
            failure_code='budget_exceeded'
            raise ValidationError('Provider usage exceeded the approved reservation.')
        if body.get('stop_reason')!='end_turn':
            raise ValidationError('Grader output is incomplete or has an unsupported finish reason.')
        blocks=body.get('content')
        if not isinstance(blocks,list) or len(blocks)!=1 or blocks[0].get('type')!='text':
            raise ValidationError('Expected one text response and no tool calls.')
        assessment=json.loads(blocks[0]['text'])
        validate_assessment(attempt.case,version,assessment)
        with transaction.atomic():
            # Serialize result finalization with recovery and other plan operations.
            CalibrationPlan.objects.select_for_update().get(pk=attempt.plan_id)
            current=LLMRun.objects.get(pk=run.pk)
            if current.status!='running':
                raise ValidationError('Attempt was stopped before result finalization.')
            complete_run(run,output_hash=stored.sha256,output_ref=stored.reference,schema_valid=True,
                parsed_output={'assessment_hash':canonical_hash(assessment)},
                finish_reason=body['stop_reason'],provider_request_id=body.get('id',''),usage=usage,
                cost_micros=cost,cost_source='estimated',measurement_status='partial',
                measurement_unavailable_reasons=['billed_cost_unavailable','first_token_timing_unavailable'],
                finalize_trace=run.parent_run_id is None)
            attempt.run.refresh_from_db()
            result=CaseEvaluationResult.objects.create(attempt=attempt,assessment=assessment,
                summary=summarize(version.rubric,assessment['criterion_results']),actor_label=attempt.actor_label)
        return result,True
    except Exception as exc:
        # Failure is a measured execution failure, never a negative quality label.
        with transaction.atomic():
            CalibrationPlan.objects.select_for_update().get(pk=attempt.plan_id)
            fail_run(run,error_class=type(exc).__name__,error_code=failure_code,error_detail='Frozen calibration attempt failed; inspect protected audit data.',
                measurement_status='partial',measurement_unavailable_reasons=['billed_cost_unavailable','failure_usage_may_be_unavailable'])
            if run.parent_run_id is None:
                fail_trace(run.trace,reason='calibration_attempt_failed')
        raise ValidationError('Calibration attempt failed and was audited; no quality judgment was recorded.') from None


@transaction.atomic
def abandon_attempt(user,attempt_id):
    require_writes()
    attempt=CalibrationAttempt.objects.select_related('plan__snapshot__dataset','run').get(pk=attempt_id)
    authorize(user,attempt.plan.snapshot.dataset,write=True)
    CalibrationPlan.objects.select_for_update().get(pk=attempt.plan_id)
    run=LLMRun.objects.get(pk=attempt.run_id)
    if run.status not in {'running','queued'}:
        raise ValidationError('Attempt is already terminal.')
    if run.started_at and timezone.now()<run.started_at+timedelta(seconds=attempt.plan.budget['timeout_seconds']+300):
        raise ValidationError('Wait for the deadline and five-minute recovery grace period.')
    fail_run(run,error_class='OperatorRecovery',error_code='abandoned',error_detail='Operator closed an overdue attempt; reservation retained.',
             measurement_unavailable_reasons=['provider_outcome_unknown'])
    if run.parent_run_id is None:
        fail_trace(run.trace,reason='overdue_calibration_attempt')
    return attempt
