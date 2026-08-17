#!/usr/bin/env bash
#
# Run the IdeaFlow batch research/review loop with Codex.
# Usage:
#   IDEAFLOW_API_TOKEN=... ./research_all_codex.sh [research_all.sh options]
#
# Optional: IDEAFLOW_CODEX_MODEL=<Codex model available to your CLI>
# Start/end timing, recorded token totals, model reporting, and --delay are
# provided by research_all.sh so both entry points produce identical logs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export IDEAFLOW_AGENT=codex

# ChatGPT Desktop bundles the Codex CLI here. This fallback supports shells
# whose PATH omits the app's Resources directory.
if [[ -z "${IDEAFLOW_AGENT_BIN:-}" && -x /Applications/ChatGPT.app/Contents/Resources/codex ]]; then
  export IDEAFLOW_AGENT_BIN=/Applications/ChatGPT.app/Contents/Resources/codex
fi

exec "$SCRIPT_DIR/research_all.sh" "$@"
