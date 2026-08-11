#!/usr/bin/env bash
#
# Run one IdeaFlow research/review/execute/critique task with Codex.
# Usage:
#   IDEAFLOW_API_TOKEN=... ./research_idea_codex.sh <idea-id> [research|review|execute|critique]
#
# Optional: IDEAFLOW_CODEX_MODEL=<Codex model available to your CLI>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export IDEAFLOW_AGENT=codex

# ChatGPT Desktop bundles the Codex CLI here. This absolute-path fallback makes
# the launcher work from shells whose PATH omits the app's Resources directory.
if [[ -z "${IDEAFLOW_AGENT_BIN:-}" && -x /Applications/ChatGPT.app/Contents/Resources/codex ]]; then
  export IDEAFLOW_AGENT_BIN=/Applications/ChatGPT.app/Contents/Resources/codex
fi

exec "$SCRIPT_DIR/research_idea.sh" "$@"
