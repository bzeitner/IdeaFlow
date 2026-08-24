"""Pure task-selection rules shared by the batch runner and admin preview."""

from datetime import datetime


BUILD_ACTION_TERMS = {
    "build", "create", "develop", "implement", "prototype", "scaffold", "ship",
}


def is_build_action(value):
    words = {word.strip(".,:;!?()[]{}") for word in value.lower().split()}
    return bool(words.intersection(BUILD_ACTION_TERMS))


def council_acted_after_latest_entry(idea):
    entries = idea.get("research_entries") or []
    if not entries:
        return False
    latest = entries[0].get("occurred_at") or entries[0].get("created_at") or ""
    reviews = (idea.get("persona_review") or {}).get("recent_reviews") or []
    return any(
        review.get("status") == "consensus"
        and (review.get("created_at") or "") > latest
        for review in reviews
    )


def awaiting_direction_after_review(idea):
    entries = idea.get("research_entries") or []
    if not entries or (entries[0].get("topic") or "").strip().lower() != "review & synthesis":
        return False
    if idea.get("agent_runs_since_feedback") == 0:
        return False
    return not council_acted_after_latest_entry(idea)


def select_work(listed_ideas, detail, *, status=None, force=False, review=False):
    """Return (selected [(idea_id, mode)], state) for a research_all pass."""
    archived_ids = [it["id"] for it in listed_ideas if it.get("status") == "archived"]
    paused_ids = [
        it["id"] for it in listed_ideas
        if it.get("status") != "archived"
        and detail[it["id"]].get("is_paused")
        and not council_acted_after_latest_entry(detail[it["id"]])
    ]
    ideas = [
        it for it in listed_ideas
        if it.get("status") != "archived"
        and (not detail[it["id"]].get("is_paused")
             or council_acted_after_latest_entry(detail[it["id"]]))
    ]

    selected_repeat = next((
        (it["id"], "repeat") for it in listed_ideas
        if it.get("status") != "archived"
        and (detail[it["id"]].get("repeat_task") or {}).get("enabled")
        and not (detail[it["id"]].get("repeat_task") or {}).get("paused")
        and (detail[it["id"]].get("repeat_task") or {}).get("is_due")
    ), None)
    persona_due_ids = [
        it["id"] for it in listed_ideas
        if it.get("status") != "archived"
        and (detail[it["id"]].get("persona_review") or {}).get("is_due")
    ]

    def has_research(i):
        return bool(detail[i].get("research_entries"))

    def has_next(i):
        return bool((detail[i].get("next_action") or "").strip())

    def has_new_signal(i):
        times = [entry.get("occurred_at") for entry in detail[i].get("research_entries") or [] if entry.get("occurred_at")]
        latest = max((datetime.fromisoformat(value) for value in times), default=None)
        progress = (detail[i].get("persona_review") or {}).get("last_meaningful_progress_at")
        return bool(progress and latest and datetime.fromisoformat(progress) > latest)

    selected, seen = [], set()

    def add(i, mode):
        if i not in seen:
            selected.append((i, mode))
            seen.add(i)

    for it in listed_ideas:
        if detail[it["id"]].get("summary_requested_at"):
            add(it["id"], "summary")
    if selected_repeat:
        add(*selected_repeat)
    for i in persona_due_ids:
        add(i, "persona")

    idle_ids = []
    if force:
        for it in ideas:
            add(it["id"], "research")
    elif review:
        for it in ideas:
            if has_research(it["id"]) and not awaiting_direction_after_review(detail[it["id"]]):
                add(it["id"], "review")
    else:
        def has_open_pr(i):
            return any("/pull/" in (r.get("url") or "") or "pr" in (r.get("label") or "").lower() for r in detail[i].get("resources") or [])

        def mode_for(it):
            i = it["id"]
            action = (detail[i].get("next_action") or "").strip().lower()
            if action.startswith("critical pr review"):
                return "critique"
            if (detail[i].get("persona_review") or {}).get("is_due"):
                return "persona"
            if detail[i].get("repo") and is_build_action(action) and has_research(i) and not has_open_pr(i):
                return "execute"
            if not has_research(i):
                return "research"
            if has_next(i):
                return None if awaiting_direction_after_review(detail[i]) else "review"
            if has_new_signal(i):
                return "review"
            return None

        for it in ideas:
            mode = mode_for(it)
            if mode is None:
                idle_ids.append(it["id"])
            else:
                add(it["id"], mode)

    skipped_ids = [it["id"] for it in ideas if it["id"] not in seen]
    if not listed_ideas:
        reason = "no_ideas"
    elif selected:
        reason = "actionable"
    elif (force or review) and ideas:
        reason = "mode_filtered"
    elif ideas:
        reason = "idle"
    else:
        reason = "unavailable"
    return selected, {
        "reason": reason, "listed": len(listed_ideas), "actionable": len(selected),
        "archived_ids": archived_ids, "paused_ids": paused_ids,
        "idle_ids": idle_ids, "skipped_ids": skipped_ids, "status_filter": status,
    }
