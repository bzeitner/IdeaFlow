#!/usr/bin/env bash
#
# Launch a headless Claude Code agent to research OR review one IdeaFlow idea,
# reporting back to the DEPLOYED app over its HTTP API.
#
#   IDEAFLOW_API_TOKEN=... ./research_idea.sh <idea-id> [research|review]
#
# Modes:
#   research (default) — research a (usually not-yet-researched) idea from scratch.
#   review             — read the idea's existing research and synthesize progress,
#                        fill gaps, and update its stage/status. Used by
#                        research_all.sh when there's nothing fresh to research.
#
# Everything goes through tools/ideaflow, so it works from any machine. The run
# isn't done until log-effort succeeds.
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token

set -euo pipefail

ID="${1:-}"
MODE="${2:-research}"
if [[ -z "$ID" || ! "$ID" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 <idea-id> [research|review]" >&2
  exit 2
fi
case "$MODE" in
  research|review) ;;
  *) echo "error: mode must be 'research' or 'review', got '$MODE'." >&2; exit 2 ;;
esac

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

REPORT="$(mktemp -t "idea-${ID}-${MODE}.XXXXXX.md")"

if [[ "$MODE" == "review" ]]; then
  read -r -d '' PROMPT <<PROMPT || true
Review IdeaFlow idea ${ID}. Talk to IdeaFlow only through the client "${IFCLI}"
(HTTP API at ${BASE}); do not touch any local database. This idea has already
been researched — your job is to review and move it forward, not start over.
Steps:

1. Read the idea, its existing research, its linked "feeds", and its
   "recent_articles" (summarized feed items): ${IFCLI} dump-idea ${ID}
2. Synthesize the existing research_entries AND anything new in recent_articles:
   what's been learned, what's validated vs still open, and the most valuable
   concrete next step. Do fresh web research only to fill specific gaps you
   identify — don't repeat prior work.
3. Register any new RSS/Atom feeds you find, rating each one's relevance to this
   idea 1-5 (the idea keeps only its top-rated feeds):
     ${IFCLI} add-feed --url <url> --idea ${ID} --rating <1-5>
4. Write a concise review + synthesis (markdown) to: ${REPORT}
5. Log it — you are NOT done until this succeeds. Always set --next-action to the
   single most valuable next step for this idea:
     ${IFCLI} log-effort ${ID} \\
       --topic 'Review & synthesis' \\
       --model claude-opus-4-8 \\
       --context-file ${REPORT} \\
       --effort <1-5> --quality <1-5> --tokens <approx> \\
       --next-action '<the single most valuable next step>'
   Update the idea's stage/status if the review warrants it: advance a promising
   one (--stage <slug>), or --status archived for a dead end, --status tracking
   to keep watching. Only change what your review actually justifies.
6. Print the new ResearchEntry id and a two-line summary of your assessment.
PROMPT
else
  read -r -d '' PROMPT <<PROMPT || true
Research IdeaFlow idea ${ID}. Talk to IdeaFlow only through the client "${IFCLI}"
(HTTP API at ${BASE}); do not touch any local database. Steps:

1. Read the idea as JSON: ${IFCLI} dump-idea ${ID}
   Work from its real title, summary, notes, resources, and any existing
   research_entries — do not guess what the idea is.
2. Research it thoroughly. Use web search/fetch for market, competitors,
   feasibility, and concrete next steps as appropriate to the idea.
3. Register any RSS/Atom feeds you come across (blogs, news, release feeds) so
   they're tracked centrally and summarized once — don't fetch/summarize them
   inline. Register each distinct feed and rate its relevance to this idea 1-5
   (the idea keeps only its top-rated feeds):
     ${IFCLI} add-feed --url <feed-url> --idea ${ID} --rating <1-5>
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
fi

echo "→ ${MODE} idea ${ID} against ${BASE}; report scratch file: ${REPORT}" >&2

claude -p "$PROMPT" \
  --allowedTools "Bash,Read,Write,WebSearch,WebFetch"
