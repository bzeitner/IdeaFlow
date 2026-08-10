#!/usr/bin/env bash
#
# Launch a headless Claude Code agent to research OR review one IdeaFlow idea,
# reporting back to the DEPLOYED app over its HTTP API.
#
#   IDEAFLOW_API_TOKEN=... ./research_idea.sh <idea-id> [research|review|execute|critique]
#
# Modes:
#   research (default) — research a (usually not-yet-researched) idea from scratch.
#   review             — read existing research, synthesize progress, fill gaps,
#                        update stage/status and the executive summary.
#   execute            — for an idea with a target repo: branch, make the change,
#                        open a PR, and schedule a critical review as the next task.
#   critique           — a deliberately critical persona reviews the idea's open PR.
#
# The model for each mode comes from /api/config (task->model routing), so
# cheap work uses a lighter model. Everything goes through tools/ideaflow.
# execute/critique run on your machine and use your gh auth (write access).
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token

set -euo pipefail

ID="${1:-}"
MODE="${2:-research}"
if [[ -z "$ID" || ! "$ID" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 <idea-id> [research|review|execute|critique]" >&2
  exit 2
fi
case "$MODE" in
  research|review|execute|critique) ;;
  *) echo "error: mode must be research|review|execute|critique, got '$MODE'." >&2; exit 2 ;;
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

# Idea title (for readable logs; empty if it can't be fetched).
TITLE="$(
  "$IFCLI" dump-idea "$ID" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('title',''))" 2>/dev/null \
    || true
)"

# Route to the right model tier for this task (falls back to Opus).
MODEL="$(
  "$IFCLI" config 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['task_models'].get('${MODE}','claude-opus-4-8'))" \
    2>/dev/null || echo claude-opus-4-8
)"
[[ -z "$MODEL" ]] && MODEL="claude-opus-4-8"

if [[ "$MODE" == "execute" ]]; then
  read -r -d '' PROMPT <<PROMPT || true
Execute on IdeaFlow idea ${ID}: implement the change in its target repo and open
a PR. Use the client "${IFCLI}" (HTTP API at ${BASE}) for IdeaFlow, and your
local git + gh (you have write access) for the repo. Steps:

1. ${IFCLI} dump-idea ${ID}. The "repo" field is the target (owner/name or URL).
   If it's empty, stop and report that no repo is set — do not guess.
2. Clone the repo to a temp dir, create a focused branch, and make the change the
   idea and its next_action call for. Keep the change small and reviewable; add
   or update tests where it makes sense. Commit.
3. Open a PR: gh pr create --fill (or with a clear title/body). Capture the PR URL.
4. Report back — you are NOT done until this succeeds:
     ${IFCLI} log-effort ${ID} \\
       --topic 'Implemented: <short what>' \\
       --model ${MODEL} \\
       --context-file ${REPORT} \\
       --effort <1-5> --quality <1-5> --tokens <approx> \\
       --repo-url '<PR_URL>' --repo-label 'PR' \\
       --status tracking \\
       --next-action 'Critical PR review: <PR_URL>'
   (This links the PR and schedules the critical review as the next task.)
5. Print the PR URL and a two-line summary.
PROMPT
elif [[ "$MODE" == "critique" ]]; then
  read -r -d '' PROMPT <<PROMPT || true
You are a deliberately critical senior reviewer for IdeaFlow idea ${ID}. Your job
is to find problems, not to praise. Use "${IFCLI}" for IdeaFlow and gh for the PR.
Steps:

1. ${IFCLI} dump-idea ${ID}. Find the open PR URL in its resources or next_action.
   If there's no PR, stop and say so.
2. gh pr diff <PR_URL> (and view files as needed). Scrutinize for: correctness
   bugs and edge cases, security issues, missing/weak tests, scope creep, and
   simpler alternatives. Assume there are problems and look hard for them.
3. Post the critique on the PR with specific, actionable, prioritized findings:
   gh pr review <PR_URL> --request-changes --body '<findings>'  (use --comment if
   nothing blocking). Reference files/lines.
4. Record it — not done until this succeeds:
     ${IFCLI} log-effort ${ID} \\
       --topic 'Critical PR review' \\
       --model ${MODEL} \\
       --context-file ${REPORT} \\
       --effort <1-5> --quality <1-5> --tokens <approx> \\
       --next-action '<what the author must fix, or merge if truly clean>'
5. Print a one-line verdict (request-changes / approve-with-nits / block).
PROMPT
elif [[ "$MODE" == "review" ]]; then
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
       --model ${MODEL} \\
       --context-file ${REPORT} \\
       --effort <1-5> --quality <1-5> --tokens <approx> \\
       --next-action '<the single most valuable next step>' \\
       --exec-summary '<2-4 sentence current state of this effort>'
   Update the idea's stage/status if the review warrants it: advance a promising
   one (--stage <slug>), or --status archived for a dead end, --status tracking
   to keep watching. Only change what your review actually justifies.
6. If distinct sub-directions deserve their own tracking: when this is a
   top-level idea (dump-idea shows "parent": null) propose up to 5 child ideas
     ${IFCLI} add-child ${ID} --title '<child title>' --summary '<why>'
   When this idea is itself a child, do NOT create children — suggest them for a
   human instead:
     ${IFCLI} suggest-children ${ID} --suggestion '<idea>' --suggestion '<idea>'
7. Print the new ResearchEntry id and a two-line summary of your assessment.
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
       --model ${MODEL} \\
       --context-file ${REPORT} \\
       --effort <1-5, how much work> \\
       --quality <1-5, your confidence in the findings> \\
       --tokens <approx tokens used> \\
       --status tracking
   If the idea has a natural next stage, add --stage <slug> too.
6. If distinct sub-directions deserve their own tracking: when this is a
   top-level idea (dump-idea shows "parent": null) propose up to 5 child ideas
     ${IFCLI} add-child ${ID} --title '<child title>' --summary '<why>'
   When this idea is itself a child, don't create children — suggest them for a
   human instead:
     ${IFCLI} suggest-children ${ID} --suggestion '<idea>'
7. Print the new ResearchEntry id, how many feeds you registered, and a
   two-line summary.
PROMPT
fi

echo "→ ${MODE}: ${TITLE:-(untitled)} (#${ID}) against ${BASE}; report scratch file: ${REPORT}" >&2

claude -p "$PROMPT" \
  --allowedTools "Bash,Read,Write,WebSearch,WebFetch"
