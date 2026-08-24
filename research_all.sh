#!/usr/bin/env bash
#
# Keep an agent doing useful work across all IdeaFlow ideas, against the
# DEPLOYED app.
#
# Default pass selects, in priority order:
#   * every idea with no research yet          -> research mode
#   * repo-backed ideas with a build action     -> execute mode (implement it)
#   * every other idea with a next action set   -> review mode (advance it)
#   * researched ideas with newer human activity -> review mode (reconsider it)
#   * researched ideas without a next action are skipped
#
#   IDEAFLOW_API_TOKEN=... ./research_all.sh [options]
#
# Options:
#   --status current|tracking|archived   limit to one tab
#   --min N                               retained for compatibility (default 5)
#   --review                              review every already-researched idea
#   --force                               research EVERY idea (ignore existing work)
#   --reflect                             just run the project-level reflection
#   --dry-run                             show the plan, launch nothing
#   --delay N                             wait N seconds between ideas (default 0)
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
AGENT="${IDEAFLOW_AGENT:-claude}"
AGENT_BIN="${IDEAFLOW_AGENT_BIN:-$AGENT}"
# shellcheck source=tools/prompt_standards.sh
source "$SCRIPT_DIR/tools/prompt_standards.sh"
prompt_load_ideaflow_env "$SCRIPT_DIR"
BASE="${IDEAFLOW_API_BASE:-https://ideaflow.bitesoftheweek.com}"
SHARED_STANDARDS="$(prompt_shared_standards)"
if [[ -n "${IDEAFLOW_API_TOKEN:-}" ]]; then
  managed_shared="$("$IFCLI" prompt shared-standards 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["content"])' 2>/dev/null || true)"
  [[ -n "$managed_shared" ]] && SHARED_STANDARDS="$managed_shared"
fi

STATUS=""
MIN=5
FORCE=0
REVIEW=0
REFLECT=0
DRY_RUN=0
DELAY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status) STATUS="${2:-}"; shift 2 ;;
    --min)    MIN="${2:-}";    shift 2 ;;
    --force)  FORCE=1; shift ;;
    --review) REVIEW=1; shift ;;
    --reflect) REFLECT=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --delay) DELAY="${2:-}"; shift 2 ;;
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
[[ "$MIN" =~ ^[0-9]+$ ]] || { echo "error: --min must be a whole number." >&2; exit 2; }
[[ "$DELAY" =~ ^[0-9]+$ ]] || { echo "error: --delay must be a whole number of seconds." >&2; exit 2; }

STATE_FILE="$(mktemp -t ideaflow-selection.json.XXXXXX)"
RUN_METRICS_FILE="$(mktemp -t ideaflow-run-metrics.tsv.XXXXXX)"
printf '{}\n' > "$STATE_FILE"
RUN_STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
RUN_START_EPOCH="$(date '+%s')"
if [[ "$AGENT" == "codex" ]]; then
  REQUESTED_MODEL="${IDEAFLOW_CODEX_MODEL:-codex-default}"
else
  REQUESTED_MODEL="task-routed"
fi
echo "Batch started: ${RUN_STARTED_AT} (agent=${AGENT}, model=${REQUESTED_MODEL})"

finish_run() {
  local status=$? end_epoch elapsed metrics total_tokens models
  end_epoch="$(date '+%s')"
  elapsed=$((end_epoch - RUN_START_EPOCH))
  metrics="$(python3 - "$RUN_METRICS_FILE" <<'PY'
import sys

total = 0
models = []
try:
    lines = open(sys.argv[1], encoding="utf-8")
except OSError:
    lines = []
for line in lines:
    model, _, tokens = line.rstrip("\n").partition("\t")
    if model and model not in models:
        models.append(model)
    try:
        total += int(tokens or 0)
    except ValueError:
        pass
print(f"{total}\t{', '.join(models)}")
PY
)"
  IFS=$'\t' read -r total_tokens models <<< "$metrics"
  [[ -n "$models" ]] || models="$REQUESTED_MODEL"
  echo "Batch finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ') (elapsed=${elapsed}s, recorded_tokens=${total_tokens:-0}, models=${models}, exit=${status})"
  rm -f "$STATE_FILE" "$RUN_METRICS_FILE"
  return "$status"
}
trap finish_run EXIT

record_task_metrics() {
  local before_file="$1" after_file="$2"
  python3 - "$before_file" "$after_file" >> "$RUN_METRICS_FILE" <<'PY'
import json
import sys

try:
    before = json.load(open(sys.argv[1], encoding="utf-8"))
    after = json.load(open(sys.argv[2], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
old_ids = {entry.get("id") for entry in before.get("research_entries", [])}
for entry in after.get("research_entries", []):
    if entry.get("id") in old_ids:
        continue
    routed = entry.get("model") or {}
    model = entry.get("execution_model") or routed.get("slug") or routed.get("name") or "unknown"
    print(f"{model}\t{entry.get('tokens_used') or 0}")
PY
}

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
  if [[ -n "${IDEAFLOW_API_TOKEN:-}" ]]; then
    managed_reflection="$("$IFCLI" prompt agent-portfolio-reflection 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["content"])' 2>/dev/null || true)"
    if [[ -n "$managed_reflection" ]]; then
      prompt="$(printf '%s' "$managed_reflection" | reason="$reason" STATE_FILE="$STATE_FILE" IFCLI="$IFCLI" BASE="$BASE" SHARED_STANDARDS="$SHARED_STANDARDS" python3 -c 'import os,sys; from string import Template; print(Template(sys.stdin.read()).safe_substitute(os.environ))')"
    fi
  fi
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
[[ -n "$note" ]] && echo "Selection:${note#,}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry run — not launching anything)"
  exit 0
fi

fail=0
for i in "${!IDS[@]}"; do
  id="${IDS[$i]}"; mode="${MODES[$i]}"; title="${TITLES[$i]}"
  before_file="$(mktemp -t ideaflow-before.json.XXXXXX)"
  after_file="$(mktemp -t ideaflow-after.json.XXXXXX)"
  "$IFCLI" dump-idea "$id" > "$before_file" 2>/dev/null || printf '{}\n' > "$before_file"
  echo
  echo "=== [$((i + 1))/${#IDS[@]}] ${mode}: ${title} (#${id}) ==="
  if ./research_idea.sh "$id" "$mode"; then
    echo "=== ${title} (#${id}) done ==="
  else
    echo "!!! ${title} (#${id}) failed (continuing) !!!" >&2
    fail=$((fail + 1))
  fi
  "$IFCLI" dump-idea "$id" > "$after_file" 2>/dev/null || printf '{}\n' > "$after_file"
  record_task_metrics "$before_file" "$after_file"
  rm -f "$before_file" "$after_file"
  if [[ "$DELAY" -gt 0 && "$i" -lt $((${#IDS[@]} - 1)) ]]; then
    echo "Waiting ${DELAY}s before the next idea..."
    sleep "$DELAY"
  fi
done

echo
echo "All done: ${#IDS[@]} attempted, ${fail} failed."
[[ "$fail" -eq 0 ]]
