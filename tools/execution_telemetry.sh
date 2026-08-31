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
    return 0
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
    echo "warning: execution trace unavailable; continuing uninstrumented: ${trace_json}" >&2
    return 0
  fi
  IDEAFLOW_TRACE_ID="$(printf '%s' "$trace_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  export IDEAFLOW_TRACE_ID
  if ! run_json="$("$IFCLI" run-start \
      --trace-id "$IDEAFLOW_TRACE_ID" --provider "$provider" --model "$model" \
      --purpose "$purpose" --input-file "$input_file" \
      --idempotency-key "attempt:${nonce}" "${prompt_args[@]}" 2>&1)"; then
    echo "warning: execution run unavailable; continuing uninstrumented: ${run_json}" >&2
    "$IFCLI" trace-fail --trace-id "$IDEAFLOW_TRACE_ID" \
      --reason "run registration failed" >/dev/null 2>&1 || true
    IDEAFLOW_TRACE_ID=""
    export IDEAFLOW_TRACE_ID
    return 0
  fi
  IDEAFLOW_RUN_ID="$(printf '%s' "$run_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  IDEAFLOW_TELEMETRY_ACTIVE=1
  export IDEAFLOW_RUN_ID IDEAFLOW_TELEMETRY_ACTIVE
  echo "  execution trace: ${IDEAFLOW_TRACE_ID}; run: ${IDEAFLOW_RUN_ID}" >&2
}

execution_succeed() {
  local output_file="$1"
  [[ "$IDEAFLOW_TELEMETRY_ACTIVE" -eq 1 ]] || return 0
  if ! "$IFCLI" run-complete --run-id "$IDEAFLOW_RUN_ID" \
      --output-file "$output_file" --finish-reason stop --measurement-status partial \
      --measurement-unavailable-reason provider-usage-unavailable \
      --measurement-unavailable-reason provider-request-id-unavailable \
      --measurement-unavailable-reason first-token-unavailable \
      --measurement-unavailable-reason cost-unavailable >/dev/null; then
    echo "warning: execution completion reporting failed; run ${IDEAFLOW_RUN_ID} remains recoverable" >&2
    return 0
  fi
  if ! "$IFCLI" trace-complete --trace-id "$IDEAFLOW_TRACE_ID" >/dev/null; then
    echo "warning: trace completion reporting failed for ${IDEAFLOW_TRACE_ID}" >&2
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
