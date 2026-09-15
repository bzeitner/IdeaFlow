#!/usr/bin/env bash
#
# Run the IdeaFlow batch research/review loop with Antigravity (agy).
# Usage:
#   IDEAFLOW_API_TOKEN=... ./research_all_agy.sh [research_all.sh options]
#
# Optional: IDEAFLOW_ANTIGRAVITY_MODEL=<Antigravity model available to your CLI>
# Start/end timing, recorded token totals, model reporting, and --delay are
# provided by research_all.sh so entry points produce identical logs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export IDEAFLOW_AGENT=antigravity

# Fallback path if agy is installed in ~/.gemini/bin but not on PATH
if [[ -z "${IDEAFLOW_AGENT_BIN:-}" && -x "$HOME/.gemini/bin/agy" ]]; then
  export IDEAFLOW_AGENT_BIN="$HOME/.gemini/bin/agy"
fi

exec "$SCRIPT_DIR/research_all.sh" "$@"
