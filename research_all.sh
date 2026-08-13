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
# shellcheck source=tools/prompt_standards.sh
source "$SCRIPT_DIR/tools/prompt_standards.sh"
SHARED_STANDARDS="$(prompt_shared_standards)"

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

STATE_FILE="$(mktemp -t ideaflow-selection.XXXXXX.json)"
printf '{}\n' > "$STATE_FILE"
trap 'rm -f "$STATE_FILE"' EXIT

run_reflection() {
  local reason="${1:-explicit}"
  echo "No actionable ideas (${reason}) — reflecting on the project."
  if ! command -v "$AGENT_BIN" >/dev/null 2>&1; then
    if [[ "$DRY_RUN" -eq 1 ]]; then :; else
    echo "error: the '$AGENT' CLI isn't on your PATH (set IDEAFLOW_AGENT_BIN to its absolute path)." >&2; exit 1
    fi
  fi
  local prompt
  read -r -d '' prompt <<PROMPT || true
Conduct a read-only portfolio reflection for IdeaFlow. The batch selector found
no actionable work because: ${reason}. Its machine-readable counts and skipped
idea ids are in ${STATE_FILE}.

Use only reads through "${IFCLI}" (HTTP API at ${BASE}). Start with list-ideas.
Read "${IFCLI} graph" to identify clusters, isolated ideas, and dependency
bottlenecks, then verify every claimed pattern against selected idea details.
Use the selector state to distinguish paused, archived, and researched-but-idle
ideas. Choose 3-8 candidates whose details are needed to support a conclusion,
then dump-idea each of them. Inspect the unsummarized feed backlog only if it
helps explain portfolio health. Do not claim an idea is stale, blocked,
duplicative, or exhausted from its list row alone.

Analyze in this order:
1. Portfolio coverage and concentration by category, stage, and status.
2. Stalled work: identify the exact missing decision, feedback, trigger, or next
   action, citing idea ids and evidence from their details.
3. Consolidation: overlapping ideas that may belong together; verify overlap
   before recommending it.
4. Child directions that fit an existing parent better than becoming new roots.
5. Genuine portfolio gaps. Propose new ideas only after checking existing titles,
   summaries, children, and suggestions for overlap. Use web research only to
   validate a specific proposed gap, not to brainstorm broadly.

Output these sections: State summary; Portfolio gaps; Stalled ideas;
Consolidation opportunities; Suggested child ideas; New root ideas. For every
recommendation include evidence, the affected idea ids, and a concrete human
decision or action. If a section has no supported finding, say "None." Do not
modify IdeaFlow, create ideas, add suggestions, register feeds, or log efforts.

${SHARED_STANDARDS}
PROMPT
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '%s\n' "$prompt"
    return 0
  fi
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
  run_reflection "explicit request"
  exit 0
fi

# Build the work list as "<id> <mode>" pairs (selection logic in select_tasks.py,
# a standalone file — bash 3.2 mis-parses a heredoc nested in <(...)).
IDS=()
MODES=()
TITLES=()
PAIRS="$(
  IF_STATUS="$STATUS" IF_FORCE="$FORCE" IF_REVIEW="$REVIEW" IF_MIN="$MIN" \
    IF_STATE_FILE="$STATE_FILE" \
    python3 "$SCRIPT_DIR/tools/select_tasks.py" "$IFCLI"
)"
while read -r id mode title; do
  [[ -z "$id" ]] && continue
  IDS+=("$id"); MODES+=("$mode"); TITLES+=("$title")
done <<< "$PAIRS"

if [[ ${#IDS[@]} -eq 0 ]]; then
  reason="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("reason", "unknown"))' "$STATE_FILE")"
  summary="$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print("listed={}, paused={}, archived={}, idle={}".format(s.get("listed", 0), len(s.get("paused_ids", [])), len(s.get("archived_ids", [])), len(s.get("idle_ids", []))))' "$STATE_FILE")"
  if [[ "$reason" == "idle" && "$FORCE" -eq 0 && "$REVIEW" -eq 0 ]]; then
    echo "Nothing actionable: ${summary}."
    run_reflection "$reason"
    exit 0
  fi
  if [[ "$reason" == "no_ideas" ]]; then
    echo "Nothing to do: no ideas match this pass${STATUS:+ (status=$STATUS)}."
  else
    echo "Nothing actionable (${reason}): ${summary}."
  fi
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
