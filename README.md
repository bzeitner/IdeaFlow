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
