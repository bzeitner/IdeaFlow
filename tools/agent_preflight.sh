#!/usr/bin/env bash

# Fail before creating remote execution records when the selected agent cannot
# accept work. A successful check is represented by agent_preflight_identity so
# child runners skip only an identical agent, binary, profile, and auth mode.
agent_flag_enabled() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

agent_auth_mode() {
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    printf '%s' "anthropic-api-key"
  elif [[ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
    printf '%s' "anthropic-auth-token"
  elif agent_flag_enabled "${CLAUDE_CODE_USE_BEDROCK:-}"; then
    printf '%s' "bedrock"
  elif agent_flag_enabled "${CLAUDE_CODE_USE_VERTEX:-}"; then
    printf '%s' "vertex"
  elif agent_flag_enabled "${CLAUDE_CODE_USE_FOUNDRY:-}"; then
    printf '%s' "foundry"
  elif agent_flag_enabled "${CLAUDE_CODE_USE_MANTLE:-}"; then
    printf '%s' "mantle"
  elif agent_flag_enabled "${CLAUDE_CODE_USE_ANTHROPIC_AWS:-}"; then
    printf '%s' "anthropic-aws"
  else
    printf '%s' "login:${CLAUDE_CONFIG_DIR:-default}"
  fi
}

agent_preflight_identity() {
  local agent="$1" agent_bin="$2" resolved_bin
  resolved_bin="$(command -v "$agent_bin" 2>/dev/null || printf '%s' '<missing>')"
  printf '%s|%s|%s' "$agent" "$resolved_bin" "$(agent_auth_mode)"
}

agent_require_ready() {
  local agent="$1" agent_bin="$2" auth_mode="" auth_state=""

  if ! command -v "$agent_bin" >/dev/null 2>&1; then
    if [[ "$agent" =~ ^(antigravity|agy)$ ]]; then
      echo "error: the Antigravity CLI ('agy') isn't on your PATH (install via 'curl -fsSL https://antigravity.google/cli/install.sh | bash' or set IDEAFLOW_AGENT_BIN to its absolute path)." >&2
    else
      echo "error: the '$agent' CLI isn't on your PATH (set IDEAFLOW_AGENT_BIN to its absolute path)." >&2
    fi
    return 1
  fi

  [[ "$agent" == "claude" ]] || return 0

  # API keys and third-party providers use credentials outside Claude's login
  # store, so loggedIn does not determine whether those configurations can run.
  auth_mode="$(agent_auth_mode)"
  [[ "$auth_mode" == login:* ]] || return 0

  auth_state="$(python3 - "$agent_bin" "${IDEAFLOW_AGENT_PREFLIGHT_TIMEOUT_SECONDS:-5}" <<'PY'
import json
import subprocess
import sys

try:
    timeout = float(sys.argv[2])
    if timeout <= 0:
        raise ValueError
except ValueError:
    timeout = 5.0

try:
    completed = subprocess.run(
        [sys.argv[1], "auth", "status"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
except subprocess.TimeoutExpired:
    print("timeout")
    raise SystemExit
except OSError:
    print("unknown")
    raise SystemExit

text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
decoder = json.JSONDecoder()
for offset, character in enumerate(text):
    if character != "{":
        continue
    try:
        status, _end = decoder.raw_decode(text[offset:])
    except ValueError:
        continue
    if isinstance(status, dict) and isinstance(status.get("loggedIn"), bool):
        print("true" if status["loggedIn"] else "false")
        raise SystemExit
print("unknown")
PY
)"

  if [[ "$auth_state" == "true" ]]; then
    return 0
  fi

  if [[ "$auth_state" == "timeout" ]]; then
    echo "warning: timed out checking Claude Code authentication; continuing and relying on provider error handling." >&2
    return 0
  fi
  if [[ "$auth_state" != "false" ]]; then
    echo "warning: Claude Code authentication status was not recognized; continuing and relying on provider error handling." >&2
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
