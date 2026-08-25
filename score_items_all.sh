#!/usr/bin/env bash
#
# Run score_items.sh for every repeat-enabled, unpaused idea, not just one
# hardcoded id. score_items.sh's assessments are per-idea (it writes both a
# shared global FeedItem summary and an idea-specific FeedItemAssessment via
# --idea), so an idea whose feed items never get scored never gets candidates
# either — no matter how well its feed roster is chosen. This is the loop
# ideaflow-score-items.service now drives, instead of a single idea id.
#
#   IDEAFLOW_API_TOKEN=... ./score_items_all.sh [options]
#
# Options (passed through to each score_items.sh run):
#   --limit N         items per idea per run (default 25; 0 for the whole queue)
#   --min-rating N    only feeds an idea rates >= N (default 4)
#   --since-days N    recency window (default 30; undated items are kept)
#   --dry-run         print each idea's queue, score nothing
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IFCLI="$SCRIPT_DIR/tools/ideaflow"

if [[ -z "${IDEAFLOW_API_TOKEN:-}" ]]; then
  echo "error: set IDEAFLOW_API_TOKEN (the IdeaFlow API bearer token)." >&2
  exit 1
fi

IDS="$(
  "$IFCLI" list-ideas | python3 -c '
import json, sys
ideas = json.load(sys.stdin)["ideas"]
for idea in ideas:
    if idea.get("repeat_enabled") and not idea.get("repeat_paused"):
        print(idea["id"])
'
)"

if [[ -z "$IDS" ]]; then
  echo "No repeat-enabled, unpaused ideas found — nothing to score." >&2
  exit 0
fi

FAILED=()
while read -r id; do
  [[ -z "$id" ]] && continue
  echo "=== idea ${id} ===" >&2
  if ! "$SCRIPT_DIR/score_items.sh" "$id" "$@"; then
    echo "warning: scoring idea ${id} failed; continuing with the rest." >&2
    FAILED+=("$id")
  fi
done <<< "$IDS"

if [[ "${#FAILED[@]}" -gt 0 ]]; then
  echo "Done, with failures for idea(s): ${FAILED[*]}" >&2
  exit 1
fi
echo "Done." >&2
