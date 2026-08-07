---
description: Research one IdeaFlow idea and log the effort back into the app
argument-hint: "idea-id (e.g. 3)"
---

Research IdeaFlow idea **$ARGUMENTS**. Work in this repo. Steps:

1. Read the idea as JSON: `.venv/bin/python manage.py dump_idea $ARGUMENTS`
   Work from its real title, summary, notes, resources, and any existing
   `research_entries` — do not guess what the idea is. If the id doesn't exist,
   stop and say so.
2. Research it thoroughly. Use web search/fetch for market, competitors,
   feasibility, and concrete next steps, as appropriate to the idea.
3. Register any RSS/Atom feeds you come across (blogs, news, release feeds,
   subreddit/YouTube feeds) so they're tracked centrally and summarized once —
   don't fetch/summarize them inline, just register them:
   ```
   .venv/bin/python manage.py add_feed --url <feed-url> --idea $ARGUMENTS
   ```
   Idempotent by URL; run once per distinct feed. Skip if none are relevant.
4. Write a clear findings report (markdown) to `/tmp/idea-$ARGUMENTS-report.md`.
5. Log the effort back into IdeaFlow — you are **not done** until this succeeds:
   ```
   .venv/bin/python manage.py log_effort $ARGUMENTS \
     --topic '<short title of what you did>' \
     --model claude-opus-4-8 \
     --context-file /tmp/idea-$ARGUMENTS-report.md \
     --effort <1-5, how much work> \
     --quality <1-5, your confidence in the findings> \
     --tokens <approx tokens used> \
     --status tracking
   ```
   If the idea has a natural next stage, add `--stage <slug>` too (stages come
   from the `dump_idea` output / the admin).
6. Print the new ResearchEntry id, how many feeds you registered, and a
   two-line summary.
