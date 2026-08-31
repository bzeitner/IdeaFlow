import hashlib
import re

from django.db import transaction
from django.conf import settings
from django.utils import timezone

from .models import (
    EvidenceAction, EvidenceAssignment, EvidenceCandidate, EvidenceExperiment,
    EvidenceObservation, Source, SourceItem, Subscription,
)


WORD_RE = re.compile(r"[a-z0-9]{3,}")


def ensure_source_for_feed(feed):
    source = Source.objects.filter(legacy_feed=feed).first()
    if source is None:
        source = Source.objects.filter(canonical_url=feed.url).first()
    values = {
        "canonical_url": feed.url,
        "title": feed.title,
        "is_active": feed.is_active,
        "fetch_policy": {
            "etag": feed.etag,
            "last_modified": feed.last_modified,
            "backfill_cutoff": feed.backfill_cutoff.isoformat() if feed.backfill_cutoff else None,
        },
    }
    if source is None:
        source = Source.objects.create(legacy_feed=feed, **values)
    else:
        for field, value in values.items():
            setattr(source, field, value)
        source.legacy_feed = feed
        source.save(update_fields=[*values, "legacy_feed", "updated_at"])
    return source


def ensure_subscription(idea_feed):
    source = ensure_source_for_feed(idea_feed.feed)
    prior = (idea_feed.rating / 5) if idea_feed.rating is not None else 0.5
    subscription = Subscription.objects.filter(legacy_idea_feed=idea_feed).first()
    if subscription is None:
        subscription = Subscription.objects.filter(
            source=source, idea=idea_feed.idea, intent="evidence"
        ).first()
    values = {
        "source": source,
        "idea": idea_feed.idea,
        "intent": "evidence",
        "relevance_prior": prior,
        "item_budget": idea_feed.idea.feed_cap,
        "is_paused": idea_feed.idea.feed_ingestion_paused,
    }
    if subscription is None:
        subscription = Subscription.objects.create(legacy_idea_feed=idea_feed, **values)
    else:
        for field, value in values.items():
            setattr(subscription, field, value)
        subscription.legacy_idea_feed = idea_feed
        subscription.save(update_fields=[*values, "legacy_idea_feed", "updated_at"])
    return subscription


def _tokens(value):
    return set(WORD_RE.findall((value or "").lower()))


def deterministic_relevance(source_item, subscription):
    idea_tokens = _tokens(f"{subscription.idea.title} {subscription.idea.summary}")
    item_tokens = _tokens(f"{source_item.title} {source_item.metadata.get('summary', '')}")
    overlap = len(idea_tokens & item_tokens) / max(1, len(idea_tokens))
    return round(min(1.0, (0.7 * overlap) + (0.3 * subscription.relevance_prior)), 6)


def _assign(candidate, experiment):
    key = f"{candidate.source_item_id}:{candidate.idea_id}"
    digest = hashlib.sha256(f"{experiment.pk}:{experiment.salt}:{key}".encode()).hexdigest()
    bucket = int(digest[:8], 16) % 100
    variant = (
        EvidenceAssignment.Variant.TREATMENT
        if bucket < experiment.treatment_percent
        else EvidenceAssignment.Variant.CONTROL
    )
    assignment, _created = EvidenceAssignment.objects.get_or_create(
        experiment=experiment,
        randomization_key=key,
        defaults={
            "candidate": candidate,
            "variant": variant,
            "assignment_hash": digest,
        },
    )
    if _created:
        _check_allocation_guardrail(experiment)
    return assignment


def _check_allocation_guardrail(experiment):
    assignments = experiment.assignments.all()
    total = assignments.count()
    if total < experiment.minimum_sample_size:
        return
    treatment = assignments.filter(variant=EvidenceAssignment.Variant.TREATMENT).count()
    expected = experiment.treatment_percent / 100
    max_imbalance = float(experiment.guardrails.get("max_exposure_imbalance", 0.15))
    if abs((treatment / total) - expected) > max_imbalance:
        EvidenceExperiment.objects.filter(
            pk=experiment.pk, state=EvidenceExperiment.State.RUNNING
        ).update(state=EvidenceExperiment.State.PAUSED)
        experiment.state = EvidenceExperiment.State.PAUSED


def _rerank(idea):
    candidates = list(
        EvidenceCandidate.objects.filter(idea=idea, source_item__eligible_for_processing=True)
        .order_by("-deterministic_score", "source_item_id")
    )
    for rank, candidate in enumerate(candidates, start=1):
        if candidate.rank != rank:
            candidate.rank = rank
            candidate.save(update_fields=["rank"])


@transaction.atomic
def sync_new_feed_item(item):
    source = ensure_source_for_feed(item.feed)
    source_item, created = SourceItem.objects.get_or_create(
        source=source,
        external_id=item.guid,
        defaults={
            "url": item.link,
            "title": item.title,
            "metadata": {"summary": item.summary, "legacy_created_at": item.created_at.isoformat()},
            "content_hash": item.content_hash or hashlib.sha256(item.content.encode()).hexdigest(),
            "published_at": item.published_at,
            "ingested_at": item.created_at,
            "eligible_for_processing": True,
            "legacy_feed_item": item,
        },
    )
    if not created or not source_item.eligible_for_processing:
        return source_item, []
    experiments = EvidenceExperiment.objects.none()
    if settings.IDEAFLOW_EXECUTION_FLAGS.get("experiments", False):
        experiments = EvidenceExperiment.objects.filter(
            state=EvidenceExperiment.State.RUNNING,
            enrollment_started_at__lte=source_item.ingested_at,
        ).order_by("pk")[:1]
    candidates = []
    for subscription in source.subscriptions.filter(is_paused=False).select_related("idea"):
        candidate = EvidenceCandidate.objects.create(
            source_item=source_item,
            subscription=subscription,
            idea=subscription.idea,
            deterministic_score=subscription.relevance_prior,
        )
        assignments = []
        for experiment in experiments:
            assignments.append(_assign(candidate, experiment))
        if not assignments or any(
            assignment.variant == EvidenceAssignment.Variant.TREATMENT
            for assignment in assignments
        ):
            candidate.deterministic_score = deterministic_relevance(source_item, subscription)
            candidate.save(update_fields=["deterministic_score"])
        _rerank(subscription.idea)
        candidates.append(candidate)
    return source_item, candidates


@transaction.atomic
def record_action(candidate, action, *, actor=None, attributed_run=None, value=1.0):
    evidence_action = EvidenceAction.objects.create(
        candidate=candidate,
        source_item=candidate.source_item,
        idea=candidate.idea,
        action=action,
        actor=actor,
        attributed_run=attributed_run,
        value=value,
    )
    metric_value = 1.0 if action in {
        EvidenceAction.Action.USEFUL,
        EvidenceAction.Action.SAVED,
        EvidenceAction.Action.CITED,
        EvidenceAction.Action.ACTION_CREATED,
    } else 0.0
    for assignment in candidate.assignments.all():
        EvidenceObservation.objects.create(
            assignment=assignment,
            action=evidence_action,
            metric=assignment.experiment.primary_metric,
            value=metric_value,
            observed_at=timezone.now(),
        )
    return evidence_action


@transaction.atomic
def sync_legacy_assessment(assessment):
    try:
        source_item = SourceItem.objects.get(legacy_feed_item=assessment.item)
        candidate = EvidenceCandidate.objects.get(
            source_item=source_item, idea=assessment.idea
        )
    except (SourceItem.DoesNotExist, EvidenceCandidate.DoesNotExist):
        return None
    action_name = (
        EvidenceAction.Action.USEFUL
        if assessment.usefulness >= 4
        else EvidenceAction.Action.IRRELEVANT
    )
    action, _created = EvidenceAction.objects.update_or_create(
        legacy_assessment=assessment,
        defaults={
            "candidate": candidate,
            "source_item": source_item,
            "idea": assessment.idea,
            "action": action_name,
            "value": assessment.usefulness / 5,
            "attributed_run": assessment.produced_by_run,
            "occurred_at": assessment.updated_at,
        },
    )
    metric_value = 1.0 if assessment.usefulness >= 4 else 0.0
    for assignment in candidate.assignments.all():
        EvidenceObservation.objects.update_or_create(
            assignment=assignment,
            action=action,
            defaults={
                "metric": assignment.experiment.primary_metric,
                "value": metric_value,
                "observed_at": assessment.updated_at,
            },
        )
    return action
