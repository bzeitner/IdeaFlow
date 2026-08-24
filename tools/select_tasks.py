#!/usr/bin/env python3
"""Pick the work list for research_all.sh and print "<id> <mode> <title>"."""

import json
import os
import subprocess
import sys

try:
    from .task_selection import select_work
except ImportError:  # Executed directly by research_all.sh.
    from task_selection import select_work


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
    selected, state = select_work(
        listed_ideas, detail, status=status, force=force, review=review
    )

    state_file = os.environ.get("IF_STATE_FILE")
    if state_file:
        with open(state_file, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

    for idea_id, mode in selected:
        print(idea_id, mode, (detail[idea_id].get("title") or "").replace("\n", " "))


if __name__ == "__main__":
    main()
