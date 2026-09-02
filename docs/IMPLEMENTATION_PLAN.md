# IdeaFlow Measurement-First Implementation Plan

Status: Active — R4 deployed; R5 evaluation work is next
Original plan date: 2026-08-31
Status reconciled: 2026-09-01
Related documents: `FEATURE_INVENTORY.md`, `REWRITE_TECHSPEC.md`

## 1. Objective

Instrument the existing IdeaFlow application, establish a trustworthy production baseline, and then migrate functionality into the measurement-first architecture without interrupting current research, feed, graph, repeat, or podcast workflows.

The governing rule is:

> No new AI-generated state may become user-visible unless its producing execution can be identified and audited.

This plan deliberately puts observability and evaluation before broad feature restructuring. Category cleanup, feed redesign, and workflow rewrites should use the resulting measurements rather than assumptions.

## 1.1 Current production baseline

R4 is deployed. Releases R0–R4 established the measured-execution foundation
and moved the durable optional workflows through the Phase 4 vertical and
cutover work described in `PHASE4_VERTICALS_AND_CUTOVER.md`.

The implementation phase numbers and the original release labels did not remain
one-to-one. This status table is authoritative when the historical milestone
descriptions below differ from deployed reality.

| Capability | Current status | Remaining work |
| --- | --- | --- |
| LLM call-site inventory and metric registry | Shipped | Keep current as workflows are added or retired. |
| Execution ledger, protected payload storage, hashes, and provenance links | Shipped | Reconcile production completeness continuously. |
| Compatibility instrumentation for shell-driven workflows | Shipped | Retire per workflow only after an authoritative replacement is verified. |
| Scoped execution principals | Shipped | Complete rotation and least-privilege operational procedures. |
| Workflow cutover controls | Shipped | Record the production mode and rollback owner for every workflow. |
| Durable artifact/media versions, deterministic jobs, and outcome events | Shipped for the Phase 4 verticals | Extend consistently as remaining workflows move to authoritative mode. |
| Source/evidence pipeline | Shipped in its initial form | Measure yield and complete the bounded evidence-queue product rollout. |
| Gateway-based structured job execution | Partial | Continue one workflow at a time; compatibility wrappers remain supported. |
| Explicit human-feedback records and common feedback UI | Not complete | Deliver with the R5 evaluator foundation. |
| Versioned evaluator and metric models | Not complete | Deliver in R5 before using model judgments for promotion decisions. |
| Immutable evaluation datasets and paired offline runner | Not complete | Deliver in R5. |
| Controlled online experimentation | Not started | Begin only after the R5 offline gate passes. |
| Portfolio taxonomy and lifecycle migration | Planned | Keep outside the research-context experiment. |

R4 production acceptance is maintained through a reconciliation report that
records, by workflow: trace completeness, projection attribution, token/cost/
timing coverage, unavailable-reason coverage, current `WorkflowCutover` mode,
payload-storage health, unattributed legacy writes, and the tested rollback
path. A production capability is marked shipped only when that evidence exists;
deploying schema or code alone is not sufficient.

## 2. Delivery strategy

Use incremental changes to the existing Django/PostgreSQL application rather than a single replacement launch.

- Keep the current `ideas` models and APIs operational during instrumentation.
- Add execution records alongside current entities and link them with nullable foreign keys.
- Wrap current shell/CLI calls before replacing them with direct provider adapters.
- Dual-write only where necessary and reconcile continuously.
- Release behind per-workflow feature flags.
- Prefer additive, reversible migrations until the final cutover.

Phase 4 implementation and rollout details are in
[PHASE4_VERTICALS_AND_CUTOVER.md](PHASE4_VERTICALS_AND_CUTOVER.md).
- Never backfill invented precision. Legacy measurements must be labeled as imported or estimated.

## 3. Workstreams

The implementation is divided into eight workstreams:

1. Execution ledger and provenance
2. Provider gateway and workflow integration
3. Feedback, evaluation, and outcomes
4. Experimentation
5. Sources and evidence redesign
6. Portfolio taxonomy and lifecycle cleanup
7. Operations, security, and cost controls
8. Migration and retirement

Workstreams 1–3 form the critical path. The others should not change production decision-making until trace completeness is proven.

## 4. Milestone 0 — Baseline and design freeze (shipped)

Goal: make current behavior and expected telemetry explicit before changing runtime paths.

### Tasks

- Inventory every location that invokes or launches an LLM:
  - `research_idea.sh`
  - `research_all.sh` and `research_all_codex.sh`
  - `score_items.sh` and `score_items_all.sh`
  - `weekly_summary.sh`
  - `tools/review_relationships.py`
  - `tools/extract_open_questions_remote.py`
  - semantic graph processing
  - persona council execution
  - podcast script generation
  - any Codex/Claude execution launched by repository workflows
- Assign a stable workflow key and purpose to every call site.
- Define the initial metric registry and workflow outcome vocabulary.
- Document which providers expose token breakdowns, request IDs, first-token timing, and billed cost.
- Establish production baselines from existing records:
  - run volume by workflow and model;
  - existing `ResearchEntry.tokens_used` totals;
  - feed funnel counts;
  - relation acceptance/rejection;
  - repeat-result status conversion;
  - podcast regeneration and publication.
- Decide raw prompt/response retention and redaction policy.
- Add feature-flag configuration for instrumentation, gateway routing, projection, feedback, and experiments.

### Deliverables

- LLM call-site register with owner, workflow key, and migration status.
- Metric registry version 1.
- Data-retention and redaction decision record.
- Baseline report stored with an immutable generation timestamp.

### Exit criteria

- Every known call site has a stable workflow key.
- Each workflow has one proposed primary outcome and explicit guardrails.
- No secrets or credentials are included in telemetry payloads.

## 5. Milestone 1 — Execution ledger (shipped)

Goal: add the minimum schema required to identify and reconstruct every execution.

### Django models

Create a new `executions` Django app rather than continuing to expand `ideas/models.py`.

#### `WorkflowDefinition`

- `key`, `name`, `description`, `is_active`
- Unique stable key such as `research`, `feed_score`, or `relationship_vote`.

#### `WorkflowVersion`

- `workflow`, `version`, `status`
- `configuration` JSON, `content_hash`
- `created_by`, `approved_by`, timestamps
- Unique `(workflow, version)`.
- Immutable after first execution.

Initially, each existing workflow receives a version representing current production behavior. This version may reference several existing `PromptRevision` records.

#### `ModelConfiguration`

- provider, exact model identifier, capability label
- normalized settings and provider-specific settings
- pricing-version reference
- active dates and immutable content hash

#### `ExecutionTrace`

- UUID primary key
- workflow version
- subject content type and object ID
- trigger: human, schedule, API, dependent workflow, migration
- actor user or service principal
- status and queued/started/completed timestamps
- experiment metadata, initially null
- correlation and idempotency keys

#### `LLMRun`

- UUID primary key, trace, optional parent run
- purpose and attempt number
- model configuration
- prompt revision manifest
- rendered-input storage reference and SHA-256 hash
- context manifest JSON
- output storage reference/hash and parsed output JSON
- status, finish reason, schema-valid state
- provider request ID
- token fields: input, output, cached, reasoning, total
- cost in integer micros, currency, and pricing source
- queued, started, first-token, completed timestamps
- error class/code and redacted detail
- `measurement_status` and unavailable-reasons JSON

#### `ToolInvocation`

- run, tool name/version, mutating flag
- request/response references and hashes
- status, timing, error, idempotency key
- affected object references

#### `ExecutionEvent`

- trace, optional run, sequence, event type, timestamp, payload
- Append-only; unique `(trace, sequence)`.

#### `PricingVersion`

- provider, model identifier, effective dates
- input/output/cached/reasoning prices
- source and currency
- Immutable after use.

### Links added to current models

Add nullable `produced_by_run` or equivalent provenance fields to:

- `ResearchEntry`
- `Artifact`
- `WeeklySummary`
- `FeedItem.summary` through a separate summary-version record where practical
- `FeedItemAssessment`
- `IdeaRelationSuggestion`
- `PersonaReview` and council votes
- `RelationshipCouncilReview` and votes
- `RepeatResult`
- `Episode` script generation

Where a current row can have multiple generated versions, create an immutable version table rather than placing a single mutable foreign key on the row.

### Storage

- Create a storage interface for prompt, response, tool, and artifact payloads.
- Support local protected filesystem storage first, with an object-storage implementation behind the same interface.
- Store object keys and hashes in PostgreSQL, not public URLs.
- Reuse the authenticated artifact-access pattern; raw execution payloads require a stricter operator permission.

### Services

- `start_trace(...)`
- `start_run(...)`
- `record_run_usage(...)`
- `complete_run(...)`
- `fail_run(...)`
- `record_tool_invocation(...)`
- `attach_projection(...)`

All terminal operations must be idempotent and transactionally safe.

### Tests

- Model immutability and constraints.
- Stable content hashing.
- Idempotent trace/run creation.
- Retry representation as separate attempts.
- Append-only event behavior and sequence conflicts.
- Cost calculation using effective pricing versions.
- Storage authorization and missing-object behavior.
- Deletion behavior: business objects may be deleted without erasing the audit ledger.

### Exit criteria

- A synthetic trace can be reconstructed from request through output and projection.
- Retries remain distinguishable.
- No successful run can reach terminal state without its frozen configuration, timing, and measurement status.

## 6. Milestone 2 — Compatibility instrumentation (shipped)

Goal: instrument current shell-driven workflows without changing their results or scheduling.

### API additions

Add scoped machine endpoints:

- `POST /api/v1/execution-traces/`
- `POST /api/v1/execution-traces/<id>/runs/`
- `POST /api/v1/runs/<id>/events/`
- `POST /api/v1/runs/<id>/complete/`
- `POST /api/v1/runs/<id>/fail/`
- `POST /api/v1/runs/<id>/tool-invocations/`

Every endpoint accepts an idempotency key. Do not use the existing unrestricted `IDEAFLOW_API_TOKEN` for long-term worker authentication; introduce scoped service principals, while allowing a temporary compatibility mode during rollout.

### CLI additions

Extend `tools/ideaflow` with:

- `trace-start`
- `run-start`
- `run-event`
- `run-complete`
- `run-fail`
- `tool-start` and `tool-complete`

Commands should accept JSON through files or standard input so prompts and responses are never exposed in process arguments.

### Shell workflow wrapper

Add a shared wrapper used by all shell workflows:

- Creates the trace and run before launching Claude or Codex.
- Records the exact approved prompt revision manifest.
- Writes rendered prompts and raw output to permission-restricted temporary files.
- Measures wall-clock timing and exit status.
- Extracts provider usage when available.
- Uploads payloads, completes or fails the run, and removes temporary files.
- Exports `IDEAFLOW_TRACE_ID` and `IDEAFLOW_RUN_ID` so later `log-effort`, feed-scoring, graph, and podcast API calls attach their projections.

Instrumentation failure policy:

- Before enforcement, an instrumentation outage warns and permits the legacy workflow.
- After trace completeness reaches the launch threshold, durable workflows fail closed before execution if a trace cannot be created.
- Completion-reporting outages retain a local retry record and must not rerun the LLM solely to reconstruct telemetry.

### Initial integration order

1. Feed scoring: high volume, low mutation risk, measurable labels.
2. Research/review: highest-value user-facing output.
3. Relationship council and semantic graph.
4. Repeat discovery and summary generation.
5. Weekly summary.
6. Podcast script generation.
7. Execute and critique workflows.

### Tests

- Shell tests with fake Claude/Codex executables.
- Success, non-zero exit, timeout, malformed output, and reporting-outage paths.
- Prompt files and raw output never appear in command-line logs.
- Existing workflow output and mutation behavior remains unchanged.
- Projection API rejects mismatched run subject or workflow.

### Exit criteria

- At least 99.5% of successful calls contain provider, model, timing, token facts or explicit unavailable reasons, and producing projection ID.
- Existing task-selection and scheduling behavior remains unchanged.
- Totals reconcile with current `ResearchEntry.tokens_used` reporting and available provider usage.

## 7. Milestone 3 — Gateway and structured workflow execution (partial)

Goal: centralize execution semantics while retaining provider-specific adapters.

### Components

- `LLMGateway` interface with normalized request/response objects.
- Adapters for current Claude CLI, Codex CLI, and direct API calls already used by IdeaFlow.
- Context builder that returns both rendered context and a source manifest.
- Response parser and JSON-schema validation service.
- Job model using lease, heartbeat, retry, and dead-letter semantics modeled after `EpisodeRun`.
- Transactional outbox for job publication.

### Migration approach

- Move one workflow at a time from shell orchestration into a Django management command or worker task using the gateway.
- Retain shell entry points as thin compatibility clients during transition.
- For each workflow, run shadow comparisons before making the gateway result authoritative.
- Freeze the exact workflow version before enqueueing.

### Required behavior

- Provider calls never execute in web requests.
- A retry creates a new attempt under the existing trace.
- A model-graded evaluator is a child `LLMRun`, never an unmeasured helper call.
- Structured output is not projected until schema and workflow terminal checks pass.
- Timeouts and cancellations remain queryable terminal records.

### Exit criteria

- Research and feed scoring run through the gateway in production.
- Provider adapters pass shared contract tests.
- Worker crashes and expired leases do not create duplicate projections.

## 8. Milestone 4 — Feedback, outcomes, and evaluations (partially shipped)

Goal: measure usefulness rather than only activity, tokens, and self-reported quality.

R4 shipped `OutcomeEvent` support and outcome attribution for the Phase 4
vertical workflows. The generic human-feedback and versioned-evaluator models
listed below are not part of the current measured-execution schema and move
forward as required R5 work. They must not be treated as deployed merely
because related workflow-specific accept/reject actions already exist.

### Models

#### `HumanFeedback`

- target run and optional projection
- action: accept, edit, reject, useful, irrelevant, save, cite, action, dismiss
- optional rating/reason
- before/after hashes for edits
- actor, exposed_at, feedback_at

#### `OutcomeEvent`

- workspace, idea, event type, value, occurred_at
- producing/attributed run where known
- attribution method and confidence
- idempotency key

#### `EvaluatorDefinition`, `EvaluatorVersion`, `EvaluationResult`

- Deterministic, model-graded, human, and outcome evaluator types.
- Immutable evaluator versions and rubrics.
- Explicit metric direction, range, and applicability.

#### `MetricDefinition`

- stable key, description, unit, direction, aggregation, window, version

### User-interface changes

- Add accept, edit, and reject actions to research and summary outputs.
- Add useful, irrelevant, save, cite, create-action, and dismiss controls to evidence cards.
- Record exposure when a result is actually rendered to a user.
- Add relation-accept/reject and repeat-result actions to the common outcome-event path.
- Show feedback status on the run inspector.

### Deterministic evaluators

Implement before model graders:

- Response schema validity.
- Required-field and terminal-condition checks.
- Citation URL and referenced-object validity.
- Duplicate-result detection.
- Podcast duration/script structural checks.
- Relationship constraint and graph-cycle checks.
- Empty, oversized, or obviously truncated output detection.

### Model graders

- Start only where no deterministic or behavioral label is sufficient.
- Use blinded inputs and exclude treatment identity.
- Record grader prompts, configurations, tokens, cost, and outputs as child runs.
- Calibrate against human-labeled dataset cases before use in experiments.

### Exit criteria

- Non-exposure, no feedback, negative feedback, and positive feedback are distinct states.
- Each core workflow emits its defined outcome events.
- At least one deterministic evaluator operates for every structured workflow.
- Evaluator costs appear in total workflow cost.

## 9. Milestone 5 — Evaluator foundation, research checkpoints, and offline comparison

Goal: complete the generic evaluation foundation and safely compare candidate
changes before exposing them to production users.

This is the next production milestone after R4. It includes the unfinished
generic evaluator work from Milestone 4; offline experiments may not use
ad-hoc, unversioned judge prompts or write scores into an existing field whose
meaning differs from the metric being measured.

### Models and services

- `EvaluationDataset`
- `DatasetCase`
- `DatasetSnapshot`
- `OfflineEvaluationRun`

Also implement the Milestone 4 records required by the runner:

- `MetricDefinition`
- `EvaluatorDefinition`
- `EvaluatorVersion`
- `EvaluationResult`

Every model-graded evaluation is a child `LLMRun`. Its evaluator version,
rubric, model configuration, rendered input, output, tokens, cost, and treatment
blinding are retained like any other measured execution.

### Research checkpoints

Add an immutable `ResearchCheckpoint` rather than using mutable
`Idea.exec_summary` as an experimental input:

- idea and producing run;
- schema version and structured state;
- source manifest and evidence cutoff timestamp;
- content hash and validation status;
- creation timestamp.

The structured state contains the scoped research objective, supported
conclusions, evidence references, rejected approaches and reasons,
contradictions, unresolved questions, current decision, and recommended next
action. `Idea.exec_summary` remains the concise human-readable projection of
the latest state; it is not the experiment's source of truth.

Define context selection as immutable workflow configuration. The initial
policies are:

- `full_history_v1`: the current complete research context;
- `checkpoint_delta_v1`: the latest validated checkpoint plus changes after its
  evidence cutoff and explicitly targeted retrieval.

For either policy, `LLMRun.context_manifest` records the policy version,
checkpoint ID/hash when applicable, included research entries, artifacts and
evidence, delta boundary, token count by context section, retrievals, and any
fallback reason.

Dataset cases reference immutable/redacted input snapshots, expected properties, prior human outcomes, and cohort metadata.

### Initial datasets

- Research reports accepted, edited, and rejected.
- Feed candidates marked useful or irrelevant, stratified by idea category.
- Relationship suggestions accepted and rejected.
- Repeat results actioned and dismissed.
- Podcast scripts approved and regenerated.
- Scheduled research continuations with short, long, conflicting, stale, and
  multi-hop histories.

### Tooling

- Admin/API action to sample eligible production cases.
- Redaction preview and approval.
- Offline runner that executes control and candidate on the identical snapshot.
- Blinded comparison UI.
- Report covering quality, cost, latency, validation, and cohort differences.

### First research context evaluation

Compare `full_history_v1` with `checkpoint_delta_v1` on identical immutable
snapshots. Each case freezes the idea state, explicit research objective,
available evidence, model configuration, tools, output limit, and workflow
version. Generate both answers independently; neither answer sees the other.

Evaluate each answer on a common 1–5 progress scale, using an immutable rubric
selected for the idea type. The shared anchors make scores aggregatable while
the type-specific rubric defines what evidence, progress, and completion mean
for that kind of idea.

The initial research rubric is versioned as `research.answer_progress`:

| Score | Definition |
| --- | --- |
| 1 — No progress | Repeats known information, misses the objective, or adds no supported conclusion. |
| 2 — Minor progress | Adds a potentially useful observation but does not close a meaningful gap or change a decision. |
| 3 — Material progress | Resolves part of the objective or materially narrows the alternatives with usable evidence. |
| 4 — Nearly answered | Supports a defensible decision with only a small, explicitly identified uncertainty remaining. |
| 5 — Completely answered | Fully answers the scoped objective with sufficient evidence, addresses material counterarguments, and leaves no decision-relevant gap. |

Do not reuse `ResearchEntry.quality`: it measures confidence in an effort, not
progress toward answering the objective.

### Type-specific scoring rubrics

Each `EvaluatorVersion` declares its applicable idea type, metric key, rubric
version, required evidence, non-applicable conditions, and completion criteria.
The initial family is:

| Idea type | Metric | What a 5 requires |
| --- | --- | --- |
| Research | `research.answer_progress` | The scoped question is completely answered with sufficient evidence and no decision-relevant gap. |
| Product | `product.validation_progress` | The named product assumption or decision is resolved with relevant user, market, feasibility, or experiment evidence and a defensible product decision. |
| Project | `project.delivery_progress` | The scoped deliverable meets its acceptance criteria, is verified, and has no unresolved blocker within scope. |
| Content | `content.completion_progress` | The scoped content outcome is complete for its audience and format, factually supported where applicable, and ready for its defined review or publication gate. |

Every type retains the shared ordinal anchors: 1 means no progress, 2 minor
progress, 3 material progress, 4 nearly complete, and 5 complete for the scoped
objective. Rubrics may add dimensions and evidence requirements but may not
reverse or redefine those anchors. Cross-type portfolio reporting may compare
the normalized 1–5 progress distribution, but must also show results by rubric;
a product 4 and research 4 are not assumed to represent identical work.

Until `Idea.type` becomes authoritative in Milestone 8, dataset construction
and experiment enrollment must store an explicit `rubric_key`. It may come
from an approved temporary category-to-rubric mapping or a human selection,
but never from an evaluator's silent inference. Ambiguous cases are excluded or
queued for classification. Once taxonomy migration is complete, new cases use
the frozen idea type at snapshot/enqueue time while historical cases retain
their original rubric assignment.

Use at least three independent, blinded evaluation roles: evidence auditor,
type-specific progress evaluator, and skeptic. Each receives the same frozen
rubric version and records a 1–5 progress score, rationale, and evidence
references before aggregation. Randomize answer order, reverse the order for a
prespecified sample, aggregate with the median, preserve all votes, and send
cases with a score range greater than two points to human review.

Measure deterministic citation validity, source coverage, contradiction with
validated prior facts, duplicate claims, schema validity, and unsupported
claims alongside the council score. The comparison report includes paired
score differences, treatment wins/ties/losses, score distribution, evaluator
agreement, missing results, input/output tokens, total generation and grader
cost, latency, tool calls, fallback rate, and cohorts by history length and
evidence complexity.

### Exit criteria

- Dataset snapshots cannot change after use.
- Control runs are reproducible within provider limitations.
- Comparison reports include evaluator disagreement and missing-result rates.
- Every type-specific progress rubric and council evaluator used in a decision
  is immutable and calibrated against a human-scored seed set for that rubric;
  calibration from one idea type cannot be assumed to transfer to another.
- Research checkpoints are reproducible from their source manifests and never
  silently change after use.
- `checkpoint_delta_v1` is eligible for scheduled shadow testing only if its
  mean paired progress difference is within a prespecified 0.25-point
  non-inferiority margin, its 4–5 score rate is no more than five percentage
  points below control, contradiction and unsupported-claim rates do not
  increase materially, and input tokens fall by at least 40%.

## 10. Milestone 6 — Controlled experimentation

Goal: conduct safe, reproducible A/B tests on production workflows.

### Models

- `Experiment`
- `ExperimentVariant`
- `ExperimentAssignment`
- `ExperimentObservation`
- `ExperimentDecision`

### Assignment service

- Deterministic salted hashing.
- Default unit: idea ID.
- Optional unit: source-item ID for evidence-ranking experiments.
- Sticky persisted assignment.
- Namespace conflict detection.
- Allocation changes affect only newly assigned units unless explicitly designed otherwise.

### Guardrails

- Completion and schema-valid rates.
- Cost and latency ceilings.
- Safety/policy failures.
- Duplicate or invalid mutation rate.
- Sample-ratio mismatch.

Guardrail breaches automatically pause enrollment and alert an operator; they do not promote or roll back workflow definitions automatically.

### Analysis

- Require hypothesis, primary metric, allocation, population, minimum sample size, minimum detectable effect, maximum duration, and analysis method before activation.
- Store raw observations and versioned analysis snapshots.
- Report effect size and uncertainty, cohort breakdown, missing outcomes, retries, and crossovers.
- Promotion requires approval and creates a new `WorkflowVersion`.

### First online experiment

Use feed/evidence ranking because it provides relatively high volume and low-risk decisions.

- Randomization unit: source item within an eligible idea cohort.
- Control: current feed-scoring prompt/configuration.
- Treatment: category-specific ranking prompt or deterministic prefilter plus LLM scoring.
- Primary metric: precision@5 from useful/save/action feedback.
- Secondary: cost per accepted item and time to feedback.
- Guardrails: invalid-output rate, latency, total daily cost, and exposure imbalance.

### Scheduled research context experiment

After the Milestone 5 offline gate passes, run a separate scheduled shadow
experiment for research continuations:

- Control: `full_history_v1`; it remains the only authoritative writer.
- Treatment: `checkpoint_delta_v1`; it is read-only during shadow operation.
- Randomization unit: idea ID, with sticky assignment within an experiment.
- Enrollment unit: one scheduled research objective frozen when the job is
  enqueued.
- Primary metric: median `research.answer_progress` and the paired treatment
  minus control difference.
- Secondary metrics: 4–5 rate, input-token reduction, total cost including
  graders, latency, and useful novelty.
- Guardrails: contradiction rate, unsupported claims, invalid citations,
  missing output, evaluator disagreement, fallback rate, and duplicate
  mutations.

Freeze the idea type or temporary rubric assignment, rubric version, research
objective, checkpoint ID/hash, evidence cutoff, delta boundary, context policy,
workflow version, model configuration, and experiment assignment at enqueue
time. New evidence arriving after enqueue belongs to a later job; it must not
make the paired inputs diverge.

All candidate and evaluator runs share one trace tree but cannot read one
another's hidden treatment identity. A missing, stale, invalid, or disputed
checkpoint routes the treatment to full context and records the fallback. The
shadow run does not create a `ResearchEntry`, alter an idea, or advance the
schedule. Exactly one authoritative completion advances the schedule.

After a successful shadow gate, a limited-authority experiment may allow the
treatment to become the single writer for a small eligible cohort. Exclude
ideas without a validated checkpoint, with unresolved source contradictions,
with material human edits after the checkpoint, or whose scheduled action is
irreversible. A guardrail breach pauses new treatment enrollment; it does not
rewrite history or automatically promote/roll back a workflow version.

### Exit criteria

- Assignment is stable across retries and repeat views.
- One shadow and one limited online experiment complete end to end.
- A decision can promote a winner through the existing approval model without rewriting history.
- Scheduled research never advances its clock twice, and no shadow candidate
  creates a user-visible projection.
- Promotion of `checkpoint_delta_v1` requires the prespecified progress,
  correctness, cost, and cohort gates; token savings alone cannot win.

## 11. Milestone 7 — Sources and evidence redesign

Goal: replace unbounded feed processing with a measured evidence funnel.

### Data changes

- Rename the product concept from feeds to sources while initially retaining current tables.
- Introduce `Subscription`, `EvidenceCandidate`, and `EvidenceAction`.
- Treat `Feed` and `FeedItem` as RSS-specific implementations of `Source` and `SourceItem`.
- Preserve global deduplication and SSRF protections.

### Funnel

1. Fetch source metadata and deduplicate items.
2. Apply deterministic recency, language, domain, and keyword filters.
3. Create per-idea candidates only for passing items.
4. Rank candidates before LLM spending.
5. Summarize/assess only the bounded top candidates.
6. Expose a small ranked queue.
7. Record explicit exposure and action.
8. Auto-throttle subscriptions with poor measured yield.

### Migration

- Import existing feeds as sources and idea-feed links as subscriptions.
- Import existing feed assessments and ratings as legacy-labeled feedback.
- Do not process the historical 20,460-item backlog.
- Retain current feed UI read-only until evidence-queue parity is confirmed.

### Exit criteria

- Daily ingestion cannot create an unbounded LLM queue.
- Dashboard reports precision@K, exposure, acceptance, cost per accepted item, and backlog age.
- Category-specific performance is visible.
- A subscription can be throttled or paused based on measured yield.

## 12. Milestone 8 — Portfolio taxonomy and lifecycle

Goal: simplify the user model after instrumentation provides a migration baseline.

### Changes

- Add `Idea.type`: research, product, project, content.
- Add unified lifecycle: inbox, exploring, active, blocked, completed, archived.
- Add independent priority: none, focus, high, normal, low.
- Add tags and goals.
- Represent app improvements as child work linked to a product/project.
- Preserve legacy category, status, and stage in migration metadata.

### Migration mapping

- Research + Research Effort → Research.
- App + Side Project → Product.
- Project → Project.
- Book + Podcast → Content with format tag.
- App Improvement → child improvement; flag unlinked records for review.
- Passive Income → infer type only where evidence exists; otherwise manual review, plus goal tag.
- Focus Project → priority `focus`; currently unused.
- News → no default mapping; currently unused.

Because production has no Current ideas and 54 of 102 ideas have no stage, generate a review report instead of silently manufacturing lifecycle precision.

### Exit criteria

- All ideas migrate with preserved legacy values.
- Ambiguous mappings are visible in a review queue.
- Existing routes redirect without breaking saved links.
- Workflow eligibility uses the new lifecycle consistently.

## 13. Milestone 9 — Operations, security, and cost controls

Goal: make the measured system safe and operable in production.

### Tasks

- Scoped service principals with rotation and revocation.
- Per-workspace, workflow, provider, and model budgets.
- Concurrency and rate limiting.
- Queue depth, lease expiry, provider failure, and cost alerts.
- Nightly reconciliation of projections, usage, pricing, storage objects, and experiment observations.
- Trace-aware structured logging without raw prompts by default.
- PostgreSQL and payload-storage backup/restore test.
- Retention enforcement and legal/privacy deletion workflow.
- Operator runbooks for stuck jobs, provider outages, cost anomalies, experiment guardrails, and incomplete telemetry.

### Exit criteria

- Budget exhaustion prevents new spending without corrupting queued work.
- Revoked credentials cannot create or complete runs.
- Restore testing reconstructs traces and protected payload references.
- Alerts identify the affected workflow and trace population.

## 14. Milestone 10 — Optional workflow migrations

After the core system is stable, migrate:

- Weekly summaries to derived analytics over execution/outcome events.
- Relationship and persona councils as trace trees with independent votes.
- Repository execution and critique with mutating tool audit.
- Podcast script generation and TTS/media jobs under a shared trace.
- Public content publishing with explicit approval outcomes.

Do not classify deterministic feed fetches, media rendering, or repository commands as LLM runs. Record them as jobs/tool invocations under the same trace so cost and latency remain attributable without corrupting LLM metrics.

## 15. Release sequence

| Release | Production behavior | Rollback |
| --- | --- | --- |
| R0–R4 (deployed) | Execution ledger, compatibility instrumentation, initial source pipeline, Phase 4 vertical provenance, outcome events, and reversible workflow cutovers | Use the recorded per-workflow cutover mode; retain audit history |
| R4.1 | Reconcile production trace completeness, attribution, measurements, payload health, cutover modes, and rollback tests | No behavior change; correct records and configuration without rerunning LLM work |
| R5A | Generic metrics/evaluators/results, human-feedback foundation, and immutable dataset snapshots | Disable evaluator and feedback projection flags; retain records |
| R5B | Immutable research checkpoints and versioned full-history/checkpoint-delta context policies | Disable checkpoint construction/use; retain checkpoints for audit |
| R5C | Offline paired research-context evaluation with blinded council scoring | Pause offline runners; no production projections are affected |
| R6A | Limited online feed-ranking experiment | Pause enrollment; control remains active |
| R6B | Scheduled research context shadow experiment; full context remains authoritative | Disable shadow execution; scheduling and control writes continue unchanged |
| R7 | Limited checkpoint-context authority for an eligible idea cohort | Pause enrollment and route all new work to `full_history_v1` |
| R8 | Evidence-queue completion and broader context-policy rollout if approved | Restore previous read path and context workflow version |
| R9 | New taxonomy/lifecycle becomes authoritative | Use compatibility mapping/read adapter |
| R10 | Remaining workflows migrated | Per-workflow rollback flags |

Database rollback should normally mean disabling new writers and restoring prior read paths, not deleting execution history.

## 16. Testing strategy

### Unit tests

- Model invariants, assignment hashing, pricing, metrics, redaction, schema validation, and attribution.
- Research-checkpoint immutability, source-manifest hashing, cutoff handling,
  context-policy rendering, stale-checkpoint fallback, rubric applicability,
  migration mapping, and 1–5 progress-score validation.

### Contract tests

- Provider adapters using recorded sanitized responses.
- CLI/API compatibility and idempotency.
- Storage implementations.

### Integration tests

- Job lifecycle, retry, lease expiry, projection, evaluator child runs, feedback, and observations.
- Current shell workflows with fake provider binaries.
- Paired context runs share the frozen case but not generated answers or hidden
  treatment labels.
- Shadow treatment cannot create a research entry, change an idea, or advance a
  repeat/research schedule.
- The authoritative completion advances a schedule exactly once across retries.
- Council votes remain independent, retain order-randomization metadata, and
  escalate when the score range exceeds two points.

### Migration tests

- Snapshot representative legacy records.
- Verify counts, legacy IDs, hashes, relationships, and ambiguous mapping reports.
- Explicitly test null stages, archived ideas, soft-deleted repeat results, current prompt revisions, and published episodes.

### End-to-end tests

- Human-triggered research through feedback and next-action outcome.
- Scheduled feed item through ranking, exposure, and useful/irrelevant action.
- Relationship suggestion through council and human decision.
- Experiment assignment through observation and approved promotion.
- Frozen scheduled research case through full-context and checkpoint-delta
  generation, blinded council scoring, paired analysis, and a non-mutating
  shadow decision.
- Limited-authority checkpoint run through projection attribution and immediate
  feature-flag fallback to full context.

### Production verification

- Shadow telemetry comparison.
- Provider usage and invoice reconciliation.
- Canary workflow cohort.
- Synthetic scheduled job and stuck-lease alert.
- Sampled reconstruction of checkpoint inputs from stored manifests and hashes.
- Sample-ratio, treatment leakage, order-bias, evaluator disagreement, fallback,
  and double-schedule-advance monitoring for the research experiment.

## 17. Definition of done

The program is complete when:

- Every new user-visible AI output has an immutable producing run.
- At least 99.5% of successful LLM calls have complete core telemetry or explicit unavailable reasons.
- Workflow cost includes generation, evaluator, and tool costs.
- Prompt, model, context, workflow, and evaluator versions used by a run are reproducible.
- Users can provide explicit feedback, and outcome attribution works across the primary workflows.
- Offline evaluation and controlled online experiments operate with stable assignment and guardrails.
- Scheduled research can use a reproducible checkpoint-plus-delta context policy
  without losing progress quality, increasing factual guardrail failures, or
  advancing a job twice.
- The feed backlog has been replaced by a bounded, measurable evidence funnel.
- Category and lifecycle migration is reconciled and reversible during the support window.
- Security, backup/restore, budget, and incident runbooks have been exercised.
- The previous execution paths can be retired without losing historical auditability.

## 18. Recommended next development slice after R4

The next mergeable slice should complete measurement prerequisites without
changing authoritative research behavior:

1. Produce the R4.1 reconciliation report and resolve any unattributed or
   incomplete production workflow population.
2. Add immutable `MetricDefinition`, `EvaluatorDefinition`,
   `EvaluatorVersion`, and `EvaluationResult` records.
3. Implement the shared 1–5 anchors, the initial type-specific rubric family,
   explicit rubric applicability, and deterministic score validation; start
   with `research.answer_progress` for the context experiment.
4. Add `ResearchCheckpoint`, structured-state validation, source manifests,
   evidence cutoffs, and stable content hashes.
5. Add `full_history_v1` and `checkpoint_delta_v1` to the context builder and
   record their complete manifests without changing production selection.
6. Build a small human-scored seed dataset spanning short, long, conflicting,
   stale, and multi-hop research histories, with an explicit frozen rubric key
   on every case.
7. Run paired offline generation and three independent blinded evaluator roles,
   including answer-order reversal for a prespecified sample.
8. Publish the paired quality/cost report and make an explicit proceed, revise,
   or stop decision against the R5 exit thresholds.

Only after this slice passes should scheduled shadow execution be enabled. The
first shadow release keeps full context authoritative, makes the treatment
strictly read-only, and proves that scheduling advances exactly once.
