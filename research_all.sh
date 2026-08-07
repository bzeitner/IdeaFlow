#!/usr/bin/env bash
#
# Slowly research every IdeaFlow idea, once each, against the DEPLOYED app.
#
#   IDEAFLOW_API_TOKEN=... ./research_all.sh [--status current|tracking|archived]
#                                            [--delay SECONDS] [--force] [--dry-run]
#
# For each idea it runs ./research_idea.sh <id> (a full headless agent run),
# then waits --delay seconds. Ideas that already have a research entry are
# skipped (via the HTTP API), so the pass is "once each"; --force re-runs them.
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
IFCLI="$SCRIPT_DIR/tools/ideaflow"

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
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "${IDEAFLOW_API_TOKEN:-}" ]]; then
  echo "error: set IDEAFLOW_API_TOKEN (the IdeaFlow API bearer token)." >&2
  exit 1
fi
if ! [[ "$DELAY" =~ ^[0-9]+$ ]]; then
  echo "error: --delay must be a whole number of seconds." >&2
  exit 2
fi

# Candidate ids: all matching ideas (with --force) or only those with no
# research entries yet. Uses the client, so it reflects the deployed data.
# (read loop rather than mapfile — macOS still ships bash 3.2)
IDS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && IDS+=("$line")
done < <(
  IF_STATUS="$STATUS" IF_FORCE="$FORCE" python3 - "$IFCLI" <<'PY'
import json, os, subprocess, sys
cli = sys.argv[1]
status = os.environ.get("IF_STATUS") or None
force = os.environ.get("IF_FORCE") == "1"

def run(*a):
    return json.loads(subprocess.check_output([cli, *a]))

listing = ["list-ideas"] + (["--status", status] if status else [])
for it in run(*listing)["ideas"]:
    if force:
        print(it["id"])
    elif not run("dump-idea", str(it["id"])).get("research_entries"):
        print(it["id"])
PY
)

if [[ ${#IDS[@]} -eq 0 ]]; then
  echo "Nothing to do — no matching ideas need research."
  echo "(Use --force to re-run ideas that already have research, or --status to narrow.)"
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
  if [[ $((i + 1)) -lt ${#IDS[@]} ]]; then
    echo "--- waiting ${DELAY}s before the next idea ---"
    sleep "$DELAY"
  fi
done

echo
echo "All done: ${#IDS[@]} attempted, ${fail} failed."
[[ "$fail" -eq 0 ]]
