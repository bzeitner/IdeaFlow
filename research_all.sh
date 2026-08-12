#!/usr/bin/env bash
#
# Keep an agent doing useful work across all IdeaFlow ideas, against the
# DEPLOYED app.
#
# Default pass selects, in priority order:
#   * every idea with no research yet          -> research mode
#   * every idea that has a next action set     -> review mode (advance it)
#   * researched ideas without a next action are skipped
#
#   IDEAFLOW_API_TOKEN=... ./research_all.sh [options]
#
# Options:
#   --status current|tracking|archived   limit to one tab
#   --min N                               retained for compatibility (default 5)
#   --delay SECONDS                       cooldown between runs (default 90)
#   --review                              review every already-researched idea
#   --force                               research EVERY idea (ignore existing work)
#   --reflect                             just run the project-level reflection
#   --dry-run                             show the plan, launch nothing
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token
#   IDEAFLOW_AGENT      claude (default) or codex
#   IDEAFLOW_AGENT_BIN  optional CLI name/path override
#   IDEAFLOW_CODEX_MODEL optional model passed to `codex exec --model`

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
IFCLI="$SCRIPT_DIR/tools/ideaflow"
BASE="${IDEAFLOW_API_BASE:-https://ideaflow.bitesoftheweek.com}"
AGENT="${IDEAFLOW_AGENT:-claude}"
AGENT_BIN="${IDEAFLOW_AGENT_BIN:-$AGENT}"

STATUS=""
MIN=5
DELAY=90
FORCE=0
REVIEW=0
REFLECT=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status) STATUS="${2:-}"; shift 2 ;;
    --min)    MIN="${2:-}";    shift 2 ;;
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
case "$AGENT" in
  claude|codex) ;;
  *) echo "error: IDEAFLOW_AGENT must be claude or codex, got '$AGENT'." >&2; exit 2 ;;
esac
for n in "$MIN" "$DELAY"; do
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "error: --min and --delay must be whole numbers." >&2; exit 2; }
done

run_reflection() {
  echo "No ideas to work on — reflecting on the project."
  if [[ "$DRY_RUN" -eq 1 ]]; then echo "(dry run — not launching anything)"; return 0; fi
  if ! command -v "$AGENT_BIN" >/dev/null 2>&1; then
    echo "error: the '$AGENT' CLI isn't on your PATH (set IDEAFLOW_AGENT_BIN to its absolute path)." >&2; exit 1
  fi
  local prompt
  read -r -d '' prompt <<PROMPT || true
Reflect on the IdeaFlow project. Use the client "${IFCLI}" (HTTP API at ${BASE}):
run "${IFCLI} list-ideas" and "${IFCLI} feed-items --unsummarized" to see the
current state. There is nothing queued to work on right now. Reflect on: what's
been covered, where the ideas are concentrated, what's stale or stuck, and 3-5
concrete new ideas or angles worth adding. Print your reflection — do not modify
anything.
PROMPT
  if [[ "$AGENT" == "claude" ]]; then
    "$AGENT_BIN" -p "$prompt" --allowedTools "Bash,Read,WebSearch,WebFetch"
  else
    local codex_args=(
      --search
      -C "$SCRIPT_DIR"
      --sandbox danger-full-access
      --ask-for-approval never
    )
    [[ -n "${IDEAFLOW_CODEX_MODEL:-}" ]] && codex_args+=(--model "$IDEAFLOW_CODEX_MODEL")
    codex_args+=(exec --ephemeral)
    "$AGENT_BIN" "${codex_args[@]}" "$prompt"
  fi
}

if [[ "$REFLECT" -eq 1 ]]; then
  run_reflection
  exit 0
fi

# Build the work list as "<id> <mode>" pairs (selection logic in select_tasks.py,
# a standalone file — bash 3.2 mis-parses a heredoc nested in <(...)).
IDS=()
MODES=()
TITLES=()
PAIRS="$(
  IF_STATUS="$STATUS" IF_FORCE="$FORCE" IF_REVIEW="$REVIEW" IF_MIN="$MIN" \
    python3 "$SCRIPT_DIR/tools/select_tasks.py" "$IFCLI"
)"
while read -r id mode title; do
  [[ -z "$id" ]] && continue
  IDS+=("$id"); MODES+=("$mode"); TITLES+=("$title")
done <<< "$PAIRS"

if [[ ${#IDS[@]} -eq 0 ]]; then
  if [[ "$FORCE" -eq 0 && "$REVIEW" -eq 0 ]]; then
    run_reflection
    exit 0
  fi
  echo "Nothing to do for this pass."
  exit 0
fi

# Summarize the plan, e.g. "3 (research), 12 (review)".
plan=""
for i in "${!IDS[@]}"; do plan+="${IDS[$i]} (${MODES[$i]}), "; done
note=""
[[ -n "$STATUS" ]] && note+=", status=$STATUS"
echo "Work list (${#IDS[@]} actionable): ${plan%, }"
echo "Agent: ${AGENT}"
echo "Pacing: ${DELAY}s between runs${note}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry run — not launching anything)"
  exit 0
fi

fail=0
for i in "${!IDS[@]}"; do
  id="${IDS[$i]}"; mode="${MODES[$i]}"; title="${TITLES[$i]}"
  echo
  echo "=== [$((i + 1))/${#IDS[@]}] ${mode}: ${title} (#${id}) ==="
  if ./research_idea.sh "$id" "$mode"; then
    echo "=== ${title} (#${id}) done ==="
  else
    echo "!!! ${title} (#${id}) failed (continuing) !!!" >&2
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
