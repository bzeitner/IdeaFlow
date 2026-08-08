"""Turn ideas into plain JSON-able dicts for the agent-facing command + API.

Both the `dump_idea` management command and the `/api/` views serialize through
here, so the shape an agent reads is identical however it connects.
"""


def _lookup(obj):
    """A Category/Stage/AIModel as {name, slug}, or None when unset."""
    return {"name": obj.name, "slug": obj.slug} if obj else None


def resource_to_dict(resource):
    return {
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
        "effort": entry.effort,
        "quality": entry.quality,
        "model": _lookup(entry.model),
        "tokens_used": entry.tokens_used,
        "occurred_at": entry.occurred_at.isoformat(),
        "created_at": entry.created_at.isoformat(),
    }


def feed_item_to_dict(item):
    return {
        "id": item.id,
        "feed_id": item.feed_id,
        "guid": item.guid,
        "title": item.title,
        "link": item.link,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "summary": item.summary,
        "summary_model": _lookup(item.summary_model),
        "summarized_at": item.summarized_at.isoformat() if item.summarized_at else None,
        "usefulness": item.usefulness,
        "interest": item.interest,
        "info_value": item.info_value,
    }


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


def idea_to_dict(idea, *, detail=True):
    """Serialize an idea. `detail=False` omits the heavy related collections
    (notes, resources, research entries) for list responses."""
    data = {
        "id": idea.id,
        "title": idea.title,
        "summary": idea.summary,
        "category": _lookup(idea.category),
        "interest_level": idea.interest_level,
        "status": idea.status,
        "is_public": idea.is_public,
        "stage": _lookup(idea.stage),
        "rank": idea.rank,
        "url": idea.get_absolute_url(),
        "created_at": idea.created_at.isoformat(),
        "updated_at": idea.updated_at.isoformat(),
    }
    if detail:
        data["notes"] = idea.notes
        data["next_action"] = idea.next_action
        data["resources"] = [resource_to_dict(r) for r in idea.resources.all()]
        data["research_entries"] = [
            research_entry_to_dict(e) for e in idea.research_entries.all()
        ]
    return data
