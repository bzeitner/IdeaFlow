import hashlib
import json

from django.db import transaction

from ideas.models import (
    Feed, FeedItem, FeedItemAssessment, IdeaFeed, IdeaRelation,
    IdeaRelationSuggestion, RelationshipCouncilReview, RepeatResult,
)

from .models import EvidenceAction, EvidenceCandidate, LegacyEntitySnapshot, SourceItem
from .services import ensure_source_for_feed, ensure_subscription


def _digest(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _snapshot(entity_type, legacy_id, payload):
    payload = json.loads(json.dumps(payload, default=str))
    snapshot, created = LegacyEntitySnapshot.objects.get_or_create(
        entity_type=entity_type,
        legacy_id=legacy_id,
        defaults={"content_hash": _digest(payload), "payload": payload},
    )
    return snapshot, created


@transaction.atomic
def import_legacy_phase3():
    counts = {key: 0 for key in (
        "sources", "subscriptions", "items", "feedback", "relations",
        "suggestions", "reviews", "repeat_results",
    )}
    for feed in Feed.objects.all().iterator():
        _source = ensure_source_for_feed(feed)
        counts["sources"] += 1
    for link in IdeaFeed.objects.select_related("feed", "idea").iterator():
        ensure_subscription(link)
        counts["subscriptions"] += 1
    for item in FeedItem.objects.select_related("feed").iterator():
        source = ensure_source_for_feed(item.feed)
        _source_item, _created = SourceItem.objects.get_or_create(
            legacy_feed_item=item,
            defaults={
                "source": source,
                "external_id": item.guid,
                "url": item.link,
                "title": item.title,
                "metadata": {
                    "summary": item.summary,
                    "interest": item.interest,
                    "info_value": item.info_value,
                    "legacy_created_at": item.created_at.isoformat(),
                },
                "content_hash": item.content_hash or hashlib.sha256(item.content.encode()).hexdigest(),
                "published_at": item.published_at,
                "ingested_at": item.created_at,
                "eligible_for_processing": False,
            },
        )
        counts["items"] += 1
    for assessment in FeedItemAssessment.objects.select_related("item", "idea").iterator():
        source_item = SourceItem.objects.get(legacy_feed_item=assessment.item)
        subscription = source_item.source.subscriptions.filter(idea=assessment.idea).first()
        candidate = None
        if subscription:
            candidate, _created = EvidenceCandidate.objects.get_or_create(
                source_item=source_item,
                idea=assessment.idea,
                defaults={
                    "subscription": subscription,
                    "deterministic_score": (assessment.usefulness / 5),
                    "decision": "included" if assessment.usefulness >= 4 else "filtered",
                },
            )
        EvidenceAction.objects.update_or_create(
            legacy_assessment=assessment,
            defaults={
                "candidate": candidate,
                "source_item": source_item,
                "idea": assessment.idea,
                "action": "useful" if assessment.usefulness >= 4 else "irrelevant",
                "value": assessment.usefulness / 5,
                "attributed_run": assessment.produced_by_run,
                "occurred_at": assessment.updated_at,
            },
        )
        counts["feedback"] += 1
    for relation in IdeaRelation.objects.all().iterator():
        payload = {
            "source_id": relation.source_id, "target_id": relation.target_id,
            "relation_type": relation.relation_type, "description": relation.description,
            "confidence": relation.confidence, "provenance": relation.provenance,
        }
        _snapshot("idea_relation", relation.pk, payload)
        counts["relations"] += 1
    for suggestion in IdeaRelationSuggestion.objects.all().iterator():
        payload = {
            "source_id": suggestion.source_id, "target_id": suggestion.target_id,
            "relation_type": suggestion.relation_type, "status": suggestion.status,
            "confidence": suggestion.confidence, "evidence": suggestion.evidence,
            "produced_by_run_id": str(suggestion.produced_by_run_id or ""),
        }
        _snapshot("relation_suggestion", suggestion.pk, payload)
        counts["suggestions"] += 1
    for review in RelationshipCouncilReview.objects.prefetch_related("votes").iterator(chunk_size=500):
        payload = {
            "suggestion_id": review.suggestion_id, "outcome": review.outcome,
            "produced_by_run_id": str(review.produced_by_run_id or ""),
            "votes": [
                {
                    "persona_id": vote.persona_id, "provider": vote.provider,
                    "model": vote.model, "decision": vote.decision,
                    "rationale": vote.rationale,
                    "produced_by_run_id": str(vote.produced_by_run_id or ""),
                }
                for vote in review.votes.all()
            ],
        }
        _snapshot("relationship_review", review.pk, payload)
        counts["reviews"] += 1
    for result in RepeatResult.all_objects.all().iterator():
        payload = {
            "idea_id": result.idea_id, "title": result.title, "url": result.url,
            "details": result.details, "status": result.status,
            "episode_id": result.episode_id, "found_at": result.found_at,
            "deleted_at": result.deleted_at,
            "produced_by_run_id": str(result.produced_by_run_id or ""),
        }
        _snapshot("repeat_result", result.pk, payload)
        counts["repeat_results"] += 1
    return counts
