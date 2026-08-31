"""Stable Phase 0 registries and production-baseline measurements.

This module is intentionally free of provider calls and runtime side effects.
The registries define the vocabulary that later execution-ledger migrations
will persist. Changing a key is a data migration, not a cosmetic refactor.
"""

from dataclasses import asdict, dataclass

from django.db.models import Avg, Count, Max, Q, Sum
from django.utils import timezone


REGISTRY_VERSION = "1.0.0"


@dataclass(frozen=True)
class CallSite:
    key: str
    workflow: str
    purpose: str
    entrypoint: str
    owner: str
    prompt_keys: tuple[str, ...]
    providers: tuple[str, ...]
    primary_outcome: str
    migration_order: int


CALL_SITES = (
    CallSite("agent-research", "research", "generation", "research_idea.sh research", "portfolio", ("agent-research", "shared-standards"), ("claude", "codex"), "research.accepted_7d", 2),
    CallSite("agent-review", "review", "generation", "research_idea.sh review", "portfolio", ("agent-review", "shared-standards"), ("claude", "codex"), "review.disposition_accepted", 2),
    CallSite("agent-execute", "execute", "generation", "research_idea.sh execute", "delivery", ("agent-execute", "shared-standards"), ("claude", "codex"), "execute.change_merged", 7),
    CallSite("agent-critique", "critique", "generation", "research_idea.sh critique", "delivery", ("agent-critique", "shared-standards"), ("claude", "codex"), "critique.finding_actioned", 7),
    CallSite("agent-summary", "summary", "generation", "research_idea.sh summary", "portfolio", ("agent-summary", "shared-standards"), ("claude", "codex"), "summary.accepted_without_edit", 4),
    CallSite("agent-repeat", "repeat", "generation", "research_idea.sh repeat", "discovery", ("agent-repeat", "shared-standards"), ("claude", "codex"), "repeat.result_actioned_30d", 4),
    CallSite("portfolio-reflection", "reflection", "generation", "research_all.sh --reflect", "portfolio", ("agent-portfolio-reflection", "shared-standards"), ("claude", "codex"), "reflection.action_adopted_7d", 6),
    CallSite("feed-score", "feed_score", "classification", "score_items.sh", "discovery", ("agent-feed-scoring", "shared-standards"), ("claude",), "evidence.precision_at_5", 1),
    CallSite("weekly-summary", "weekly_summary", "generation", "weekly_summary.sh", "reporting", ("agent-weekly-summary", "shared-standards"), ("claude", "codex"), "weekly_summary.accepted_without_refresh", 5),
    CallSite("relationship-classifier", "relationship_classification", "classification", "manage.py process_semantic_graph", "graph", ("semantic-relationship-classification",), ("openai-compatible",), "relationship.accepted_precision", 3),
    CallSite("relationship-council-vote", "relationship_council", "evaluation", "tools/review_relationships.py", "graph", ("relationship-council-review", "shared-standards"), ("claude", "codex"), "relationship.council_human_agreement", 3),
    CallSite("open-question-single", "open_question_extraction", "extraction", "manage.py extract_open_questions --use-ai", "portfolio", ("open-question-single",), ("openai-compatible",), "question.accepted_precision", 4),
    CallSite("open-question-batch", "open_question_extraction", "extraction", "tools/extract_open_questions_remote.py", "portfolio", ("open-question-batch",), ("openai-compatible",), "question.accepted_precision", 4),
    CallSite("persona-council", "persona_council", "evaluation", "research_idea.sh persona", "portfolio", ("agent-persona", "shared-standards"), ("claude", "codex"), "persona.proposal_retained_30d", 5),
    CallSite("podcast-script", "podcast_script", "generation", "research_idea.sh repeat (podcast)", "publishing", ("agent-repeat", "shared-standards"), ("claude", "codex"), "podcast.episode_published_30d", 6),
)


@dataclass(frozen=True)
class Metric:
    key: str
    unit: str
    direction: str
    window_days: int | None
    description: str


METRICS = (
    Metric("run.completion_rate", "ratio", "higher", None, "Terminal successful runs divided by terminal runs."),
    Metric("run.schema_valid_rate", "ratio", "higher", None, "Successful outputs passing the declared schema."),
    Metric("run.retry_rate", "ratio", "lower", None, "Traces requiring more than one provider attempt."),
    Metric("run.end_to_end_ms", "milliseconds", "lower", None, "Queue creation through terminal completion."),
    Metric("run.total_tokens", "tokens", "lower", None, "All reported token classes for a run."),
    Metric("run.cost_micros", "currency_micros", "lower", None, "Provider-reported or price-table-estimated cost."),
    Metric("research.accepted_7d", "ratio", "higher", 7, "Research accepted or used without rejection within seven days."),
    Metric("review.disposition_accepted", "ratio", "higher", 7, "Review disposition retained by the user."),
    Metric("summary.accepted_without_edit", "ratio", "higher", 7, "Summary accepted without material editing."),
    Metric("weekly_summary.accepted_without_refresh", "ratio", "higher", 7, "Weekly summary retained without an explicit refresh."),
    Metric("reflection.action_adopted_7d", "ratio", "higher", 7, "Portfolio reflection recommendation adopted within seven days."),
    Metric("evidence.precision_at_5", "ratio", "higher", 7, "Useful, saved, cited, or actioned items among five exposed results."),
    Metric("relationship.accepted_precision", "ratio", "higher", 30, "Suggested relationships accepted by council or human review."),
    Metric("relationship.council_human_agreement", "ratio", "higher", 30, "Council decision agreement with later human review."),
    Metric("question.accepted_precision", "ratio", "higher", 30, "Extracted questions retained or answered rather than removed."),
    Metric("persona.proposal_retained_30d", "ratio", "higher", 30, "Persona-council proposal not reversed within thirty days."),
    Metric("repeat.result_actioned_30d", "ratio", "higher", 30, "Discovered results marked actioned within thirty days."),
    Metric("podcast.episode_published_30d", "ratio", "higher", 30, "Generated episode scripts that reach publication."),
    Metric("execute.change_merged", "ratio", "higher", 30, "Execution changes merged or explicitly accepted."),
    Metric("critique.finding_actioned", "ratio", "higher", 30, "Critique findings that result in a verified change."),
)


OUTCOME_EVENTS = (
    "output.exposed", "output.accepted", "output.edited", "output.rejected",
    "next_action.adopted", "next_action.completed", "evidence.useful",
    "evidence.irrelevant", "evidence.saved", "evidence.cited",
    "evidence.action_created", "evidence.dismissed", "relationship.accepted",
    "relationship.rejected", "relationship.removed", "repeat.interested",
    "repeat.actioned", "repeat.dismissed", "episode.regenerated",
    "episode.published", "change.opened", "change.merged", "change.rejected",
)


def registry_payload():
    return {
        "version": REGISTRY_VERSION,
        "call_sites": [asdict(item) for item in CALL_SITES],
        "metrics": [asdict(item) for item in METRICS],
        "outcome_events": list(OUTCOME_EVENTS),
    }


def validate_registries():
    """Return validation errors without raising, suitable for checks/tests."""
    errors = []
    for label, values in (
        ("call site", [item.key for item in CALL_SITES]),
        ("metric", [item.key for item in METRICS]),
        ("outcome", list(OUTCOME_EVENTS)),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            errors.append(f"Duplicate {label} keys: {', '.join(duplicates)}")
    metric_keys = {item.key for item in METRICS}
    for call_site in CALL_SITES:
        if call_site.primary_outcome not in metric_keys:
            errors.append(
                f"Call site {call_site.key} references unknown metric "
                f"{call_site.primary_outcome}."
            )
    return errors


def _counts(queryset, field):
    return list(queryset.values(field).annotate(count=Count("id")).order_by(field))


def production_baseline():
    """Build a JSON-serializable, read-only snapshot from the active database."""
    from .models import (
        Artifact, Category, Episode, EpisodeRun, Feed, FeedItem,
        FeedItemAssessment, Idea, IdeaFeed, IdeaRelation,
        IdeaRelationSuggestion, PersonaReview, PromptRevision, PromptTemplate,
        RelationshipCouncilReview, RepeatResult, ResearchEntry, WeeklySummary,
    )

    generated_at = timezone.now()
    item_total = FeedItem.objects.count()
    assessment_total = FeedItemAssessment.objects.count()
    summarized_total = FeedItem.objects.exclude(summarized_at=None).count()
    interest_total = FeedItem.objects.exclude(interest=None).count()
    info_total = FeedItem.objects.exclude(info_value=None).count()

    def ratio(numerator, denominator):
        return round(numerator / denominator, 6) if denominator else None

    return {
        "schema_version": "1.0.0",
        "registry_version": REGISTRY_VERSION,
        "generated_at": generated_at.isoformat(),
        "ideas": {
            "total": Idea.objects.count(),
            "by_status": _counts(Idea.objects.all(), "status"),
            "by_stage": _counts(Idea.objects.all(), "stage__name"),
            "by_category": list(
                Category.objects.annotate(
                    count=Count("ideas"), latest_update=Max("ideas__updated_at")
                ).values("name", "slug", "is_active", "is_research", "count", "latest_update")
            ),
        },
        "research": {
            "total": ResearchEntry.objects.count(),
            "latest": ResearchEntry.objects.aggregate(value=Max("occurred_at"))["value"],
            "tokens_known": ResearchEntry.objects.exclude(tokens_used=None).count(),
            "tokens_total": ResearchEntry.objects.aggregate(value=Sum("tokens_used"))["value"] or 0,
            "quality_average": ResearchEntry.objects.aggregate(value=Avg("quality"))["value"],
            "by_model": list(
                ResearchEntry.objects.values(
                    "execution_provider", "execution_model", "model__name"
                ).annotate(count=Count("id"), tokens=Sum("tokens_used"), quality=Avg("quality"))
                .order_by("-count")
            ),
        },
        "feeds": {
            "feeds": Feed.objects.count(),
            "active_feeds": Feed.objects.filter(is_active=True).count(),
            "idea_feed_links": IdeaFeed.objects.count(),
            "ideas_with_feeds": IdeaFeed.objects.values("idea_id").distinct().count(),
            "items": item_total,
            "summarized": summarized_total,
            "summarized_ratio": ratio(summarized_total, item_total),
            "assessed": assessment_total,
            "assessed_ratio": ratio(assessment_total, item_total),
            "human_interest_rated": interest_total,
            "human_interest_ratio": ratio(interest_total, item_total),
            "human_info_rated": info_total,
            "human_info_ratio": ratio(info_total, item_total),
            "usefulness": _counts(FeedItemAssessment.objects.all(), "usefulness"),
            "high_usefulness": FeedItemAssessment.objects.filter(usefulness__gte=4).count(),
        },
        "graph": {
            "relations": IdeaRelation.objects.count(),
            "suggestions": IdeaRelationSuggestion.objects.count(),
            "suggestions_by_status": _counts(IdeaRelationSuggestion.objects.all(), "status"),
            "council_reviews": RelationshipCouncilReview.objects.count(),
            "council_outcomes": _counts(RelationshipCouncilReview.objects.all(), "outcome"),
            "persona_reviews": PersonaReview.objects.count(),
            "persona_statuses": _counts(PersonaReview.objects.all(), "status"),
        },
        "repeat": {
            "active_results": RepeatResult.objects.count(),
            "all_results": RepeatResult.all_objects.count(),
            "by_status": _counts(RepeatResult.objects.all(), "status"),
        },
        "podcast": {
            "episodes": Episode.objects.count(),
            "episodes_by_status": _counts(Episode.objects.all(), "status"),
            "runs": EpisodeRun.objects.count(),
            "runs_by_status": _counts(EpisodeRun.objects.all(), "status"),
            "episodes_with_regeneration": Episode.objects.annotate(
                run_count=Count("runs")
            ).filter(run_count__gt=1).count(),
        },
        "governance": {
            "prompt_templates": PromptTemplate.objects.count(),
            "prompt_revisions": PromptRevision.objects.count(),
            "prompt_revisions_by_status": _counts(PromptRevision.objects.all(), "status"),
        },
        "other": {
            "artifacts": Artifact.objects.count(),
            "weekly_summaries": WeeklySummary.objects.count(),
        },
        "measurement_limitations": [
            "Legacy rows do not identify exact rendered prompts or prompt revisions.",
            "Research token totals are caller-reported and are not split by token class.",
            "Legacy records do not contain provider latency or normalized cost.",
            "Missing human ratings are not equivalent to negative feedback.",
            "Podcast regeneration is inferred from episodes with more than one render run.",
        ],
    }
