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

Every signed-in user gets a `Profile` with five independent role flags:

| Role | Grants |
| --- | --- |
| Admin | Everything — every tab, adding ideas, `/admin/`, and User Management |
| Current | View and manage ideas in the Current tab |
| Tracking | View and manage ideas in the Tracking tab |
| Archive | View and manage ideas in the Archive tab |
| Add Ideas | Create new ideas |

New users start with **no roles** — an admin has to grant access at `/users/` (linked from
the top bar as "Users" for admins). The one exception: `bzeitner@gmail.com` always gets every
role automatically on first sign-in. Moving an idea between tabs only requires the role for
the tab it's currently in, not the destination tab.

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
| `POST /api/ideas/<id>/effort/` | Record an effort report |
| `GET /api/feeds/` · `POST /api/feeds/` | List feeds · register one (`{url, title?, idea_id?}`) |
| `GET /api/feed-items/` | Feed items (`?unsummarized=1`, `?feed=<id>`) |
| `POST /api/feed-items/<id>/summarize/` | Agent summary + usefulness (`{summary, model, usefulness}`) |

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
./tools/ideaflow feed-items --unsummarized
./tools/ideaflow summarize-item 42 --summary-file s.md --model claude-opus-4-8 --usefulness 4
```

The `research_idea.sh` / `research_all.sh` scripts and the `/research-idea`
command all drive this client, so they run from any machine — see
[`deploy/README.md`](deploy/README.md) §14 for the "clone + set token" bootstrap.

`research_all.sh` is tiered so an agent always has useful work: it researches
ideas with no research yet, and when there are none it **reviews** the
already-researched ones (`research_idea.sh <id> review` — synthesize progress,
update stage/status, and set each idea's **next action**), and if there are no
ideas at all it reflects on the project. Flags: `--review`, `--force`,
`--reflect`, `--status`, `--delay`, `--dry-run`. Once an idea has research, its
detail page prompts for that single next action (also settable by the review
agent via `log-effort --next-action`).

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
| `usefulness` | 1–5 | the ingesting agent, when it summarizes |
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

# Agent write-back: summary + a 1-5 usefulness rating (stamps it "done")
manage.py summarize_feed_item 42 \
    --summary-file summary.md --model claude-opus-4-8 --usefulness 4
```

Run `refresh_feeds` on a schedule (cron / systemd timer / `/loop`); it's
idempotent, so re-running only picks up genuinely new entries.

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
