#!/usr/bin/env bash
#
# Launch a headless Claude Code agent to research one IdeaFlow idea and report
# the effort back into the DEPLOYED app over its HTTP API.
#
#   IDEAFLOW_API_TOKEN=... ./research_idea.sh <idea-id>
#
# The agent reads the idea, researches it, registers any feeds it finds, writes
# a report, and logs a ResearchEntry — all through tools/ideaflow, so it works
# from any machine without a database checkout. It isn't done until log-effort
# succeeds.
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token

set -euo pipefail

ID="${1:-}"
if [[ -z "$ID" || ! "$ID" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 <idea-id>   (a numeric idea id, e.g. ./research_idea.sh 3)" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IFCLI="$SCRIPT_DIR/tools/ideaflow"
BASE="${IDEAFLOW_API_BASE:-https://ideaflow.bitesoftheweek.com}"

if ! command -v claude >/dev/null 2>&1; then
  echo "error: the 'claude' CLI isn't on your PATH." >&2
  exit 1
fi
if [[ -z "${IDEAFLOW_API_TOKEN:-}" ]]; then
  echo "error: set IDEAFLOW_API_TOKEN (the IdeaFlow API bearer token)." >&2
  exit 1
fi

REPORT="$(mktemp -t "idea-${ID}-report.XXXXXX.md")"

read -r -d '' PROMPT <<PROMPT || true
Research IdeaFlow idea ${ID}. Talk to IdeaFlow only through the client
"${IFCLI}" (it uses the HTTP API at ${BASE}); do not touch any local database.
Steps:

1. Read the idea as JSON: ${IFCLI} dump-idea ${ID}
   Work from its real title, summary, notes, resources, and any existing
   research_entries — do not guess what the idea is.
2. Research it thoroughly. Use web search/fetch for market, competitors,
   feasibility, and concrete next steps as appropriate to the idea.
3. Register any RSS/Atom feeds you come across (blogs, news, release feeds) so
   they're tracked centrally and summarized once — don't fetch/summarize them
   inline, just register each distinct feed (idempotent by URL):
     ${IFCLI} add-feed --url <feed-url> --idea ${ID}
4. Write a clear findings report (markdown) to: ${REPORT}
5. Log the effort back into IdeaFlow — you are NOT done until this succeeds:
     ${IFCLI} log-effort ${ID} \\
       --topic '<short title of what you did>' \\
       --model claude-opus-4-8 \\
       --context-file ${REPORT} \\
       --effort <1-5, how much work> \\
       --quality <1-5, your confidence in the findings> \\
       --tokens <approx tokens used> \\
       --status tracking
   If the idea has a natural next stage, add --stage <slug> too.
6. Print the new ResearchEntry id, how many feeds you registered, and a
   two-line summary.
PROMPT

echo "→ Researching idea ${ID} against ${BASE}; report scratch file: ${REPORT}" >&2

claude -p "$PROMPT" \
  --allowedTools "Bash,Read,Write,WebSearch,WebFetch"
