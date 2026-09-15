#!/usr/bin/env bash
#
# Run one IdeaFlow research/review/execute/critique task with Antigravity (agy).
# Usage:
#   IDEAFLOW_API_TOKEN=... ./research_idea_agy.sh <idea-id> [research|review|execute|critique|persona|repeat|summary]
#
# Optional: IDEAFLOW_ANTIGRAVITY_MODEL=<Antigravity model available to your CLI>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export IDEAFLOW_AGENT=antigravity

# Fallback path if agy is installed in ~/.gemini/bin but not on PATH
if [[ -z "${IDEAFLOW_AGENT_BIN:-}" && -x "$HOME/.gemini/bin/agy" ]]; then
  export IDEAFLOW_AGENT_BIN="$HOME/.gemini/bin/agy"
fi

exec "$SCRIPT_DIR/research_idea.sh" "$@"
