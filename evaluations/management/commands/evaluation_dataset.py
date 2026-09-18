"""Local trusted operator entry point; no machine bearer-token impersonation."""
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from evaluations import datasets
from evaluations.models import DatasetCase, EvaluationDataset
from evaluations.security import MAX_METADATA_BYTES
from django.utils import timezone


def read_json(path):
    with Path(path).open('rb') as source:
        data = source.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise ValidationError('Dataset request exceeds 64 KiB; use bounded redacted excerpts.')
    return json.loads(data)


def private_json(path, value):
    # Exclusive creation prevents overwriting files or following existing symlinks.
    data = json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write(data)


class Command(BaseCommand):
    help = 'Create, preview, approve/freeze, export and retire protected A4 datasets. No provider calls.'

    def add_arguments(self, parser):
        parser.add_argument('operation', choices=['create', 'sample', 'preview', 'freeze', 'snapshot', 'export', 'delete-content', 'purge-expired'])
        parser.add_argument('--user-id', type=int, required=True, help='Active local operator with operate_datasets permission; recorded in audit.')
        parser.add_argument('--dataset-id', type=int)
        parser.add_argument('--case-id', type=int)
        parser.add_argument('--snapshot-id', type=int)
        parser.add_argument('--request-file', help='Bounded JSON request; see R5A operator guide.')
        parser.add_argument('--output-file', help='New private file; required for content-bearing operations.')
        parser.add_argument('--approve-hash', help='Exact preview hash explicitly approved after review.')
        parser.add_argument('--idempotency-key')
        parser.add_argument('--reason', choices=['expired', 'required_deletion'])

    def handle(self, *args, **options):
        operation = options['operation']
        if operation in {'sample', 'preview', 'export'} and not options['output_file']:
            raise CommandError('A new --output-file is required; protected content is never printed to stdout.')
        required = {
            'create': ['request_file'], 'sample': ['dataset_id', 'request_file'],
            'preview': ['dataset_id', 'request_file'],
            'freeze': ['dataset_id', 'request_file', 'approve_hash', 'idempotency_key'],
            'snapshot': ['dataset_id', 'request_file', 'idempotency_key'],
            'export': ['snapshot_id'], 'delete-content': ['case_id', 'reason'],
            'purge-expired': ['dataset_id'],
        }
        if any(not options[k] for k in required[operation]):
            raise CommandError('Missing required options: ' + ', '.join(required[operation]))
        try:
            user = get_user_model().objects.get(pk=options['user_id'])
            datasets.authorize(user)
            request = read_json(options['request_file']) if options['request_file'] else {}
            dataset_id = options['dataset_id']
            if operation == 'create':
                fields = {'key', 'purpose', 'eligibility_policy', 'redaction_policy', 'retention_days'}
                if not isinstance(request, dict) or set(request) != fields:
                    raise ValidationError('Dataset policy fields do not match the required schema.')
                row = datasets.create_dataset(user, **request)
                result = {'dataset_id': row.pk, 'hash': row.content_hash}
            elif operation == 'sample':
                result = datasets.sample_research(user, dataset_id, request['entry_ids'])
            elif operation == 'preview':
                result = datasets.preview_case(user, dataset_id, request)
            elif operation == 'freeze':
                row, created = datasets.freeze_case(user, dataset_id, request['proposal'],
                    approval_token=request['approval_token'], approved_hash=options['approve_hash'],
                    idempotency_key=options['idempotency_key'])
                result = {'case_id': row.pk, 'revision': row.revision, 'hash': row.content_hash, 'created': created}
            elif operation == 'snapshot':
                row, created = datasets.create_snapshot(user, dataset_id, request['case_ids'],
                    request['sampling_rules'], idempotency_key=options['idempotency_key'])
                result = {'snapshot_id': row.pk, 'hash': row.content_hash, 'created': created}
            elif operation == 'export':
                result = datasets.export_snapshot(user, options['snapshot_id'])
            elif operation == 'delete-content':
                row = datasets.delete_case_content(user, options['case_id'], reason=options['reason'])
                result = {'tombstone_id': row.pk, 'case_id': row.case_id}
            else:
                dataset = EvaluationDataset.objects.get(pk=dataset_id)
                datasets.authorize(user, dataset, write=True)
                ids = list(DatasetCase.objects.filter(dataset=dataset, expires_at__lte=timezone.now(),
                    protected_content__isnull=False).values_list('pk', flat=True))
                for pk in ids:
                    datasets.delete_case_content(user, pk, reason='expired')
                result = {'deleted_case_ids': ids}
            if options['output_file']:
                private_json(options['output_file'], result)
                self.stdout.write(json.dumps({'written': options['output_file']}))
            else:
                self.stdout.write(json.dumps(result))
        except (ObjectDoesNotExist, PermissionDenied, ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
            # Do not echo parser errors or exception details containing reviewed content.
            raise CommandError(f'Dataset operation failed ({type(exc).__name__}); check permissions, request schema, hashes and policy.') from None
