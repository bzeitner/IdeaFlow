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
.venv/bin/pip install django
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Then open http://127.0.0.1:8000/.

For the Django admin: `.venv/bin/python manage.py createsuperuser`, then `/admin/`.

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
