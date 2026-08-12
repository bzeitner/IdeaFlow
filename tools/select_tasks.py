#!/usr/bin/env python3
"""Pick the work list for research_all.sh — prints one "<id> <mode>" per line.

Reads the deployed data through the tools/ideaflow client (path in argv[1]) and
applies the default selection:
  * every idea with no research yet     -> research
  * every idea with a next action set   -> review
  * researched ideas without a next action are skipped; continue to the next
    actionable idea instead of re-analyzing idle work

Env: IF_STATUS (tab filter), IF_FORCE (=1 research all), IF_REVIEW (=1 review
all researched).

If IF_STATE_FILE is set, writes a JSON explanation of the selection, including
why a pass has no actionable work.

Kept as a standalone file (not an inline heredoc) because bash 3.2 mis-parses a
heredoc nested inside a process substitution.
"""

import json
import os
import subprocess
import sys


def main():
    cli = sys.argv[1]
    status = os.environ.get("IF_STATUS") or None
    force = os.environ.get("IF_FORCE") == "1"
    review = os.environ.get("IF_REVIEW") == "1"

    def run(*args):
        return json.loads(subprocess.check_output([cli, *args]))

    listing = ["list-ideas"] + (["--status", status] if status else [])
    listed_ideas = run(*listing)["ideas"]
    detail = {it["id"]: run("dump-idea", str(it["id"])) for it in listed_ideas}
    # Never work archived ideas, and skip paused ones (they need human feedback
    # first). The effort API rejects both anyway; this keeps agents off them.
    archived_ids = [it["id"] for it in listed_ideas if it.get("status") == "archived"]
    paused_ids = [
        it["id"]
        for it in listed_ideas
        if it.get("status") != "archived" and detail[it["id"]].get("is_paused")
    ]
    ideas = [
        it
        for it in listed_ideas
        if it.get("status") != "archived" and not detail[it["id"]].get("is_paused")
    ]

    def has_research(i):
        return bool(detail[i].get("research_entries"))

    def has_next(i):
        return bool((detail[i].get("next_action") or "").strip())

    selected, seen = [], set()

    def add(i, mode):
        if i not in seen:
            selected.append((i, mode))
            seen.add(i)

    idle_ids = []
    if force:
        for it in ideas:
            add(it["id"], "research")
    elif review:
        for it in ideas:
            if has_research(it["id"]):
                add(it["id"], "review")
    else:
        def has_open_pr(i):
            for r in detail[i].get("resources") or []:
                if "/pull/" in (r.get("url") or "") or "pr" in (r.get("label") or "").lower():
                    return True
            return False

        def mode_for(it):
            i = it["id"]
            na = (detail[i].get("next_action") or "").strip().lower()
            if na.startswith("critical pr review"):
                return "critique"                 # a PR is waiting for a critical pass
            if detail[i].get("repo") and na and has_research(i) and not has_open_pr(i):
                return "execute"                  # repo-targeted, ready to build
            if not has_research(i):
                return "research"
            if has_next(i):
                return "review"
            return None                           # researched, idle → skip

        for it in ideas:
            m = mode_for(it)
            if m is not None:
                add(it["id"], m)
            else:
                idle_ids.append(it["id"])

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
    state = {
        "reason": reason,
        "listed": len(listed_ideas),
        "actionable": len(selected),
        "archived_ids": archived_ids,
        "paused_ids": paused_ids,
        "idle_ids": idle_ids,
        "skipped_ids": skipped_ids,
        "status_filter": status,
    }
    state_file = os.environ.get("IF_STATE_FILE")
    if state_file:
        with open(state_file, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

    # Emit "<id> <mode> <title>" — the title is the rest of the line.
    for i, mode in selected:
        print(i, mode, (detail[i].get("title") or "").replace("\n", " "))


if __name__ == "__main__":
    main()
