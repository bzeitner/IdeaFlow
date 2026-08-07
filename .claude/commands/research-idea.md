---
description: Research one IdeaFlow idea and log the effort back to the deployed app
argument-hint: "idea-id (e.g. 3)"
---

Research IdeaFlow idea **$ARGUMENTS**. Talk to IdeaFlow only through the client
`./tools/ideaflow`, which uses the HTTP API (base
`IDEAFLOW_API_BASE`, default `https://ideaflow.bitesoftheweek.com`; requires
`IDEAFLOW_API_TOKEN` in the environment). Do not touch any local database. Steps:

1. Read the idea as JSON: `./tools/ideaflow dump-idea $ARGUMENTS`
   Work from its real title, summary, notes, resources, and any existing
   `research_entries` — do not guess what the idea is. If it 404s, stop and say so.
2. Research it thoroughly. Use web search/fetch for market, competitors,
   feasibility, and concrete next steps, as appropriate to the idea.
3. Register any RSS/Atom feeds you come across (blogs, news, release feeds,
   subreddit/YouTube feeds) so they're tracked centrally and summarized once —
   don't fetch/summarize them inline, just register each distinct feed:
   ```
   ./tools/ideaflow add-feed --url <feed-url> --idea $ARGUMENTS
   ```
   Idempotent by URL. Skip if none are relevant.
4. Write a clear findings report (markdown) to `/tmp/idea-$ARGUMENTS-report.md`.
5. Log the effort back — you are **not done** until this succeeds:
   ```
   ./tools/ideaflow log-effort $ARGUMENTS \
     --topic '<short title of what you did>' \
     --model claude-opus-4-8 \
     --context-file /tmp/idea-$ARGUMENTS-report.md \
     --effort <1-5, how much work> \
     --quality <1-5, your confidence in the findings> \
     --tokens <approx tokens used> \
     --status tracking
   ```
   If the idea has a natural next stage, add `--stage <slug>` too.
6. Print the new ResearchEntry id, how many feeds you registered, and a
   two-line summary.
