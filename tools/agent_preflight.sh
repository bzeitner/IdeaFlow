#!/usr/bin/env bash

# Fail before creating remote execution records when the selected agent cannot
# accept work. Callers may export IDEAFLOW_AGENT_PREFLIGHTED=1 after success so
# child runners do not repeat the same check.
agent_require_ready() {
  local agent="$1" agent_bin="$2" auth_output="" logged_in=""

  if ! command -v "$agent_bin" >/dev/null 2>&1; then
    if [[ "$agent" =~ ^(antigravity|agy)$ ]]; then
      echo "error: the Antigravity CLI ('agy') isn't on your PATH (install it or set IDEAFLOW_AGENT_BIN to its absolute path)." >&2
    else
      echo "error: the '$agent' CLI isn't on your PATH (set IDEAFLOW_AGENT_BIN to its absolute path)." >&2
    fi
    return 1
  fi

  [[ "$agent" == "claude" ]] || return 0

  # Claude emits useful JSON even when the status command exits non-zero.
  auth_output="$("$agent_bin" auth status 2>&1 || true)"
  logged_in="$(
    printf '%s' "$auth_output" | python3 -c '
import json
import sys

try:
    status = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(0)
print("true" if status.get("loggedIn") is True else "false")
' 2>/dev/null || true
  )"

  if [[ "$logged_in" == "true" ]]; then
    return 0
  fi

  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    echo "error: Claude Code is not logged in for CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR}." >&2
    printf 'Log in, then retry: env CLAUDE_CONFIG_DIR=%q %q auth login\n' \
      "$CLAUDE_CONFIG_DIR" "$agent_bin" >&2
  else
    echo "error: Claude Code is not logged in." >&2
    printf 'Log in, then retry: %q auth login\n' "$agent_bin" >&2
  fi
  return 1
}
