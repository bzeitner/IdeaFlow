"""Trusted local operator/reviewer workflow. No web or machine-token endpoint."""
import json
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from evaluations import calibration as api
from evaluations.grading import grade_case, abandon_attempt
from evaluations.datasets import authorize
from evaluations.models import CalibrationPlan, DatasetSnapshot, EvaluatorVersion
from .evaluation_dataset import read_json, private_json
from executions.services import canonical_hash


class Command(BaseCommand):
    help='Prepare and run explicitly budgeted frozen-case calibration; real calls require model_graders flag.'

    def add_arguments(self,parser):
        parser.add_argument('operation',choices=['publish-grader','plan-preview','plan','packet','adjudication-packet','review','adjudicate','grade','recover','report','approve-report'])
        parser.add_argument('--user-id',type=int,required=True)
        parser.add_argument('--plan-id',type=int)
        parser.add_argument('--case-id',type=int)
        parser.add_argument('--attempt-id',type=int)
        parser.add_argument('--report-id',type=int)
        parser.add_argument('--request-file')
        parser.add_argument('--output-file')
        parser.add_argument('--idempotency-key')
        parser.add_argument('--approve-hash')
        parser.add_argument('--human-attested',action='store_true')
        parser.add_argument('--reason')

    def handle(self,*args,**options):
        op=options['operation']
        required={'publish-grader':['request_file'],'plan-preview':['request_file','output_file'],
            'plan':['request_file','approve_hash','idempotency_key'],'packet':['plan_id','case_id','output_file'],
            'adjudication-packet':['plan_id','case_id','output_file'],
            'review':['plan_id','case_id','request_file','idempotency_key','human_attested'],
            'adjudicate':['plan_id','case_id','request_file','idempotency_key','human_attested'],
            'grade':['plan_id','case_id','idempotency_key'],'recover':['attempt_id'],
            'report':['plan_id','output_file'],'approve-report':['report_id','reason']}
        if any(not options[key] for key in required[op]):
            raise CommandError('Required options: '+', '.join(required[op]))
        try:
            user=get_user_model().objects.get(pk=options['user_id'])
            data=read_json(options['request_file']) if options['request_file'] else {}
            if op=='publish-grader':
                row,created=api.publish_grader(user,data['human_version_id'],data['configuration_id'],data['version_number'])
                result={'evaluator_version_id':row.pk,'hash':row.content_hash,'created':created,'decision_grade':False}
            elif op=='plan-preview':
                api.require_writes()
                snapshot=DatasetSnapshot.objects.get(pk=data['snapshot_id'])
                authorize(user,snapshot.dataset,write=True)
                data['execution_binding']=api.execution_binding(EvaluatorVersion.objects.get(pk=data['grader_version_id']))
                model_data={**data}
                model_data['supersedes_id']=model_data.pop('supersedes_plan_id',None)
                candidate=CalibrationPlan(**model_data,actor_label=f'user:{user.pk}',idempotency_key='preview')
                candidate.clean()
                result={'plan':data,'approval_hash':canonical_hash(data),
                    'notice':'Approve exact reviewers, held-out thresholds, model/pricing binding and maximum budgets before creating this plan. No provider call or human label has been created.'}
            elif op=='plan':
                row,created=api.create_plan(user,**data['plan'],approved_hash=options['approve_hash'],idempotency_key=options['idempotency_key'])
                result={'plan_id':row.pk,'hash':row.content_hash,'created':created}
            elif op in {'packet','adjudication-packet'}:
                fn=api.review_packet if op=='packet' else api.adjudication_packet
                result=fn(user,options['plan_id'],options['case_id'])
            elif op in {'review','adjudicate'}:
                row,created=api.submit_review(user,options['plan_id'],options['case_id'],data,
                    human_attested=options['human_attested'],idempotency_key=options['idempotency_key'],adjudication=op=='adjudicate')
                result={'review_id':row.pk,'label_id':row.label_id,'created':created}
            elif op=='grade':
                row,created=grade_case(user,options['plan_id'],options['case_id'],idempotency_key=options['idempotency_key'])
                result={'result_id':row.pk,'attempt_id':row.attempt_id,'run_id':str(row.attempt.run_id),'created':created}
            elif op=='recover':
                row=abandon_attempt(user,options['attempt_id'])
                result={'attempt_id':row.pk,'reservation_retained':True}
            elif op=='report':
                row,created=api.create_report(user,options['plan_id'])
                result={'report_id':row.pk,'hash':row.content_hash,'input_manifest':row.input_manifest,'metrics':row.metrics,'eligible_for_approval':row.eligible,'created':created}
            else:
                row,created=api.approve_report(user,options['report_id'],reason=options['reason'])
                result={'approval_id':row.pk,'created':created}
            if options['output_file']:
                private_json(options['output_file'],result)
                self.stdout.write(json.dumps({'written':options['output_file']}))
            else:
                self.stdout.write(json.dumps(result))
        except (ObjectDoesNotExist,PermissionDenied,ValidationError,OSError,ValueError,TypeError,KeyError):
            raise CommandError('Calibration operation rejected; inspect permissions, frozen inputs, explicit approvals, budgets and audit status. No sensitive content is printed.') from None
