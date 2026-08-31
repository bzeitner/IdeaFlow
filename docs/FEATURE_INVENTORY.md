# IdeaFlow Feature Inventory and Production Usage Audit

Date of audit: 2026-08-31
Sources: current repository and read-only queries against the production database.

## Executive summary

IdeaFlow is no longer primarily an idea tracker. In practice it is an AI-assisted portfolio and workflow system whose highest-volume behavior is research, relationship analysis, recurring discovery, prompt governance, and—in one focused workflow—podcast production.

The rewrite should preserve the idea as the durable unit of intent, but make every LLM call a first-class, traceable execution with a versioned input, measurable output, cost, latency, evaluation, and experiment assignment. Features that do not produce or consume evidence of value should not dictate the new architecture.

## Production snapshot

| Area | Observed production usage | Interpretation |
| --- | ---: | --- |
| Ideas | 102 | Core entity is well established. |
| Research entries | 425 | Primary AI-generated work product. |
| Recorded research tokens | 10,386,700 across all 425 entries | Token capture exists, but cost, latency, prompt version, and outcome attribution do not. |
| Artifacts | 33 | Useful durable outputs, but much less common than research logs. |
| Repeat results | 229 | Recurring discovery is actively used. |
| Idea relations | 285 | The knowledge graph has meaningful persisted data. |
| Relation suggestions | 992 | Automated candidate generation is heavily used. |
| Relationship council reviews | 481 | Multi-agent review is a substantial workload. |
| Persona reviews | 25 | Used, but specialized. |
| Weekly summaries | 4 | Early/low-volume feature. |
| Prompt templates / revisions | 19 / 39 | Governance is real and should be retained. |
| Feeds / feed items | 213 / 20,460 | High ingestion volume. |
| Feed summaries / assessments | 659 / 637 | Only 3.2% / 3.1% of ingested items reach these stages. |
| Feed items with human interest rating | 33 | 0.16% of ingested items receive this feedback. |
| Podcast shows / episodes | 4 / 18 | Specialized but real vertical. |
| Published podcast episodes | 10 | Workflow reaches a valuable terminal outcome. |
| Help messages | 0 | Not validated by production usage. |

All 102 ideas and all 425 research entries were created within the last 30 days, so production is young. Counts indicate adoption and flow, not long-term retention.

## Categories actually used

| Current category | Total | Tracking | Archived | Recommendation |
| --- | ---: | ---: | ---: | --- |
| Research | 47 | 23 | 24 | Keep as `Research`. |
| App | 15 | 4 | 11 | Merge into `Product`, with a subtype/tag if needed. |
| App Improvement | 15 | 8 | 7 | Treat as `Improvement` work linked to a parent product, not a peer category. |
| Side Project | 8 | 4 | 4 | Merge into `Product` or express as a portfolio tag. |
| Project | 7 | 6 | 1 | Keep as `Project` or merge into `Product` based on whether delivery differs. |
| Research Effort | 6 | 4 | 2 | Merge into `Research`; it duplicates the dominant category. |
| Passive Income | 2 | 2 | 0 | Make this a goal/tag, not a workflow category. |
| Book | 1 | 1 | 0 | Use a `Content` type or tag. |
| Podcast | 1 | 1 | 0 | Use a `Content` type with a podcast workflow capability. |
| News | 0 | 0 | 0 | Remove from defaults. |
| Focus Project | 0 | 0 | 0 | Remove from defaults; “focus” is a priority state. |

Recommended rewrite taxonomy:

- `Research`: questions, investigations, monitoring, and evidence synthesis.
- `Product`: apps, side projects, and other things being built.
- `Project`: finite delivery efforts that are not products.
- `Content`: books, podcasts, and other publishable outputs.
- `Improvement` should be a child work item linked to its product/project.
- `Passive income`, `focus`, and similar intent should be tags or goals.
- Preserve original category values during migration in a legacy metadata field.

The current lifecycle is also noisier than its UI model implies: production contains 53 tracking and 49 archived ideas, but no current ideas. Stages are unset for 54 ideas; the only used stages are Exploring (27), Building (20), and Stalled (1). The rewrite should use one lifecycle field and an independent priority/focus field rather than overlapping status, stage, and category semantics.

## Feature list

### Portfolio and idea management

- Create, edit, rank, own, publish, archive, and restore ideas.
- Maintain title, summary, notes, interest, lifecycle, next-action queue, repository, and parent/child relationships.
- Pause autonomous work after a run limit until human feedback is received.
- Assign owners and enforce role-based access.
- Show current, tracking, archive, research queue, research history, and public views.

Rewrite disposition: **retain and simplify**. Replace Current/Tracking plus Stage with a single lifecycle and separate priority; keep owner, archive, parent/child, next actions, and access controls.

### AI research and execution workflows

- Research, review, execute, critique, summarize, repeat, feed-score, and portfolio-reflection modes.
- Store research topic, focus, narrative result, open questions, effort, quality, model, provider, tokens, and timestamp.
- Create/update executive summaries and next actions as part of agent reporting.
- Pause an idea after repeated autonomous runs without feedback.
- Run work locally or through a token-authenticated API and shell client.

Rewrite disposition: **core feature**. Rebuild around an execution ledger and explicit workflow definitions. Existing `ResearchEntry` becomes a user-facing projection of one or more execution records, not the telemetry record itself.

### Prompt governance

- Stable prompt-template keys.
- Immutable prompt revisions with proposed, approved, rejected, and superseded states.
- Side-by-side review and explicit approval.
- Placeholder validation and source-controlled fallbacks.

Rewrite disposition: **retain and extend**. Prompt revision must be attached to every run. Add versioned model configuration, tool policy, response schema, evaluator version, and experiment eligibility.

### Artifacts and deliverables

- Upload or link reports, lists, summaries, and structured files.
- Render safe text formats inline and download other files.
- Maintain one summary artifact per idea and cross-reference artifacts between ideas.

Rewrite disposition: **retain**. Add immutable artifact versions and explicit provenance back to executions and source evidence.

### Recurring discovery and repeat results

- Configure repeat goal, cadence, target result count, pause state, and last-run time.
- Store deduplicated results and let users mark them new, interested, actioned, or dismissed.
- Link actioned results into podcast episodes and soft-delete completed backlog entries while preserving provenance.

Rewrite disposition: **retain**. The 229 results and 37 explicit status changes demonstrate real use. Add outcome conversion metrics and use them as evaluator labels.

### Knowledge graph and councils

- Store typed idea relationships with provenance.
- Generate semantic relationship suggestions.
- Review suggestions manually or through multi-persona/provider councils.
- Expose graph, neighborhood, search, export, bounded prompt context, and a graph lab.

Rewrite disposition: **retain, but make graph derivation measurable**. Each suggested edge must point to the generating execution, confidence/evaluator outputs, review decision, and later reversals. Accepted-edge precision is the primary quality metric.

### Feeds and evidence discovery

- Register RSS/Atom feeds once and associate them with multiple ideas.
- Fetch conditionally using ETag/Last-Modified and prevent unsafe/internal URL access.
- Store deduplicated items and source content.
- Produce a global neutral summary and an idea-specific usefulness assessment.
- Rate feed and item relevance; cap feeds per idea and prune lower-ranked associations.
- Pause ingestion per idea.

Rewrite disposition: **replace the feed product with a source/evidence pipeline**. Preserve source registration, safe fetching, deduplication, and idea-specific scoring. Do not preserve a large global inbox as a primary UX.

### Weekly portfolio reporting

- Generate weekly narrative summaries.
- Store token metrics by task, model, category, and idea plus open pull requests.
- Compare reporting periods.

Rewrite disposition: **retain as a derived analytics view**, generated from the execution ledger rather than separately reported estimates.

### Podcast workflow

- Configure a show and source idea.
- Generate structured scripts from research/repeat results.
- Queue leased audio rendering jobs with heartbeat, failure, retry, and completion handling.
- Review, approve, publish, unpublish, and expose public episode pages and RSS.
- Track voice profiles and render manifests/reports.

Rewrite disposition: **retain as an optional vertical module**. The 10 published episodes prove an end-to-end outcome. Script-generation runs and rendering runs should share a trace but use different telemetry types (LLM vs deterministic/media job).

### User experience and administration

- Google sign-in, profiles, roles, preferences, admin-managed lookup values, ownership reassignment, guide, and help conversation.

Rewrite disposition: retain authentication, ownership, preferences, and access control. Defer the help conversation until there is a validated use case; it has zero production messages.

## Feed structure and value assessment

### What is working

- The shared `Feed` → `FeedItem` model avoids downloading the same URL repeatedly.
- `IdeaFeed` correctly recognizes that source relevance is idea-specific.
- A neutral item summary plus an idea-specific assessment is the right conceptual split.
- All 213 feeds are attached to at least one idea; there are no orphan feeds.
- 58 ideas have feeds, so discovery spans more than half the portfolio.
- 277 of 637 assessed items (43.5%) scored useful (4–5), showing that the pipeline sometimes finds high-value evidence.

### What is not demonstrating value

- 20,460 items were ingested, but only 659 were summarized and 637 assessed. Ingestion is outpacing attention by roughly 31:1.
- Only 33 items have a human interest rating and 29 have an information-value rating. This is too little ground truth to improve ranking reliably.
- No item has assessments for more than one idea. The global-summary/shared-item abstraction is technically sound, but its cross-idea reuse benefit has not materialized in production.
- Only 31 of 213 feeds are linked to more than one idea. Shared fetching has some operational value, but is not the dominant product value.
- Usefulness differs sharply by context: Project assessments average 3.62 with 219/264 high-value, while Research averages 1.93 with only 50/336 high-value. A single scoring policy is not adequate.
- Recent throughput repeats the pattern: in seven days, 8,732 items were ingested, 597 assessed, and only 15 received a human interest rating.

### Rewrite decision

Replace “Feeds” with “Sources and Evidence”:

1. Register sources globally and attach source subscriptions to intents/ideas.
2. Ingest metadata cheaply, but delay full content extraction and LLM summarization until a deterministic prefilter passes.
3. Rank candidates per idea/workflow before spending LLM tokens.
4. Show a small evidence queue with clear actions: useful, irrelevant, save to idea, create next action, or dismiss.
5. Treat these actions and downstream citations/actions as evaluation labels.
6. Measure precision at K, action/conversion rate, cost per accepted item, and time-to-use—not raw item volume.
7. Automatically throttle or pause low-yield subscriptions and test ranking/prompt variants per category.

## Current measurement gaps

Current records capture model labels, tokens, and a subjective 1–5 quality score for research. They do not reliably capture:

- exact prompt revision and rendered prompt;
- model/provider configuration and sampling parameters;
- input versus output tokens and cached/reasoning tokens;
- dollar cost;
- queue, provider, and end-to-end latency;
- tool calls and failures;
- structured output/schema validation;
- experiment assignment and comparison baseline;
- automated evaluator results and evaluator versions;
- human feedback tied to the run;
- downstream outcomes attributable to the run;
- retries, parent/child calls, and full traces;
- data lineage from sources through output to action.

These gaps are the central design constraint for the rewrite.
