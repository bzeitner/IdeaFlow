"""A4 protected dataset services. No provider calls or generation writes."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from executions.services import canonical_hash
from .interactions import target_for, check_identity, save_once
from .models import (EvaluationDataset, DatasetCase, DatasetCaseContent,
                     DatasetCaseTombstone, DatasetSnapshot, EvaluatorVersion,
                     HumanCalibrationLabel)
from .security import validate_metadata

SALT = 'evaluation-dataset-preview-v1'


def authorize(user, dataset=None, *, write=False):
    if not getattr(user, 'pk', None):
        raise PermissionDenied('An active dataset operator is required.')
    user = get_user_model().objects.get(pk=user.pk)
    if not user.is_active or not user.has_perm('evaluations.operate_datasets'):
        raise PermissionDenied('Dataset operator permission is required.')
    if dataset and not (user.is_superuser or dataset.owner_user_id == user.pk):
        raise PermissionDenied('Dataset belongs to another operator.')
    if write and not settings.IDEAFLOW_EXECUTION_FLAGS.get('datasets', False):
        raise PermissionDenied('Dataset writers are disabled.')
    return user


def verified(record):
    if record.content_hash != record.fingerprint():
        raise ValidationError('Dataset audit hash mismatch.')
    return record


def validate_assignments(assignments):
    if not isinstance(assignments, list) or not assignments:
        raise ValidationError('Exact rubric assignments are required.')
    seen = set()
    for row in assignments:
        if not isinstance(row, dict) or set(row) != {'id', 'hash', 'rubric_key'}:
            raise ValidationError('Invalid rubric assignment.')
        version = verified(EvaluatorVersion.objects.get(pk=row['id']))
        if row['id'] in seen or row['hash'] != version.content_hash or row['rubric_key'] != version.applicability.get('rubric_key') or 'research' not in version.applicability.get('workflows', []):
            raise ValidationError('Rubric identity, hash, or applicability mismatch.')
        seen.add(row['id'])


def validate_payload(payload):
    validate_metadata(payload)
    if not isinstance(payload, dict) or set(payload) != {'objective', 'prior_state', 'output', 'evidence', 'unavailable', 'exclusions'}:
        raise ValidationError('Payload requires objective, prior_state, output, evidence, unavailable, and exclusions.')
    for key in ('objective', 'prior_state', 'output'):
        if payload[key] is not None and not isinstance(payload[key], str):
            raise ValidationError('Case text must be a string or explicitly unavailable.')
    if not isinstance(payload['unavailable'], dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in payload['unavailable'].items()):
        raise ValidationError('Unavailable evidence requires explicit reasons.')
    for key in ('objective', 'prior_state', 'output'):
        if not payload[key] and not payload['unavailable'].get(key):
            raise ValidationError('Missing inputs require unavailable reasons.')
    if not isinstance(payload['evidence'], list) or not isinstance(payload['exclusions'], list) or not all(isinstance(x, str) and x.strip() for x in payload['exclusions']):
        raise ValidationError('Evidence and exclusions must be explicit lists.')
    refs = set()
    for evidence in payload['evidence']:
        if not isinstance(evidence, dict) or set(evidence) != {'ref', 'excerpt', 'hash'}:
            raise ValidationError('Evidence requires a reference, redacted excerpt, and exact hash.')
        if not isinstance(evidence['ref'], str) or not evidence['ref'].strip() or evidence['ref'] in refs or not isinstance(evidence['excerpt'], str) or evidence['hash'] != canonical_hash(evidence['excerpt']):
            raise ValidationError('Evidence identity or hash mismatch.')
        refs.add(evidence['ref'])
    if not payload['evidence'] and not payload['unavailable'].get('evidence'):
        raise ValidationError('Missing source evidence requires an explicit reason.')


def create_dataset(user, **values):
    user = authorize(user, write=True)
    dataset = EvaluationDataset(actor_label=f'user:{user.pk}', owner_user_id=user.pk, **values)
    existing = EvaluationDataset.objects.filter(key=dataset.key).first()
    if existing:
        authorize(user, existing)
        if existing.content_hash != dataset.fingerprint():
            raise ValidationError('Dataset key already identifies another policy.')
        return existing
    try:
        with transaction.atomic():
            dataset.save()
    except IntegrityError:
        existing = EvaluationDataset.objects.filter(key=dataset.key).first()
        if existing is None:
            raise
        authorize(user, existing)
        if verified(existing).content_hash != dataset.fingerprint():
            raise ValidationError('Dataset key already identifies another policy.')
        return existing
    return dataset


def sample_research(user, dataset_id, entry_ids):
    dataset = verified(EvaluationDataset.objects.get(pk=dataset_id))
    user = authorize(user, dataset)
    if not isinstance(entry_ids, list) or not 1 <= len(entry_ids) <= 30 or len(set(entry_ids)) != len(entry_ids):
        raise ValidationError('Select 1–30 explicit research entry IDs per sample.')
    rows = []
    for pk in entry_ids:
        entry, identity, _ = target_for(user, 'research', pk)
        if not identity['producing_run_id'] and not dataset.eligibility_policy['allow_legacy']:
            raise ValidationError('Dataset eligibility excludes outputs with unavailable provenance.')
        rows.append({'origin': identity, 'topic': entry.topic, 'focus': entry.focus,
                     'output': entry.context, 'prior_state': None,
                     'unavailable': {'prior_state': 'Not captured by projection sampling.',
                                     'evidence': 'Requires separately reviewed source excerpts.'}})
    return rows


def preview_case(user, dataset_id, proposal, *, _lock_source=False):
    dataset = verified(EvaluationDataset.objects.get(pk=dataset_id))
    user = authorize(user, dataset, write=True)
    validate_metadata(proposal)
    required = {'case_key', 'origin', 'payload', 'rubric_assignments', 'cohorts', 'split', 'evidence_cutoff'}
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise ValidationError('Unexpected or missing case proposal fields.')
    _, identity, _ = target_for(user, 'research', proposal['origin']['target_id'], lock=_lock_source)
    if not identity['producing_run_id'] and not dataset.eligibility_policy['allow_legacy']:
        raise ValidationError('Dataset eligibility excludes outputs with unavailable provenance.')
    check_identity(identity, proposal['origin'])
    validate_payload(proposal['payload'])
    validate_assignments(proposal['rubric_assignments'])
    cutoff = parse_datetime(proposal['evidence_cutoff'])
    if cutoff is None or timezone.is_naive(cutoff) or cutoff > timezone.now():
        raise ValidationError('Evidence cutoff must be an aware, nonfuture timestamp.')
    # Validate field formats before issuing approval capability.
    candidate = DatasetCase(dataset=dataset, case_key=proposal['case_key'], revision=1,
        actor_label=f'user:{user.pk}', origin=identity, rubric_assignments=proposal['rubric_assignments'],
        cohorts=proposal['cohorts'], split=proposal['split'], evidence_cutoff=cutoff,
        expires_at=timezone.now() + timedelta(days=dataset.retention_days),
        payload_hash=canonical_hash(proposal['payload']), approval_hash=canonical_hash(proposal),
        idempotency_key='preview')
    candidate.content_hash = candidate.fingerprint()
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    digest = canonical_hash(proposal)
    token = signing.dumps({'actor': user.pk, 'dataset': dataset.pk, 'dataset_hash': dataset.content_hash,
                           'approval_hash': digest}, salt=SALT)
    return {'proposal': proposal, 'approval_hash': digest, 'approval_token': token,
            'redaction_policy': dataset.redaction_policy,
            'notice': 'Review all content and exclusions. Approval attests redaction and eligibility; no automated redaction guarantee.'}


@transaction.atomic
def freeze_case(user, dataset_id, proposal, *, approval_token, approved_hash, idempotency_key):
    dataset = verified(EvaluationDataset.objects.select_for_update().get(pk=dataset_id))
    user = authorize(user, dataset, write=True)
    digest = canonical_hash(proposal)
    actor = f'user:{user.pk}'
    key = canonical_hash({'dataset': dataset.pk, 'actor': user.pk, 'request': idempotency_key})
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 160:
        raise ValidationError('A bounded idempotency key is required.')
    if approved_hash != digest:
        raise ValidationError('Approval must identify the exact reviewed proposal hash.')
    existing = DatasetCase.objects.filter(idempotency_key=key).first()
    if existing:
        if verified(existing).approval_hash != digest:
            raise ValidationError('Request key identifies different content.')
        return existing, False
    try:
        approval = signing.loads(approval_token, salt=SALT, max_age=86400)
    except signing.BadSignature as exc:
        raise ValidationError('Approval preview expired or is invalid; preview again.') from exc
    if approval != {'actor': user.pk, 'dataset': dataset.pk, 'dataset_hash': dataset.content_hash, 'approval_hash': digest}:
        raise ValidationError('Preview belongs to different content, actor, or dataset.')
    preview_case(user, dataset.pk, proposal, _lock_source=True)
    if DatasetCase.objects.filter(dataset=dataset, origin__target_id=proposal['origin']['target_id']).exclude(case_key=proposal['case_key']).exists():
        raise ValidationError('Use a revision of the existing case for this source; do not duplicate it across splits.')
    prior = DatasetCase.objects.filter(dataset=dataset, case_key=proposal['case_key']).order_by('-revision').first()
    case = DatasetCase.objects.create(dataset=dataset, case_key=proposal['case_key'],
        revision=prior.revision + 1 if prior else 1, supersedes=prior, actor_label=actor,
        origin=proposal['origin'], rubric_assignments=proposal['rubric_assignments'],
        cohorts=proposal['cohorts'], split=proposal['split'],
        evidence_cutoff=parse_datetime(proposal['evidence_cutoff']),
        expires_at=timezone.now() + timedelta(days=dataset.retention_days),
        payload_hash=canonical_hash(proposal['payload']), approval_hash=digest, idempotency_key=key)
    DatasetCaseContent.objects.create(case=case, payload=proposal['payload'])
    return case, True


def case_content(case):
    verified(case)
    if DatasetCaseTombstone.objects.filter(case=case).exists():
        return None, 'deleted'
    if case.expires_at <= timezone.now():
        return None, 'expired'
    content = DatasetCaseContent.objects.filter(case=case).first()
    if content is None:
        return None, 'missing'
    if canonical_hash(content.payload) != case.payload_hash:
        raise ValidationError('Frozen case content failed hash verification.')
    validate_payload(content.payload)
    return content.payload, 'available'


@transaction.atomic
def create_snapshot(user, dataset_id, case_ids, sampling_rules, *, idempotency_key):
    dataset = verified(EvaluationDataset.objects.select_for_update().get(pk=dataset_id))
    user = authorize(user, dataset, write=True)
    if not isinstance(case_ids, list) or not 1 <= len(case_ids) <= 200:
        raise ValidationError('Select 1–200 case revisions.')
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 160:
        raise ValidationError('A bounded idempotency key is required.')
    cases = [DatasetCase.objects.get(pk=pk, dataset=dataset) for pk in case_ids]
    manifest = {'schema_version': 1, 'dataset_hash': dataset.content_hash,
                'redaction_policy': dataset.redaction_policy, 'sampling_rules': sampling_rules,
                'cases': [{'id': c.pk, 'hash': verified(c).content_hash, 'payload_hash': c.payload_hash} for c in cases]}
    key = canonical_hash({'dataset': dataset.pk, 'actor': user.pk, 'snapshot_request': idempotency_key})
    values = dict(dataset=dataset, manifest=manifest, actor_label=f'user:{user.pk}', idempotency_key=key)
    existing = DatasetSnapshot.objects.filter(idempotency_key=key).first()
    if existing:
        return save_once(DatasetSnapshot, **values)
    for case in cases:
        if case_content(case)[1] != 'available':
            raise ValidationError('New snapshots require available case content.')
    return save_once(DatasetSnapshot, **values)


@transaction.atomic
def export_snapshot(user, snapshot_id):
    snapshot = verified(DatasetSnapshot.objects.get(pk=snapshot_id))
    dataset = verified(EvaluationDataset.objects.select_for_update().get(pk=snapshot.dataset_id))
    authorize(user, dataset)
    snapshot.clean()
    rows = []
    rubric_ids = set()
    for item in snapshot.manifest['cases']:
        case = DatasetCase.objects.get(pk=item['id'])
        payload, status = case_content(case)
        validate_assignments(case.rubric_assignments)
        rubric_ids.update(row['id'] for row in case.rubric_assignments)
        tombstone = DatasetCaseTombstone.objects.filter(case=case).first()
        rows.append({'id': case.pk, 'metadata': case.audit_values(), 'hash': case.content_hash,
                     'status': status, 'payload': payload,
                     'tombstone': {'metadata': verified(tombstone).audit_values(), 'hash': tombstone.content_hash} if tombstone else None})
    rubrics = []
    for pk in sorted(rubric_ids):
        version = verified(EvaluatorVersion.objects.select_related('metric').get(pk=pk))
        metric = verified(version.metric)
        rubrics.append({'id': version.pk, 'metadata': version.audit_values(), 'hash': version.content_hash,
                       'metric': {'id': metric.pk, 'metadata': metric.audit_values(), 'hash': metric.content_hash}})
    return {'schema_version': 1, 'snapshot_id': snapshot.pk, 'manifest': snapshot.manifest,
            'snapshot_metadata': snapshot.audit_values(), 'snapshot_hash': snapshot.content_hash,
            'dataset_metadata': dataset.audit_values(), 'dataset_hash': dataset.content_hash,
            'cases': rows, 'rubrics': rubrics, 'exported_at': timezone.now().isoformat(),
            'reproducible': all(c['status'] == 'available' for c in rows)}


@transaction.atomic
def delete_case_content(user, case_id, *, reason):
    case = DatasetCase.objects.get(pk=case_id)
    dataset = EvaluationDataset.objects.select_for_update().get(pk=case.dataset_id)
    user = authorize(user, dataset, write=True)
    if reason not in {'expired', 'required_deletion'} or (reason == 'expired' and case.expires_at > timezone.now()):
        raise ValidationError('Invalid deletion reason or case has not expired.')
    tombstone = DatasetCaseTombstone.objects.filter(case=case).first()
    if not tombstone:
        tombstone = DatasetCaseTombstone.objects.create(case=case, reason=reason, actor_label=f'user:{user.pk}')
    DatasetCaseContent.objects.filter(case=case).delete()
    return tombstone


def validate_label(label):
    version = label.evaluator_version
    case = label.case
    if label.actor_label != f'user:{label.reviewer_user_id}' or version.method != 'human':
        raise ValidationError('Human labels require an attributed human reviewer and human rubric.')
    if {'id': version.pk, 'hash': version.content_hash, 'rubric_key': version.applicability.get('rubric_key')} not in case.rubric_assignments:
        raise ValidationError('Label rubric must match the frozen case assignment.')
    criteria = {c['id']: c for c in version.rubric['criteria']}
    rows = label.criterion_results
    if not isinstance(rows, list) or len(rows) != len(criteria):
        raise ValidationError('Every rubric criterion needs an explicit judgment or abstention.')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'status'} or row['id'] not in criteria or row['id'] in seen or row['status'] not in {'pass', 'fail', 'insufficient_evidence', 'not_applicable'}:
            raise ValidationError('Invalid criterion label.')
        seen.add(row['id'])
    judgeable = all(r['status'] in {'pass', 'fail'} for r in rows if criteria[r['id']]['severity'] != 'optional')
    if version.metric.unit == 'ordinal':
        if judgeable and (type(label.progress_score) is not int or not 1 <= label.progress_score <= 5):
            raise ValidationError('Judgeable progress requires a 1–5 integer score.')
        if not judgeable and label.progress_score is not None:
            raise ValidationError('Incomplete progress labels must abstain.')
    elif label.progress_score is not None:
        raise ValidationError('Quality labels cannot become progress scores.')
    payload, state = case_content(case)
    if state != 'available':
        raise ValidationError('Unavailable cases cannot receive new labels.')
    refs = {'objective', 'prior_state', 'output'} - set(payload['unavailable'])
    refs |= {e['ref'] for e in payload['evidence']}
    if not isinstance(label.supporting_refs, list) or not all(isinstance(r, str) and r in refs for r in label.supporting_refs):
        raise ValidationError('Label references must identify frozen available evidence.')
    if any(r['status'] in {'pass', 'fail'} for r in rows) and not label.supporting_refs:
        raise ValidationError('Observed judgments require supporting references.')
    if not isinstance(label.adjudicates, list) or len(set(label.adjudicates)) != len(label.adjudicates):
        raise ValidationError('Invalid adjudication links.')
    if label.adjudicates:
        originals = list(HumanCalibrationLabel.objects.filter(pk__in=label.adjudicates, case=case, evaluator_version=version))
        if len(originals) != len(label.adjudicates) or len({r.reviewer_user_id for r in originals}) < 2 or any(r.adjudicates for r in originals):
            raise ValidationError('Adjudication requires independent original labels for this case and rubric.')
