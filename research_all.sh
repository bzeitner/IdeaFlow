#!/usr/bin/env bash
#
# Slowly research every IdeaFlow idea, once each.
#
#   ./research_all.sh [--status current|tracking|archived] [--delay SECONDS]
#                     [--force] [--dry-run]
#
# For each idea it runs ./research_idea.sh <id> (a full headless agent run),
# then waits --delay seconds before the next one. Ideas that already have a
# research entry are skipped, so the pass is "once each" and a re-run only
# picks up whatever is still untouched. --force re-runs them anyway.

set -euo pipefail
cd "$(dirname "$0")"

STATUS=""
DELAY=90
FORCE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status) STATUS="${2:-}"; shift 2 ;;
    --delay)  DELAY="${2:-}";  shift 2 ;;
    --force)  FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "$DELAY" =~ ^[0-9]+$ ]]; then
  echo "error: --delay must be a whole number of seconds." >&2
  exit 2
fi

PY=".venv/bin/python"

# Pick the ideas to work on: by default only those with zero research entries,
# in id order. Status/force are passed via env to avoid shell-quoting them into
# the Python source.
CANDIDATES=$(
  IF_STATUS="$STATUS" IF_FORCE="$FORCE" "$PY" manage.py shell --no-imports -c '
import os
from django.db.models import Count
from ideas.models import Idea
qs = Idea.objects.annotate(_n=Count("research_entries"))
status = os.environ.get("IF_STATUS")
if status:
    qs = qs.filter(status=status)
if os.environ.get("IF_FORCE") != "1":
    qs = qs.filter(_n=0)
print(" ".join(str(i) for i in qs.order_by("id").values_list("id", flat=True)))
' 2>/dev/null)

# shellcheck disable=SC2206
IDS=($CANDIDATES)

if [[ ${#IDS[@]} -eq 0 ]]; then
  echo "Nothing to do — no matching ideas need research."
  echo "(Use --force to re-run ideas that already have research, or --status to widen/narrow.)"
  exit 0
fi

note=""
[[ -n "$STATUS" ]] && note+=", status=$STATUS"
[[ "$FORCE" -eq 1 ]] && note+=", force (re-running already-researched ideas)"

echo "Ideas to research (${#IDS[@]}): ${IDS[*]}"
echo "Pacing: ${DELAY}s between runs${note}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry run — not launching anything)"
  exit 0
fi

fail=0
for i in "${!IDS[@]}"; do
  id="${IDS[$i]}"
  echo
  echo "=== [$((i + 1))/${#IDS[@]}] researching idea ${id} ==="
  if ./research_idea.sh "$id"; then
    echo "=== idea ${id} done ==="
  else
    echo "!!! idea ${id} failed (continuing) !!!" >&2
    fail=$((fail + 1))
  fi
  # Cool down before the next one, but not after the last.
  if [[ $((i + 1)) -lt ${#IDS[@]} ]]; then
    echo "--- waiting ${DELAY}s before the next idea ---"
    sleep "$DELAY"
  fi
done

echo
echo "All done: ${#IDS[@]} attempted, ${fail} failed."
[[ "$fail" -eq 0 ]]
