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
| `DELETE /api/ideas/<id>/resources/<resource-id>/` | Remove a verified stale resource |
| `GET /api/ideas/<id>/graph-context/` | Token-budgeted context (`?task=research|review|execute|critique&token_budget=2500`) |
| `POST /api/ideas/<id>/effort/` | Record an effort report |
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
./tools/ideaflow log-effort 12 --topic "Prototyped it" --model claude-opus-4-8 \
  --context-file report.md --effort 4 --quality 5 --tokens 180000 --status tracking
./tools/ideaflow add-feed --url https://example.com/feed.xml --idea 12
./tools/ideaflow feed-items --idea 12 --unassessed
./tools/ideaflow summarize-item 42 --idea 12 --summary-file s.md --model claude-haiku-4-5 --usefulness 4
```

The `research_idea.sh` / `research_all.sh` scripts and the `/research-idea`
command all drive this client, so they run from any machine — see
[`deploy/README.md`](deploy/README.md) §14 for the "clone + set token" bootstrap.
The inputs, allowed mutations, outputs, terminal conditions, and shared safety
standards for every mode are documented in
[`docs/agent-workflows.md`](docs/agent-workflows.md).

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
lighter model (Haiku) while research/review/execute/critique use the heavy one.
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

**More agent modes** (`research_idea.sh <id> <mode>`): `execute` branches an
idea's target `repo`, makes the change, opens a PR, and schedules a **critical
PR review** as the next action; `critique` runs a deliberately critical persona
over that PR. Both run on your laptop with your `gh` auth. The review agent also
keeps each idea's **executive summary** current (shown on the detail page, where
each effort's in-depth write-up is collapsed behind a click). Review and critique
agents check every listed GitHub PR and remove its resource after `gh` reports it
closed or merged; failed lookups are left untouched.

`research_all.sh` researches ideas with no research yet and **reviews**
already-researched ideas only when they have a clear next action. Researched
ideas without one are skipped so the runner proceeds to the next actionable
idea instead of re-analyzing them. If eligible ideas exist but all are idle, it
runs a structured read-only portfolio reflection. If no ideas match, or all are
paused/archived, it reports the reason and exits without launching an agent.
Flags: `--review`, `--force`,
`--reflect`, `--status`, `--delay`, `--dry-run`. Once an idea has research, its
detail page prompts for that single next action (also settable by the review
agent via `log-effort --next-action`). After two agent runs without human input,
an idea pauses until a person adds a next action or chooses **Continue work**.

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
