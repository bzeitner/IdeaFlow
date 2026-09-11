#!/usr/bin/env bash
# Compatibility instrumentation for existing shell-driven LLM workflows.
# Source after IDEAFLOW_API_* variables and IFCLI have been initialized.

IDEAFLOW_TRACE_ID=""
IDEAFLOW_RUN_ID=""
IDEAFLOW_TELEMETRY_ACTIVE=0

execution_start() {
  local workflow="$1" idea_id="$2" provider="$3" model="$4" purpose="$5" input_file="$6"
  shift 6
  if [[ -z "${IDEAFLOW_EXECUTION_API_TOKEN:-}" ]]; then
    echo "error: IDEAFLOW_EXECUTION_API_TOKEN is required for metered LLM execution." >&2
    return 1
  fi
  if [[ "$provider" == "codex" ]] && { [[ ! "${IDEAFLOW_CODEX_COST_MICROS_PER_RUN:-}" =~ ^[0-9]+$ ]] || [[ "${IDEAFLOW_CODEX_COST_MICROS_PER_RUN}" -eq 0 ]]; }; then
    echo "error: IDEAFLOW_CODEX_COST_MICROS_PER_RUN must be a positive integer for Codex cost allocation." >&2
    return 1
  fi
  local nonce trace_json run_json prompt_args=() prompt_key
  local subject_args=()
  nonce="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  for prompt_key in "$@"; do
    prompt_args+=(--prompt-key "$prompt_key")
  done
  if [[ -n "$idea_id" ]]; then
    subject_args+=(--idea "$idea_id")
  fi
  if ! trace_json="$("$IFCLI" trace-start \
      --workflow "$workflow" "${subject_args[@]}" --trigger scheduler \
      --correlation-key "${workflow}:idea:${idea_id}" \
      --idempotency-key "${workflow}:${idea_id}:${nonce}" 2>&1)"; then
    echo "error: execution trace registration failed: ${trace_json}" >&2
    return 1
  fi
  IDEAFLOW_TRACE_ID="$(printf '%s' "$trace_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  export IDEAFLOW_TRACE_ID
  if ! run_json="$("$IFCLI" run-start \
      --trace-id "$IDEAFLOW_TRACE_ID" --provider "$provider" --model "$model" \
      --purpose "$purpose" --input-file "$input_file" \
      --idempotency-key "attempt:${nonce}" "${prompt_args[@]}" 2>&1)"; then
    echo "error: execution run registration failed: ${run_json}" >&2
    "$IFCLI" trace-fail --trace-id "$IDEAFLOW_TRACE_ID" \
      --reason "run registration failed" >/dev/null 2>&1 || true
    IDEAFLOW_TRACE_ID=""
    export IDEAFLOW_TRACE_ID
    return 1
  fi
  IDEAFLOW_RUN_ID="$(printf '%s' "$run_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  IDEAFLOW_TELEMETRY_ACTIVE=1
  export IDEAFLOW_RUN_ID IDEAFLOW_TELEMETRY_ACTIVE
  echo "  execution trace: ${IDEAFLOW_TRACE_ID}; run: ${IDEAFLOW_RUN_ID}" >&2
}

execution_succeed() {
  local output_file="$1" measurement_file="${2:-}"
  [[ "$IDEAFLOW_TELEMETRY_ACTIVE" -eq 1 ]] || return 0
  local measurement_status="partial" reasons=(--measurement-unavailable-reason first-token-unavailable)
  local metric_args=()
  if [[ -n "$measurement_file" && -s "$measurement_file" ]]; then
    local values input_tokens output_tokens cached_tokens reasoning_tokens total_tokens cost_micros request_id cost_source
    values="$(python3 - "$measurement_file" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print("|".join("" if d.get(k) is None else str(d.get(k)) for k in (
    "input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens",
    "total_tokens", "cost_micros", "provider_request_id", "cost_source")))
PY
)"
    IFS='|' read -r input_tokens output_tokens cached_tokens reasoning_tokens total_tokens cost_micros request_id cost_source <<< "$values"
    [[ -n "$input_tokens" ]] && metric_args+=(--input-tokens "$input_tokens")
    [[ -n "$output_tokens" ]] && metric_args+=(--output-tokens "$output_tokens")
    [[ -n "$cached_tokens" ]] && metric_args+=(--cached-tokens "$cached_tokens")
    [[ -n "$reasoning_tokens" ]] && metric_args+=(--reasoning-tokens "$reasoning_tokens")
    [[ -n "$total_tokens" ]] && metric_args+=(--total-tokens "$total_tokens")
    [[ -n "$cost_micros" ]] && metric_args+=(--cost-micros "$cost_micros")
    [[ -n "$request_id" ]] && metric_args+=(--provider-request-id "$request_id")
    [[ -n "$cost_source" ]] && metric_args+=(--cost-source "$cost_source")
    [[ -z "$total_tokens" ]] && reasons+=(--measurement-unavailable-reason provider-usage-unavailable)
    [[ -z "$cost_micros" ]] && reasons+=(--measurement-unavailable-reason cost-unavailable)
    [[ -n "$total_tokens" && -n "$cost_micros" ]] && measurement_status="complete" && reasons=()
  else
    reasons+=(--measurement-unavailable-reason provider-usage-unavailable --measurement-unavailable-reason cost-unavailable)
  fi
  # Bash 3.2 treats expansion of an empty array as an unbound variable under
  # `set -u`. Complete measurements intentionally clear `reasons`, so keep the
  # empty array out of that command path entirely.
  if [[ "$measurement_status" == "complete" ]]; then
    if "$IFCLI" run-complete --run-id "$IDEAFLOW_RUN_ID" \
        --output-file "$output_file" --finish-reason stop --measurement-status "$measurement_status" \
        "${metric_args[@]}" >/dev/null; then
      completion_status=0
    else
      completion_status="$?"
    fi
  else
    if "$IFCLI" run-complete --run-id "$IDEAFLOW_RUN_ID" \
        --output-file "$output_file" --finish-reason stop --measurement-status "$measurement_status" \
        "${metric_args[@]}" "${reasons[@]}" >/dev/null; then
      completion_status=0
    else
      completion_status="$?"
    fi
  fi
  if [[ "$completion_status" -ne 0 ]]; then
    echo "error: execution completion reporting failed; run ${IDEAFLOW_RUN_ID} requires reconciliation" >&2
    return 1
  fi
  if ! "$IFCLI" trace-complete --trace-id "$IDEAFLOW_TRACE_ID" >/dev/null; then
    echo "error: trace completion reporting failed for ${IDEAFLOW_TRACE_ID}" >&2
    return 1
  fi
}

execution_fail() {
  local exit_code="$1" detail="${2:-provider process failed}"
  [[ "$IDEAFLOW_TELEMETRY_ACTIVE" -eq 1 ]] || return 0
  "$IFCLI" run-fail --run-id "$IDEAFLOW_RUN_ID" \
    --error-class ProviderProcessError --error-code "exit-${exit_code}" \
    --error-detail "$detail" \
    --measurement-unavailable-reason provider-process-failed >/dev/null 2>&1 || true
  "$IFCLI" trace-fail --trace-id "$IDEAFLOW_TRACE_ID" \
    --reason "$detail" >/dev/null 2>&1 || true
}
