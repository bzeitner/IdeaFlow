#!/usr/bin/env python3
"""Pick the work list for research_all.sh — prints one "<id> <mode>" per line.

Reads the deployed data through the tools/ideaflow client (path in argv[1]) and
applies the default selection:
  * every idea with no research yet     -> research
  * every idea with a next action set   -> review
  * if fewer than IF_MIN, top up with the highest interest-level ideas
    not already picked                  -> review (or research if still new)

Env: IF_STATUS (tab filter), IF_FORCE (=1 research all), IF_REVIEW (=1 review
all researched), IF_MIN (minimum tasks, default 5).

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
    try:
        minimum = int(os.environ.get("IF_MIN", "5"))
    except ValueError:
        minimum = 5

    def run(*args):
        return json.loads(subprocess.check_output([cli, *args]))

    listing = ["list-ideas"] + (["--status", status] if status else [])
    ideas = run(*listing)["ideas"]
    detail = {it["id"]: run("dump-idea", str(it["id"])) for it in ideas}

    def has_research(i):
        return bool(detail[i].get("research_entries"))

    def has_next(i):
        return bool((detail[i].get("next_action") or "").strip())

    selected, seen = [], set()

    def add(i, mode):
        if i not in seen:
            selected.append((i, mode))
            seen.add(i)

    if force:
        for it in ideas:
            add(it["id"], "research")
    elif review:
        for it in ideas:
            if has_research(it["id"]):
                add(it["id"], "review")
    else:
        for it in ideas:                          # 1. new ideas
            if not has_research(it["id"]):
                add(it["id"], "research")
        for it in ideas:                          # 2. ideas with a next action
            if has_next(it["id"]):
                add(it["id"], "review")
        if len(selected) < minimum:               # 3. top up by interest level
            rest = [it for it in ideas if it["id"] not in seen]
            rest.sort(key=lambda it: it.get("interest_level", 0), reverse=True)
            for it in rest:
                if len(selected) >= minimum:
                    break
                add(it["id"], "review" if has_research(it["id"]) else "research")

    for i, mode in selected:
        print(i, mode)


if __name__ == "__main__":
    main()
