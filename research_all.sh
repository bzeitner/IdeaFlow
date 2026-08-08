#!/usr/bin/env bash
#
# Keep an agent doing useful work across all IdeaFlow ideas, against the
# DEPLOYED app. Tiered by default:
#
#   1. Research every idea that has no research yet (research mode).
#   2. If none are left, review/synthesize the already-researched ideas
#      (review mode) — updating each idea's next action.
#   3. If there are no ideas at all, reflect on the project as a whole.
#
#   IDEAFLOW_API_TOKEN=... ./research_all.sh [options]
#
# Options:
#   --status current|tracking|archived   limit to one tab
#   --delay SECONDS                       cooldown between runs (default 90)
#   --review                              force the review pass over researched ideas
#   --force                               research ALL ideas (ignore existing research)
#   --reflect                             just run the project-level reflection
#   --dry-run                             show the plan, launch nothing
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
IFCLI="$SCRIPT_DIR/tools/ideaflow"
BASE="${IDEAFLOW_API_BASE:-https://ideaflow.bitesoftheweek.com}"

STATUS=""
DELAY=90
FORCE=0
REVIEW=0
REFLECT=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status) STATUS="${2:-}"; shift 2 ;;
    --delay)  DELAY="${2:-}";  shift 2 ;;
    --force)  FORCE=1; shift ;;
    --review) REVIEW=1; shift ;;
    --reflect) REFLECT=1; shift ;;
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

run_reflection() {
  echo "No ideas to work on — reflecting on the project."
  if [[ "$DRY_RUN" -eq 1 ]]; then echo "(dry run — not launching anything)"; return 0; fi
  if ! command -v claude >/dev/null 2>&1; then
    echo "error: the 'claude' CLI isn't on your PATH." >&2; exit 1
  fi
  local prompt
  read -r -d '' prompt <<PROMPT || true
Reflect on the IdeaFlow project. Use the client "${IFCLI}" (HTTP API at ${BASE}):
run "${IFCLI} list-ideas" and "${IFCLI} feed-items --unsummarized" to see the
current state. There is nothing queued to research right now. Reflect on: what's
been covered, where the ideas are concentrated, what's stale or stuck, and 3-5
concrete new ideas or angles worth adding. Print your reflection — do not modify
anything.
PROMPT
  claude -p "$prompt" --allowedTools "Bash,Read,WebSearch,WebFetch"
}

if [[ "$REFLECT" -eq 1 ]]; then
  run_reflection
  exit 0
fi

# Sort ideas into fresh (no research) and done (has research), via the client.
FRESH=()
DONE=()
while read -r kind id; do
  [[ -z "$kind" ]] && continue
  if [[ "$kind" == "F" ]]; then FRESH+=("$id"); else DONE+=("$id"); fi
done < <(
  IF_STATUS="$STATUS" python3 - "$IFCLI" <<'PY'
import json, os, subprocess, sys
cli = sys.argv[1]
status = os.environ.get("IF_STATUS") or None

def run(*a):
    return json.loads(subprocess.check_output([cli, *a]))

listing = ["list-ideas"] + (["--status", status] if status else [])
for it in run(*listing)["ideas"]:
    detail = run("dump-idea", str(it["id"]))
    print("D" if detail.get("research_entries") else "F", it["id"])
PY
)

# Decide the pass: what to run, and in which mode.
TARGETS=()
MODE="research"
if [[ "$FORCE" -eq 1 ]]; then
  MODE="research"
  [[ ${#FRESH[@]} -gt 0 ]] && TARGETS+=("${FRESH[@]}")
  [[ ${#DONE[@]} -gt 0 ]] && TARGETS+=("${DONE[@]}")
elif [[ "$REVIEW" -eq 1 ]]; then
  MODE="review"
  [[ ${#DONE[@]} -gt 0 ]] && TARGETS+=("${DONE[@]}")
elif [[ ${#FRESH[@]} -gt 0 ]]; then
  MODE="research"
  TARGETS+=("${FRESH[@]}")
elif [[ ${#DONE[@]} -gt 0 ]]; then
  MODE="review"
  echo "Nothing fresh to research — reviewing already-researched ideas."
  TARGETS+=("${DONE[@]}")
else
  run_reflection
  exit 0
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "Nothing to do for this pass."
  exit 0
fi

note=""
[[ -n "$STATUS" ]] && note+=", status=$STATUS"
echo "${MODE} pass over ${#TARGETS[@]} idea(s): ${TARGETS[*]}"
echo "Pacing: ${DELAY}s between runs${note}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry run — not launching anything)"
  exit 0
fi

fail=0
for i in "${!TARGETS[@]}"; do
  id="${TARGETS[$i]}"
  echo
  echo "=== [$((i + 1))/${#TARGETS[@]}] ${MODE} idea ${id} ==="
  if ./research_idea.sh "$id" "$MODE"; then
    echo "=== idea ${id} done ==="
  else
    echo "!!! idea ${id} failed (continuing) !!!" >&2
    fail=$((fail + 1))
  fi
  if [[ $((i + 1)) -lt ${#TARGETS[@]} ]]; then
    echo "--- waiting ${DELAY}s before the next idea ---"
    sleep "$DELAY"
  fi
done

echo
echo "All done: ${MODE} pass, ${#TARGETS[@]} attempted, ${fail} failed."
[[ "$fail" -eq 0 ]]
