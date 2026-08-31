"""Turn ideas into plain JSON-able dicts for the agent-facing command + API.

Both the `dump_idea` management command and the `/api/` views serialize through
here, so the shape an agent reads is identical however it connects.
"""

from .podcast_policy import minimum_script_word_count


def _lookup(obj):
    """A Category/Stage/AIModel as {name, slug}, or None when unset."""
    return {"name": obj.name, "slug": obj.slug} if obj else None


def resource_to_dict(resource):
    return {
        "id": resource.id,
        "label": resource.label,
        "url": resource.url,
        "created_at": resource.created_at.isoformat(),
    }


def research_entry_to_dict(entry):
    return {
        "id": entry.id,
        "topic": entry.topic,
        "focus": entry.focus,
        "context": entry.context,
        "open_questions": entry.open_questions,
        "question_answers": [
            {"question": item["question"], "answer": item["answer"]}
            for item in entry.open_question_items
            if item["answer"].strip()
        ],
        "effort": entry.effort,
        "quality": entry.quality,
        "model": _lookup(entry.model),
        "execution_provider": entry.execution_provider,
        "execution_model": entry.execution_model,
        "execution_run_id": str(entry.produced_by_run_id) if entry.produced_by_run_id else None,
        "tokens_used": entry.tokens_used,
        "occurred_at": entry.occurred_at.isoformat(),
        "created_at": entry.created_at.isoformat(),
    }


def feed_item_to_dict(item, *, content=False, idea=None):
    data = {
        "id": item.id,
        "feed_id": item.feed_id,
        "guid": item.guid,
        "title": item.title,
        "link": item.link,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "summary": item.summary,
        "summary_model": _lookup(item.summary_model),
        "summarized_at": item.summarized_at.isoformat() if item.summarized_at else None,
        "summary_execution_run_id": (
            str(item.summarized_by_run_id) if item.summarized_by_run_id else None
        ),
        "interest": item.interest,
        "info_value": item.info_value,
    }
    if content:
        data["content"] = item.content
    if idea is not None:
        assessment = next(
            (a for a in item.assessments.all() if a.idea_id == idea.pk), None
        )
        data["assessment"] = (
            {
                "usefulness": assessment.usefulness,
                "relevance_note": assessment.relevance_note,
                "execution_run_id": (
                    str(assessment.produced_by_run_id)
                    if assessment.produced_by_run_id else None
                ),
            }
            if assessment
            else None
        )
    return data


def feed_to_dict(feed, *, detail=False):
    data = {
        "id": feed.id,
        "url": feed.url,
        "title": feed.title,
        "is_active": feed.is_active,
        "last_fetched_at": feed.last_fetched_at.isoformat()
        if feed.last_fetched_at
        else None,
    }
    if detail:
        data["items"] = [feed_item_to_dict(i) for i in feed.items.all()]
    return data


def podcast_show_to_dict(show):
    """None when the idea has no associated podcast show — the agent-facing
    signal for "this is an ordinary repeat task, not a podcast" in
    research_idea.sh's repeat mode."""
    if show is None:
        return None
    from .models import VoiceProfile

    return {
        "id": show.id,
        "title": show.title,
        "slug": show.slug,
        "default_tts_engine": show.default_tts_engine,
        "target_episode_duration_seconds": show.target_episode_duration_seconds,
        "minimum_script_word_count": minimum_script_word_count(
            show.target_episode_duration_seconds
        ),
        "voice_profiles": [
            {"name": vp.name, "speaker_label": vp.speaker_label}
            for vp in VoiceProfile.objects.filter(is_active=True).order_by("name")
        ],
    }


def idea_to_dict(idea, *, detail=True):
    """Serialize an idea. `detail=False` omits the heavy related collections
    (notes, resources, research entries) for list responses."""
    data = {
        "id": idea.id,
        "title": idea.title,
        "summary": idea.summary,
        "created_by": (
            {"id": idea.created_by_id, "email": idea.created_by.email}
            if idea.created_by_id
            else None
        ),
        "category": _lookup(idea.category),
        "interest_level": idea.interest_level,
        "status": idea.status,
        "is_public": idea.is_public,
        "exec_summary": idea.exec_summary,
        "stage": _lookup(idea.stage),
        "rank": idea.rank,
        "url": idea.get_absolute_url(),
        "created_at": idea.created_at.isoformat(),
        "updated_at": idea.updated_at.isoformat(),
        # Cheap scalars (no extra query), unlike the heavier repeat_task
        # object below — kept available even in the detail=False list view so
        # a caller can filter for repeat-enabled ideas without a full dump.
        "repeat_enabled": idea.repeat_enabled,
        "repeat_paused": idea.repeat_paused,
    }
    if detail:
        from django.db.models import F

        from .feeds import recent_articles

        data["notes"] = idea.notes
        data["summary_requested_at"] = (
            idea.summary_requested_at.isoformat() if idea.summary_requested_at else None
        )
        data["next_action"] = idea.next_action
        data["next_actions"] = idea.next_action_queue
        data["repeat_task"] = {
            "enabled": idea.repeat_enabled,
            "paused": idea.repeat_paused,
            "goal": idea.repeat_goal,
            "target_count": idea.repeat_target_count,
            "interval_days": idea.repeat_interval_days,
            "last_run_at": idea.last_repeat_run_at.isoformat() if idea.last_repeat_run_at else None,
            "is_due": idea.repeat_is_due,
        }
        data["repeat_results"] = [
            {"id": r.id, "title": r.title, "url": r.url, "details": r.details,
             "status": r.status, "found_at": r.found_at.isoformat()}
            for r in idea.repeat_results.all()
        ]
        data["podcast_show"] = podcast_show_to_dict(getattr(idea, "podcast_show", None))
        data["repo"] = idea.repo
        data["parent"] = (
            {"id": idea.parent_id, "title": idea.parent.title} if idea.parent_id else None
        )
        data["children"] = [
            {"id": c.id, "title": c.title, "status": c.status}
            for c in idea.children.all()
        ]
        data["suggested_children"] = idea.suggested_children
        data["agent_runs_since_feedback"] = idea.agent_runs_since_feedback
        data["is_paused"] = idea.is_paused
        data["persona_review"] = {
            "enabled": idea.persona_review_enabled,
            "paused": idea.persona_review_paused,
            "stall_days": idea.persona_stall_days,
            "last_meaningful_progress_at": idea.last_meaningful_progress_at.isoformat(),
            "last_review_at": (
                idea.last_persona_review_at.isoformat()
                if idea.last_persona_review_at else None
            ),
            "is_due": idea.persona_review_is_due,
            "personas": [
                {
                    "id": assignment.persona_id,
                    "name": assignment.persona.name,
                    "description": assignment.persona.description,
                    "goals": assignment.persona.goals,
                    "constraints": assignment.persona.constraints,
                    "required": assignment.required,
                }
                for assignment in idea.idea_personas.all()
                if assignment.active and assignment.persona.is_active
            ],
            "recent_reviews": [
                {
                    "id": review.pk,
                    "status": review.status,
                    "proposal": review.proposal,
                    "created_at": review.created_at.isoformat(),
                    "votes": [
                        {
                            "persona_id": vote.persona_id,
                            "persona": vote.persona.name,
                            "decision": vote.decision,
                            "rationale": vote.rationale,
                        }
                        for vote in review.votes.all()
                    ],
                }
                for review in list(idea.persona_reviews.all())[:10]
            ],
        }
        data["feed_cap"] = idea.feed_cap
        data["resources"] = [resource_to_dict(r) for r in idea.resources.all()]
        data["artifacts"] = [
            {
                "id": artifact.pk,
                "title": artifact.title,
                "kind": artifact.kind,
                "description": artifact.description,
                "url": artifact.link,
                "research_entry_id": artifact.research_entry_id,
                "generated_at": artifact.generated_at.isoformat(),
                "updated_at": artifact.updated_at.isoformat(),
            }
            for artifact in idea.artifacts.all()
        ]
        data["referenced_artifacts"] = [
            {
                "id": artifact.pk,
                "title": artifact.title,
                "kind": artifact.kind,
                "description": artifact.description,
                "url": artifact.link,
                "source_idea": {
                    "id": artifact.idea_id,
                    "title": artifact.idea.title,
                },
                "generated_at": artifact.generated_at.isoformat(),
                "updated_at": artifact.updated_at.isoformat(),
            }
            for artifact in idea.referenced_artifacts.all()
        ]
        data["research_entries"] = [
            research_entry_to_dict(e) for e in idea.research_entries.all()
        ]
        links = idea.idea_feeds.select_related("feed").order_by(
            F("rating").desc(nulls_last=True), "-created_at"
        )
        data["feeds"] = [{**feed_to_dict(link.feed), "rating": link.rating} for link in links]
        data["recent_articles"] = [
            feed_item_to_dict(i, idea=idea) for i in recent_articles(idea)
        ]
    return data
