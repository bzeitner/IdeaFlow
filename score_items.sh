#!/usr/bin/env bash
#
# Launch a headless Claude Code agent to SCORE one idea's unsummarized feed
# items in the DEPLOYED app — the read side of the loop that research_idea.sh
# drives on the write side.
#
#   IDEAFLOW_API_TOKEN=... ./score_items.sh <idea-id> [options]
#
# The app has no LLM dependency by design (`manage.py summarize_feed_item` is
# documented as "the ingesting agent's write-back"), so the intelligence lives
# here, outside it. Everything goes through tools/ideaflow over HTTP, so this
# runs from any machine with no repo database access.
#
# Options:
#   --limit N         items per run (default 25; 0 for the whole queue)
#   --min-rating N    only feeds this idea rates >= N (default 4)
#   --since-days N    recency window (default 30; undated items are kept)
#   --dry-run         print the queue, launch nothing
#
# Config (env):
#   IDEAFLOW_API_BASE   default https://ideaflow.bitesoftheweek.com
#   IDEAFLOW_API_TOKEN  required — the shared bearer token

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
IFCLI="$SCRIPT_DIR/tools/ideaflow"
BASE="${IDEAFLOW_API_BASE:-https://ideaflow.bitesoftheweek.com}"

ID="${1:-}"
if [[ -z "$ID" || ! "$ID" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 <idea-id> [--limit N] [--min-rating N] [--since-days N] [--dry-run]" >&2
  exit 2
fi
shift

LIMIT=25
MIN_RATING=4
SINCE_DAYS=30
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit)       LIMIT="${2:-}";       shift 2 ;;
    --min-rating)  MIN_RATING="${2:-}";  shift 2 ;;
    --since-days)  SINCE_DAYS="${2:-}";  shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
for n in "$LIMIT" "$MIN_RATING" "$SINCE_DAYS"; do
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "error: --limit/--min-rating/--since-days must be whole numbers." >&2; exit 2; }
done

if [[ -z "${IDEAFLOW_API_TOKEN:-}" ]]; then
  echo "error: set IDEAFLOW_API_TOKEN (the IdeaFlow API bearer token)." >&2
  exit 1
fi

QUEUE="$(mktemp -t "idea-${ID}-queue.XXXXXX.json")"
python3 "$SCRIPT_DIR/tools/score_queue.py" "$ID" \
  --min-rating "$MIN_RATING" --since-days "$SINCE_DAYS" --limit "$LIMIT" > "$QUEUE"

SIZE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["returned"])' "$QUEUE")"
TOTAL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["queue_size"])' "$QUEUE")"
echo "→ idea ${ID}: ${SIZE} of ${TOTAL} queued items (rating>=${MIN_RATING}, last ${SINCE_DAYS}d) at ${BASE}" >&2
echo "  queue file: ${QUEUE}" >&2

if [[ "$SIZE" -eq 0 ]]; then
  echo "Nothing to score — the queue is empty." >&2
  exit 0
fi
if [[ "$DRY_RUN" -eq 1 ]]; then
  cat "$QUEUE"
  exit 0
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "error: the 'claude' CLI isn't on your PATH." >&2
  exit 1
fi

read -r -d '' PROMPT <<PROMPT || true
Score IdeaFlow feed items for idea ${ID}. Talk to IdeaFlow only through the
client "${IFCLI}" (HTTP API at ${BASE}); do not touch any local database.

The work queue is the JSON file ${QUEUE}: the idea (with its title and summary)
and ${SIZE} unsummarized items, each with its title, link, and the idea's 1-5
rating of the feed it came from. Read that file first.

For EVERY item in the queue, in order:

1. Judge it against THIS idea, not in the abstract. Idea ${ID} tracks what its
   summary says it tracks — an item is useful to the extent that it tells the
   reader something they'd act on or want to know about that.
2. Fetch the link when the title alone doesn't tell you what the item actually
   says (WebFetch). Skip the fetch when the title is already decisive — a
   marketing customer story or an off-topic release note needs no fetch.
3. Write it back:
     ${IFCLI} summarize-item <item-id> \\
       --summary '<1-3 sentences: what it says, and why it matters to this idea>' \\
       --model claude-opus-4-8 \\
       --usefulness <1-5>

Usefulness scale — apply it consistently, and use the whole range:
  5 substantive primary source that changes what the reader should do or believe
  4 solid primary/technical content worth reading
  3 useful but derivative, aggregated, or already covered by a primary feed
  2 tangential to this idea, or thin
  1 noise for this idea: marketing, off-topic, or content-free

Rules:
  * One summarize-item call per item. An item you skip stays in the queue
    forever, so score every one, including the ones you rate 1.
  * The summary is what the human reads INSTEAD of the article — write the
    finding ("X hits N% at 1/6 the cost"), not a description of the genre.
  * Do not add feeds, log efforts, or change the idea. Scoring only.

When done, print: how many items you scored, the distribution of your 1-5
usefulness ratings, and the 3 items most worth the human's time.
PROMPT

claude -p "$PROMPT" \
  --allowedTools "Bash,Read,WebFetch,WebSearch"
