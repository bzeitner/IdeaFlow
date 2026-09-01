# IdeaFlow Measurement-First Implementation Plan

Status: Proposed
Date: 2026-08-31
Related documents: `FEATURE_INVENTORY.md`, `REWRITE_TECHSPEC.md`

## 1. Objective

Instrument the existing IdeaFlow application, establish a trustworthy production baseline, and then migrate functionality into the measurement-first architecture without interrupting current research, feed, graph, repeat, or podcast workflows.

The governing rule is:

> No new AI-generated state may become user-visible unless its producing execution can be identified and audited.

This plan deliberately puts observability and evaluation before broad feature restructuring. Category cleanup, feed redesign, and workflow rewrites should use the resulting measurements rather than assumptions.

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

## 4. Milestone 0 — Baseline and design freeze

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

## 5. Milestone 1 — Execution ledger

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

## 6. Milestone 2 — Compatibility instrumentation

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

## 7. Milestone 3 — Gateway and structured workflow execution

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

## 8. Milestone 4 — Feedback, outcomes, and evaluations

Goal: measure usefulness rather than only activity, tokens, and self-reported quality.

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

## 9. Milestone 5 — Evaluation datasets and offline comparison

Goal: safely compare candidate changes before exposing them to production users.

### Models and services

- `EvaluationDataset`
- `DatasetCase`
- `DatasetSnapshot`
- `OfflineEvaluationRun`

Dataset cases reference immutable/redacted input snapshots, expected properties, prior human outcomes, and cohort metadata.

### Initial datasets

- Research reports accepted, edited, and rejected.
- Feed candidates marked useful or irrelevant, stratified by idea category.
- Relationship suggestions accepted and rejected.
- Repeat results actioned and dismissed.
- Podcast scripts approved and regenerated.

### Tooling

- Admin/API action to sample eligible production cases.
- Redaction preview and approval.
- Offline runner that executes control and candidate on the identical snapshot.
- Blinded comparison UI.
- Report covering quality, cost, latency, validation, and cohort differences.

### Exit criteria

- Dataset snapshots cannot change after use.
- Control runs are reproducible within provider limitations.
- Comparison reports include evaluator disagreement and missing-result rates.

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

### Exit criteria

- Assignment is stable across retries and repeat views.
- One shadow and one limited online experiment complete end to end.
- A decision can promote a winner through the existing approval model without rewriting history.

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
| R0 | Schema only; instrumentation disabled | Reverse additive migration if unused |
| R1 | Shadow trace creation for feed scoring | Disable feature flag |
| R2 | Trace all shell workflows; legacy outputs authoritative | Disable wrapper; retain records |
| R3 | Gateway authoritative for feed scoring and research | Route those workflows back to compatibility wrapper |
| R4 | Feedback UI and deterministic evaluators | Hide controls; events remain valid |
| R5 | Offline evaluation and shadow experiments | Pause runners |
| R6 | Limited online feed-ranking experiment | Pause enrollment; control remains active |
| R7 | Evidence queue replaces feed inbox | Restore old UI read path |
| R8 | New taxonomy/lifecycle becomes authoritative | Use compatibility mapping/read adapter |
| R9 | Remaining workflows migrated | Per-workflow rollback flags |

Database rollback should normally mean disabling new writers and restoring prior read paths, not deleting execution history.

## 16. Testing strategy

### Unit tests

- Model invariants, assignment hashing, pricing, metrics, redaction, schema validation, and attribution.

### Contract tests

- Provider adapters using recorded sanitized responses.
- CLI/API compatibility and idempotency.
- Storage implementations.

### Integration tests

- Job lifecycle, retry, lease expiry, projection, evaluator child runs, feedback, and observations.
- Current shell workflows with fake provider binaries.

### Migration tests

- Snapshot representative legacy records.
- Verify counts, legacy IDs, hashes, relationships, and ambiguous mapping reports.
- Explicitly test null stages, archived ideas, soft-deleted repeat results, current prompt revisions, and published episodes.

### End-to-end tests

- Human-triggered research through feedback and next-action outcome.
- Scheduled feed item through ranking, exposure, and useful/irrelevant action.
- Relationship suggestion through council and human decision.
- Experiment assignment through observation and approved promotion.

### Production verification

- Shadow telemetry comparison.
- Provider usage and invoice reconciliation.
- Canary workflow cohort.
- Synthetic scheduled job and stuck-lease alert.

## 17. Definition of done

The program is complete when:

- Every new user-visible AI output has an immutable producing run.
- At least 99.5% of successful LLM calls have complete core telemetry or explicit unavailable reasons.
- Workflow cost includes generation, evaluator, and tool costs.
- Prompt, model, context, workflow, and evaluator versions used by a run are reproducible.
- Users can provide explicit feedback, and outcome attribution works across the primary workflows.
- Offline evaluation and controlled online experiments operate with stable assignment and guardrails.
- The feed backlog has been replaced by a bounded, measurable evidence funnel.
- Category and lifecycle migration is reconciled and reversible during the support window.
- Security, backup/restore, budget, and incident runbooks have been exercised.
- The previous execution paths can be retired without losing historical auditability.

## 18. Recommended first development slice

The first mergeable slice should be deliberately narrow:

1. Create the `executions` app.
2. Add `WorkflowDefinition`, `WorkflowVersion`, `ExecutionTrace`, `ModelConfiguration`, `LLMRun`, `ExecutionEvent`, and `PricingVersion`.
3. Add protected local payload storage and hashing.
4. Add trace/run service functions and operator admin pages.
5. Add nullable `produced_by_run` to `FeedItemAssessment`.
6. Instrument `score_items.sh` using fake-provider-compatible wrapper commands.
7. Add run inspection for feed scoring.
8. Deploy behind `IDEAFLOW_EXECUTION_INSTRUMENTATION=false`.
9. Enable for one scheduled feed-scoring batch, reconcile, then expand.

This slice exercises prompt provenance, tokens, cost, failure handling, a user-visible projection, and high-volume production behavior without risking the core research workflow first.
