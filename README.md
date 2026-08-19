# IdeaFlow

A Django app for tracking "ideas" — work efforts that could become projects, businesses,
or side projects.

## Structure

Every idea has a **status** that decides which tab it appears on:

| Tab | Status | What it holds |
| --- | --- | --- |
| Current | `current` | The efforts actively being worked on |
| Tracking | `tracking` | Everything on the radar, with state + ranking at a glance |
| Archive | `archived` | Shelved or finished efforts |

Ideas move between tabs with the `→ Current` / `→ Tracking` / `→ Archive` buttons.

### An idea

- **Title**, **Summary**, **Notes**
- **Category** — Project, Side Project, Passive Income, Research Effort, Focus Project
- **Interest Level** — 1–5 stars
- **Stage** — Spark, Exploring, Building, Launched, Stalled (the "current state" on the tracking tab)
- **Rank** — manual sort order, lower first
- **Resources** — a list of labeled links

## Managing the dropdowns

**Category** and **Stage** are database tables, edited under **Manage** (the Django admin)
at `/admin/`. Each option has a name, a slug, a hex **color** (used for its pill throughout
the app), an **order** in the dropdown, and an **active** flag. Order and active are editable
straight from the list page.

Deactivating an option removes it from the new-idea dropdown but leaves it on ideas already
using it — those still render, and the option reappears in the dropdown when you edit one of
them. Options in use can't be deleted (the FK is `PROTECT`); deactivate instead.

**Status** is deliberately *not* editable: the three values are structural, each backed by its
own route and template. Adding a fourth tab is a code change.

The five categories and five stages above are seeded by migration `0002_seed_lookups`, so a
fresh `migrate` starts with them already in place.

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
.venv/bin/python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# paste that into DJANGO_SECRET_KEY in .env, and set DJANGO_DEBUG=true for local work

.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Then open http://127.0.0.1:8000/. Logged-out visitors see a public overview page; signing
in requires Google OAuth credentials (see below) — without them the "Sign in with Google"
button will error.

## Google sign-in setup

The app has no local username/password accounts — the only way in is Google. To run it
locally you need your own OAuth client:

1. In [Google Cloud Console](https://console.cloud.google.com/), create or reuse a project,
   then go to **APIs & Services → OAuth consent screen** and configure it (External is fine;
   add your own email as a test user if it stays in Testing mode).
2. **APIs & Services → Credentials → Create Credentials → OAuth client ID → Web application**.
   Add this **Authorized redirect URI**: `http://127.0.0.1:8000/accounts/google/login/callback/`
3. Copy the resulting Client ID and Client Secret into `.env`:
   ```
   GOOGLE_OAUTH_CLIENT_ID=...
   GOOGLE_OAUTH_CLIENT_SECRET=...
   ```

## Roles & access

Every signed-in user gets a `Profile` with six independent role flags:

| Role | Grants |
| --- | --- |
| Admin | Everything — every tab, adding ideas, `/admin/`, and User Management |
| Current | View and manage ideas in the Current tab |
| Tracking | View and manage ideas in the Tracking tab |
| Archive | View and manage ideas in the Archive tab |
| Add Ideas | Create new ideas |
| Knowledge Graph | Inspect relationships and launch the read-only Graph Lab |

New users start with **no roles** — an admin has to grant access at `/users/` (linked from
the top bar as "Users" for admins). The one exception: `bzeitner@gmail.com` always gets every
role automatically on first sign-in. Moving an idea between tabs only requires the role for
the tab it's currently in, not the destination tab.

An idea marked **Public** (`is_public`) is listed on the home page and readable by *any*
signed-in user, including those with no tab roles — but editing it still requires the tab's
role, so public means view-only for everyone else. The home page shows these public projects
to everyone signed in; role-holders reach their tabs from the top nav.

## Configuration

Settings that shouldn't be in version control live in `.env`, which is gitignored. Real
environment variables take precedence over the file, so a deploy can set these without one.

| Variable | Notes |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required. No default — startup fails loudly if it's missing, rather than falling back to a value that could ship to production. |
| `DJANGO_DEBUG` | `true`/`false`, defaults to **false**. Never true in production. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated, defaults to `localhost,127.0.0.1`. |
| `DATABASE_URL` | Optional. Unset uses a local sqlite3 file. See "Using Postgres" below. |

`.env.example` is the committed template — add any new variable there (with an empty or
safe value) when you introduce one.

For the Django admin: `.venv/bin/python manage.py createsuperuser`, then `/admin/`.

## Using Postgres

By default the app uses a local `db.sqlite3` file — nothing to configure. To use Postgres
instead (recommended for any real deployment), set `DATABASE_URL` in `.env`:

```
DATABASE_URL=postgres://USER:PASSWORD@HOST:PORT/DBNAME
```

For local Postgres via Homebrew:

```bash
brew install postgresql@16
brew services start postgresql@16
createdb ideaflow
```

then in `.env`:

```
DATABASE_URL=postgres://YOUR_MAC_USERNAME@localhost:5432/ideaflow
```

Run `migrate` as usual afterward — `DATABASE_URL` is read at startup, so it applies to
`runserver`, `migrate`, `test`, everything.

## Agent access (read an idea, report effort)

An agent can pull an idea, act on it (research it, spin up a repo, write code), then
report back. "Reporting" means creating a **research entry** — topic, a free-text write-up,
effort/quality (1–5), tokens used, and which AI model — and optionally attaching a result
link and moving the idea's stage/tab. Two ways in, sharing the same read/write core:

### Local: management commands

For an agent running in this repo — it talks straight to the DB, no auth needed.

```bash
# Read
.venv/bin/python manage.py dump_idea 12            # one idea, full detail, as JSON
.venv/bin/python manage.py dump_idea               # all ideas (summary rows)
.venv/bin/python manage.py dump_idea --status current

# Report effort (--context-file hands off a long write-up without shell-quoting)
.venv/bin/python manage.py log_effort 12 \
    --topic "Prototyped the CSV importer" \
    --model claude-opus-4-8 \
    --context-file report.md \
    --effort 4 --quality 5 --tokens 180000 \
    --repo-url https://github.com/you/csv-importer --repo-label "Repo" \
    --stage prototyping --status tracking
```

`--model` takes an AI-model slug or name (see the seeded list in the admin; default `other`).
`--stage`/`--status` are optional idea moves. Both commands print JSON.

### Remote: JSON HTTP API

IdeaFlow stores timezone-aware datetimes in UTC and displays them in Pacific
Time (`America/Los_Angeles`, automatically selecting PST or PDT).

For an agent that can't reach the DB directly. Set a token to enable it (empty = disabled):

```
# in .env — generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
IDEAFLOW_API_TOKEN=your-long-random-token
```

Pass it as `Authorization: Bearer <token>` (or an `X-API-Token` header):

Pass it as `Authorization: Bearer <token>` (or an `X-API-Token` header):

| Method & path | Does |
| --- | --- |
| `GET /api/ideas/` | List ideas (optional `?status=current\|tracking\|archived`) |
| `GET /api/ideas/<id>/` | One idea with resources + research entries |
| `GET /api/weekly-summaries/` | List portfolio-wide weekly executive summaries |
| `POST /api/weekly-summaries/` | Create or replace one reporting period (`{period_start, period_end, title, content, model?, tokens_used?}`) |
| `DELETE /api/ideas/<id>/resources/<resource-id>/` | Remove a verified stale resource |
| `GET /api/ideas/<id>/graph-context/` | Token-budgeted context (`?task=research|review|execute|critique&token_budget=2500`) |
| `POST /api/ideas/<id>/effort/` | Record an effort report; supports `open_questions`, replaces `next_action`, and appends `queued_next_actions` |
| `POST /api/ideas/<id>/persona-reviews/` | Record explicit persona votes and apply only a unanimous reversible proposal |
| `POST /api/ideas/<id>/research/<entry-id>/open-questions/` | Additively merge extracted questions into an existing non-archived research entry |
| `POST /api/ideas/<id>/repeat-results/` | Store a completed repeat run (`{results: [{title, url?, details?}]}`), deduplicate URLs, and advance its schedule |
| `GET /api/graph/` | Active knowledge-graph projection (`?archived=1` includes archived ideas) |
| `GET /api/graph/neighborhood/` | Bounded neighborhood (`?idea=<id>&depth=1&max_nodes=50`) |
| `GET /api/graph/search/` | Search graph ideas (`?q=<text>`) |
| `GET /api/feeds/` · `POST /api/feeds/` | List feeds · register one (`{url, title?, idea_id?}`) |
| `GET /api/feed-items/` | Feed items (`?unassessed=1&idea=<id>`, `?unsummarized=1`, `?feed=<id>`, `?limit=&offset=`, `?content=1`) |
| `POST /api/feed-items/<id>/summarize/` | Neutral global summary + per-idea assessment (`{summary, model, idea_id, usefulness, relevance_note}`) |

```bash
curl -H "Authorization: Bearer $IDEAFLOW_API_TOKEN" \
  https://ideaflow.bitesoftheweek.com/api/ideas/12/
```

### From another machine: the `tools/ideaflow` client

`tools/ideaflow` is a standalone, dependency-free (stdlib-only) client that
wraps the API, so an agent on any box drives the whole loop without a repo/DB.
Point it at the deployed hub and give it the token:

```bash
export IDEAFLOW_API_BASE=https://ideaflow.bitesoftheweek.com   # already the default
export IDEAFLOW_API_TOKEN=<the token from the server .env>

./tools/ideaflow list-ideas
./tools/ideaflow dump-idea 12
./tools/ideaflow weekly-summaries
./tools/ideaflow log-weekly-summary --period-start 2026-08-09 --period-end 2026-08-15 \
  --title "Week ending 2026-08-15" --summary-file weekly.md --metrics-file metrics.json \
  --model claude-opus-4-8
./tools/ideaflow log-effort 12 --topic "Prototyped it" --model claude-opus-4-8 \
  --context-file report.md --effort 4 --quality 5 --tokens 180000 --status tracking
./tools/ideaflow add-feed --url https://example.com/feed.xml --idea 12
./tools/ideaflow feed-items --idea 12 --unassessed
./tools/ideaflow summarize-item 42 --idea 12 --summary-file s.md --model claude-haiku-4-5 --usefulness 4
```

The `research_idea.sh` / `research_all.sh` scripts and the `/research-idea`
command all drive this client, so they run from any machine — see
[`deploy/README.md`](deploy/README.md) §14 for the "clone + set token" bootstrap.
These workflows automatically read `IDEAFLOW_API_TOKEN` and
`IDEAFLOW_API_BASE` from the repository `.env` when they are not already
exported. Explicit environment values take precedence for individual runs.
The inputs, allowed mutations, outputs, terminal conditions, and shared safety
standards for every mode are documented in
[`docs/agent-workflows.md`](docs/agent-workflows.md).

### Prompt governance

Administrators manage executable AI prompts under **Manage → Prompt templates**.
Every template has a stable runtime key, documented placeholders, and an
immutable revision history. **Propose change** copies the current approved text
into a new proposal; the proposal review page displays the approved and proposed
versions side by side with additions, deletions, and changed text highlighted.
Only an explicit **Approve proposal** action makes a revision executable. The
previous approved version is retained as superseded, while rejected proposals
remain reviewable. Prompt revision bodies cannot be edited in place.

The registry covers research, review, execution, critique, repeat tasks,
portfolio reflection, weekly portfolio summaries, feed scoring, shared agent standards, semantic relationship
classification, and open-question extraction. Agent scripts fetch only active,
approved revisions through the authenticated API and retain source-controlled
fallbacks for availability during a deployment or API outage. The admin UI also
adds mouse-over descriptions to fields, models, columns, and action controls.

**Knowledge graph.** Users with the Knowledge Graph role get a Graph tab that
projects parent-child links and typed idea relationships directly from canonical
PostgreSQL data. Agents read bounded context with `./tools/ideaflow graph-context
<idea-id>`; `graph` returns the active projection. Relationship edits require
graph access plus permission to manage the source idea. `manage.py audit_graph`
checks dependency cycles, and `manage.py rebuild_graph_revision` invalidates
projection caches after operational repairs. Cytoscape.js is bundled locally.

When `IDEAFLOW_GRAPH_LAB_ENABLED=true`, the Graph tab also launches a pinned,
self-hosted Gephi Lite build on a separate browser origin. IdeaFlow checks the
Knowledge Graph role and sends that origin a short-lived, read-only capability;
the IdeaFlow login cookie and API token are never shared. GraphML exports are
bounded by node, edge, and byte limits. See [`deploy/README.md`](deploy/README.md)
for build, installation, DNS, rollback, and security verification steps.

With PostgreSQL's `vector` extension enabled, changed idea and research text is
automatically marked for semantic processing. The
`process_semantic_graph` worker embeds it, retrieves nearby ideas with an HNSW
cosine index, and asks a configurable OpenAI-compatible classifier for precise,
evidence-backed relationship types. Results appear as suggestions in the Graph
tab; accepting one promotes it to a canonical agent-provenance relationship,
while rejection is remembered until the underlying content changes. Install
`deploy/ideaflow-semantic-graph.{service,timer}` to process changes every five
minutes. See `deploy/env.production.example` for model/API settings.
Relationships above the configurable auto-accept confidence are promoted
immediately. The default is 90%; exactly 90% remains pending. Administrators
can change it under **Admin → Semantic graph settings**.
Dependency recommendations that would create a cycle are discarded before
review. `manage.py prune_cyclic_suggestions` cleans up any older pending cyclic
recommendations created before this guard existed.

**Model routing.** Task→model mapping lives in `IDEAFLOW_TASK_MODELS` (settings)
and is served at `/api/config`; cheap work like feed/blog summaries routes to a
lighter model (Haiku) while research/review/execute/critique/weekly-summary use the heavy one.
`research_idea.sh` fetches its model from there per mode. Feed summarizing is
bounded — `feed-items --idea <id> --per-feed 5` caps summaries per feed per idea
per run, and `--limit/--offset` page the queue instead of downloading the whole
corpus. Items carry the entry body as ingested (`--content`), so a scoring agent
judges them without re-fetching the page.

**Scoring loop.** `score_items.sh <idea> [--limit N]` builds a queue with
`tools/score_queue.py` (the idea's feeds, above a rating floor, within a recency
window) and runs a headless agent that writes one factual, idea-neutral summary
and rates each item 1-5 against *that* idea. Assessments are stored per idea, so
a shared item can be essential to one idea and irrelevant to another.
`deploy/ideaflow-score-items.{service,timer}` runs it daily.

**Weekly portfolio summary.** `weekly_summary.sh` runs each Sunday at 12:01 AM through
`deploy/ideaflow-weekly-summary.{service,timer}`, reads every idea and its full
research history through the HTTP client, and stores one executive summary for
the prior Sunday-Saturday and backfills older activity weeks with no stored summary. The permission-gated **Weekly Summary** tab expands the
latest report and keeps older reports collapsed but available.

**More agent modes** (`research_idea.sh <id> <mode>`): `execute` branches an
idea's target `repo`, makes the change, opens a PR, and schedules a **critical
PR review** as the next action; `critique` runs a deliberately critical persona
over that PR. When that review finds no issues and required checks pass, the
reviewer merges the PR, verifies the merged state, and reconciles the completed
review action in IdeaFlow. Both run on your laptop with your `gh` auth. Every successful agent
effort replaces the idea's **Latest effort summary** with a concise outcome and
up to three recommended next steps for human readers (shown at the top of the
detail page, where each effort's in-depth write-up is collapsed behind a click).
Review and critique
agents check every listed GitHub PR and remove its resource after `gh` reports it
closed or merged; failed lookups are left untouched.

**Persona councils.** Each idea can enable stalled-work review with its own
day threshold and one or more assigned personas. New ideas receive draft
owner-goals, delivery, and risk personas by default; administrators can edit
personas and each idea's required membership. After the configured interval
without meaningful progress, the task runner selects a persona review using
bounded parent, child, sibling, and relationship context. Every required persona
must explicitly approve, reject, or abstain. Only unanimous proposals act, and
the API additionally requires an allowlisted reversible action type and verb;
rejection or abstention records the review without changing the idea.
Council-supported question answers are retained inside the review proposal with
persona-consensus provenance and never overwrite fields reserved for human answers.

`research_all.sh` researches ideas with no research yet and **reviews**
already-researched ideas only when they have a clear next action. Researched
ideas without one are skipped so the runner proceeds to the next actionable
idea instead of re-analyzing them. If eligible ideas exist but all are idle, it
runs a structured read-only portfolio reflection. If no ideas match, or all are
paused/archived, it reports the reason and exits without launching an agent.
Flags: `--review`, `--force`,
`--reflect`, `--status`, `--dry-run`. Once an idea has research, its
detail page identifies the active next action (also settable by the review agent
via `log-effort --next-action`). After two agent runs without human input,
an idea pauses until a person adds a next action or chooses **Continue work**.
Ideas can hold an ordered queue of next actions. The first item remains the
active `next_action` used by task selection; the detail page can add, reorder,
complete, or remove later items. Agents may append concrete follow-ups with the
repeatable `--queue-next-action` option without replacing the active item.
Agents record questions that genuinely require human input with repeatable
`--open-question` options. Unanswered questions appear on the idea page with
answer fields. Saving an answer counts as human feedback, resumes paused agent
work, and exposes the answer in `research_entries.question_answers` on the next
`dump-idea` call.

To backfill older reports, first preview deterministic extraction from Markdown
sections, then save it:

```bash
.venv/bin/python manage.py extract_open_questions --dry-run --limit 1000
.venv/bin/python manage.py extract_open_questions --limit 1000
```

Add `--use-ai` to inspect prose that lacks a recognizable Open Questions
section; this uses `IDEAFLOW_SEMANTIC_API_KEY` and the configured classifier
model. Use `--idea ID` to scope a run, or `--all` to merge newly detected
questions into entries that already have structured questions.

For the same workflow from a local checkout against the deployed API, use the
standalone script. It reads `IDEAFLOW_API_TOKEN` and semantic settings from the
environment or the repository `.env`, previews by default, skips archived ideas,
and only writes when `--apply` is explicit:

```bash
./tools/extract_open_questions_remote.py --use-ai --limit 1000
./tools/extract_open_questions_remote.py --use-ai --limit 1000 --apply
```

The shared API token is a system agent credential rather than an individual UI
user identity. The server authenticates every request, scopes each research
entry to its idea, rejects archived mutations, and only allows additive question
merges; it cannot overwrite research text or human answers.

### Repeatable tasks and result tracking

Enable **Repeat this task** on an idea, provide a measurable per-run goal, a
target result count, and an interval in days (use `1` for daily). When due,
`research_all.sh` selects it in `repeat` mode even if ordinary idea work is
paused. The agent records a structured JSON batch through
`tools/ideaflow log-repeat-results`; non-empty URLs are deduplicated per idea and
the completion timestamp prevents another run until the interval elapses.

Repeatable ideas show a results table instead of the latest-effort summary.
People with the idea's normal status role can classify each row as New,
Interested, Applied / Actioned, or Dismissed. IdeaFlow remains the source of
truth so these mutations retain its existing authentication and role checks.
The same role can pause or resume repeat runs independently of the ordinary
agent-feedback pause; while paused, the scheduler skips the task and the repeat
results API rejects write-backs.

Google Sheets is best added as an optional one-way export or narrowly scoped
sync adapter, not as primary storage. A future adapter should use a dedicated
service account, store the sheet ID in server configuration, map rows by the
IdeaFlow result ID, allowlist writable columns, and perform all synchronization
server-side. Never expose Google credentials to browsers or research agents.

The POST body's only required field is `topic`; everything else is optional. It returns the
created entry plus the refreshed idea (`201`). The token is a single shared secret with no
per-user roles — treat it like a password and only enable the API when you need it.

## Feeds (fetch + summarize once)

Research often turns up RSS/Atom feeds. Feeds are tracked centrally so each is
downloaded only when it changes and each entry is summarized exactly once, no
matter how many ideas point at it or how often the agent runs.

- **`Feed`** — one row per URL (unique), optionally linked to ideas. Stores
  ETag/Last-Modified for conditional GETs.
- **`FeedItem`** — one row per entry, unique on `(feed, guid)`. That constraint
  is what makes ingest + summary happen once.

Ratings per item:

| Rating | Range | Who sets it |
| --- | --- | --- |
| `usefulness` | 1–5 per idea | the scoring agent, in `FeedItemAssessment` |
| `interest` | 1–5 | you (personal interest) |
| `info_value` | 1–5 | you (information value) |

Set your two ratings from the Django admin (`/admin/` → Feed items — editable
right in the list), or wherever a feed UI is added later.

Workflow (mirrors the idea commands):

```bash
# Register a feed (idempotent by URL), optionally tied to an idea
manage.py add_feed --url https://example.com/feed.xml --idea 12

# Fetch active feeds and ingest new entries (deduped, conditional GET)
manage.py refresh_feeds

# The agent's work queue: entries with no summary yet
manage.py dump_feed_items --unsummarized

# Agent write-back: neutral summary + usefulness for one idea
manage.py summarize_feed_item 42 \
    --idea 12 --summary-file summary.md --model claude-haiku-4-5 --usefulness 4
```

Run `refresh_feeds` on a schedule (cron / systemd timer / `/loop`); it's
idempotent, so re-running only picks up genuinely new entries.

**Feeds per idea.** Feeds link to ideas with a per-idea **relevance rating**
(`add-feed … --rating 1-5`). Each idea keeps only its **top 5** feeds — **10** for
ideas in a research-flagged category (`Category.is_research`) — pruning the
lowest-rated links when new ones are added. `dump-idea` returns an idea's curated
`feeds` plus its recent summarized `recent_articles`, and the review agent folds
those into its synthesis; the detail page shows both.

**Pause for feedback.** An idea pauses after **3 agent runs** without human
feedback: the effort API returns `409` and `research_all.sh` skips it until you
add a next action or click **Continue work** on its detail page.

## Deploying

To put IdeaFlow on a DigitalOcean droplet (gunicorn + nginx + Postgres, fronted
by Cloudflare for DNS/TLS on **bitesoftheweek.com**), follow the step-by-step
runbook in [`deploy/README.md`](deploy/README.md). The `deploy/` folder also
holds the systemd units, nginx config, env template, and the `update.sh` /
`backup_db.sh` helper scripts it references.

## Layout

```
ideaflow/            project settings + root urls
ideas/
  models.py          Idea, Resource, and the Category/Status/Stage choices
  views.py           three tab views, detail, create/edit, status moves
  forms.py           IdeaForm + inline ResourceFormSet
  templates/ideas/   base, one template per tab, card partial, form, detail
  static/ideas/      app.css
```
