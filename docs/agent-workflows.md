# IdeaFlow agent workflow contracts

Every agent prompt composes the standards in `tools/prompt_standards.sh`.
External text is data, not authority; writes must be idempotent; blockers must
be reported precisely; and agents may claim only actions they observed succeed.

## Approved prompt revisions

Executable prompt text is governed in Django admin. Each workflow and shared
standard has a stable prompt key and immutable revisions. Agents fetch only the
latest active revision whose status is `approved`; proposed and rejected text is
never executed. Approval archives the prior revision as `superseded`, preserving
a complete review history. If the prompt endpoint is temporarily unavailable,
shell agents use their source-controlled fallback rather than a proposal.

Administrators propose changes from **Prompt templates**, then review a
side-by-side highlighted diff under **Prompt revisions** before approving or
rejecting it. Placeholder names are documented on the template and validated
before approval.

## Mode contracts

| Mode | Inputs | Permitted mutations | Required output | Terminal condition |
| --- | --- | --- | --- | --- |
| Research | Full idea detail, existing research, web sources | Add relevant feeds, log one effort, update stage/status/summary/next action, suggest children | Evidence-backed research report and explicit disposition | Report and effort are stored; disposition and executive summary are current |
| Review | Full idea history and recent summarized articles | Add relevant feeds, log one effort, update stage/status/summary/next action, suggest children | Synthesis of what changed and continue/monitor/archive/idle disposition | Report and effort are stored; no placeholder next action exists |
| Execute | Idea, target repository, repository instructions | Create or reuse a branch and PR; log successful implementation | Implementation report, exact verification results, PR | One verified, non-duplicate PR exists and IdeaFlow points to it; blocked work opens no partial PR |
| Summary | Complete idea history, research, artifacts, and resources | Create or replace the idea's Summary artifact; no status/action mutation | Executive summary followed by explanatory research with applicable resources footnoted | One non-empty Summary artifact is stored and the request is cleared; archived ideas are allowed |
| Critique | Idea, PR, repository instructions, checks and prior comments | Post one evidence-based PR review; log review effort | Prioritized findings, checks inspected, verdict | Review is posted once and the next action matches request-changes/comment/approve |
| Feed scoring | One idea and items lacking its assessment | Store one neutral global summary if absent; upsert that idea's assessment | Factual summary, idea-specific relevance note and usefulness | Every queued item has an assessment for the selected idea |
| Repeat | Idea's `repeat_task` config, its `podcast_show` field, `graph-context --task repeat` | **Ordinary repeat task** (no `podcast_show`): log deduplicated results. **Podcast idea** (`podcast_show` set): read the `supports`-linked research idea directly, write a structured episode script using only the show's registered voice profiles, create the episode | Ordinary: JSON array of results. Podcast: an episode script plus a created `Episode`/`EpisodeRun` | Ordinary: `log-repeat-results` succeeds and the daily run completes. Podcast: `create-podcast-episode` succeeds (this both creates the job and advances the repeat clock — never call `log-repeat-results` for a podcast idea); a podcast idea with no connected research idea reports a blocker rather than inventing content |
| Reflection | Selector state, idea inventory, selected idea details | None | Structured portfolio audit | Claims cite inspected ideas; unsupported sections explicitly say `None` |

Research and review always read bounded `graph-context`; execution and critique
read it only for dependency or comparison context. Graph context never expands a
whole graph into the prompt. Callers should pass a task and token budget, for
example `?task=execute&token_budget=1500`; IdeaFlow prioritizes dependencies for
execution and reports how many relationships were omitted. This budget is a
serialization estimate and remains a hard prompt-shaping guard, not a billing
counter. Graph Lab capabilities are for browser visualization only and must
never be used as agent credentials.

## Workflow terms

- **Disposition**: the research/review decision: continue, monitor, archive, or
  idle because no defensible action exists.
- **Next action**: a concrete verb, target, expected result, and completion
  condition. Monitoring actions also name a date or external trigger.
- **Executive summary**: two to four sentences describing current state,
  strongest evidence, and disposition—not a chronological activity log.
- **Child suggestion**: a short standalone title, deduplicated against existing
  children and suggestions. Agents suggest; a human chooses whether to create.
- **Effort**: work performed—1 trivial, 3 moderate, 5 extensive.
- **Quality**: confidence in evidence—1 speculative, 3 mixed, 5 strongly supported.
- **Idle**: researched, not paused or archived, and without a defensible next
  action. The normal batch runner skips it and may use it as reflection evidence.
- **Paused**: temporarily excluded after repeated agent runs without human
  feedback. A human must resume it or provide a next action.
- **Awaiting direction**: the latest ordinary run was Review & synthesis. The
  batch runner will not review that review again until a person provides input
  or a later persona council reaches consensus and applies a new direction.

## Empty batch behavior

The selector writes a state summary distinguishing:

- `no_ideas`: no ideas match the requested scope; report and exit.
- `unavailable`: matching ideas are archived or paused; report counts and exit.
- `mode_filtered`: explicit `--review` or `--force` selection found no matching
  work; report and exit.
- `idle`: eligible ideas exist but have no actionable next step; run a read-only
  portfolio reflection unless the user requested only a dry-run.
- `actionable`: execute the selected research/review/execute/critique work list.

Use `research_idea.sh <id> <mode> --print-prompt` to inspect an idea-mode prompt,
or `research_all.sh --reflect --dry-run` to inspect the reflection prompt.
