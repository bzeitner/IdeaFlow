# IdeaFlow Rewrite Technical Specification

Status: Proposed
Date: 2026-08-31
Primary principle: every LLM execution is observable, evaluable, and eligible for controlled experimentation.

## 1. Purpose

Rewrite IdeaFlow as a measurement-first system for advancing a portfolio of ideas through AI-assisted research, decisions, and execution. The system must answer, with evidence:

- What ran, why, and for whom?
- Which exact prompt, model, context, tools, and configuration were used?
- What did it cost and how long did it take?
- Was the result valid, useful, and better than the alternative?
- What human or business outcome followed?
- Can the winning configuration be reproduced and promoted safely?

The rewrite is successful when prompt/model/workflow changes are decisions supported by comparable outcomes rather than anecdotes.

## 2. Product scope

### In scope for the first release

- Portfolio, idea, ownership, lifecycle, tags, parent/child work, and next actions.
- Versioned workflow and prompt definitions.
- Provider-neutral LLM execution gateway.
- Complete execution telemetry, tracing, cost accounting, and artifacts.
- Evaluation datasets, automated evaluators, human feedback, and outcome attribution.
- A/B experiments with deterministic assignment, guardrails, and promotion workflow.
- Research/review/summarize workflows.
- Source ingestion and per-idea evidence ranking.
- Recurring jobs and result disposition.
- Knowledge-graph suggestion and review.
- Migration of existing IdeaFlow data with legacy IDs and provenance.

### Follow-on modules

- Execute/critique integration with code repositories.
- Weekly portfolio reporting from event data.
- Persona and relationship councils.
- Podcast scripting and production.
- Public idea and content pages.

### Explicitly deferred

- A generic help/chat feature.
- A global feed inbox optimized for consumption volume.
- Fully autonomous experiment promotion.
- Microservices solely for organizational separation.

## 3. Architecture

Use a modular monolith for the control plane and a separate worker process for asynchronous jobs. Both use the same application packages and PostgreSQL database. This preserves transactional integrity and operational simplicity while allowing execution workers to scale independently.

```text
Browser / API client
        |
Control-plane web app
        |
Application services ------------------------------+
  Portfolio | Workflows | Experiments | Evaluation |
        |                                             |
PostgreSQL + object storage                          |
        |                                             |
Transactional outbox -> job queue -> workers -> LLM gateway
                                         |          |
                                      tools      providers
                                         |          |
                                         +-> execution events
```

Rules:

- The web layer never calls an LLM inline for durable workflows.
- Creating a run and enqueueing it is one transaction using an outbox record.
- Workers lease jobs, heartbeat, and write append-only execution events.
- Provider integrations exist only behind the LLM gateway.
- Large prompts, responses, tool payloads, and artifacts live in encrypted object storage; PostgreSQL stores hashes, metadata, and queryable excerpts.
- User-facing research, summaries, graph edges, and next actions are projections with explicit provenance back to executions.
- All timestamps are UTC; user display timezone is a preference.

## 4. Domain model

### Portfolio domain

`Workspace`

- id, name, settings, retention policy, created_at

`User`, `Membership`, `Role`

- workspace-scoped access; owner, editor, viewer, and operator capabilities

`Idea`

- id, workspace_id, title, summary, notes
- type: research, product, project, content
- lifecycle: inbox, exploring, active, blocked, completed, archived
- priority: none, focus, high, normal, low
- owner_id, parent_id, interest, created_at, updated_at
- legacy_id and legacy_metadata for migration audit

`Tag`, `IdeaTag`, `NextAction`, `Resource`

- ordered next actions have open/completed/dismissed state and timestamps
- resources store canonical URL and provenance

### Workflow definition domain

`WorkflowDefinition`

- stable key (`research`, `review`, `summarize`, `feed_score`, etc.)
- input schema, output schema, terminal conditions, allowed tools
- active version pointer

`WorkflowVersion`

- immutable workflow snapshot
- prompt revision IDs, context-builder version, tool-policy version
- default model configuration ID, evaluator suite version ID
- proposed/approved/superseded/rejected state and approver metadata

`PromptTemplate` and `PromptRevision`

- retain current immutable governance
- store template body, declared variables, content hash, author, review state
- never alter approved content in place

`ModelConfiguration`

- provider, provider model identifier, capability class
- temperature and other provider-neutral settings
- provider-specific settings JSON
- pricing-version ID and active dates
- immutable after use

### Execution domain

`ExecutionTrace`

- one user or scheduler intent across one or more calls
- trace_id, workflow version, subject type/id, trigger, actor, experiment assignment
- queued_at, started_at, completed_at, final status

`LLMRun`

- run_id and trace_id
- parent_run_id for subcalls/evaluators
- purpose (`generation`, `classification`, `evaluation`, `embedding`)
- prompt revision IDs and rendered-input object reference/hash
- model configuration and provider request ID
- context manifest: source IDs, versions, token budget, truncation decisions
- output object reference/hash and parsed structured output
- status, attempt number, idempotency key
- queued, first-token, completed timestamps
- input, output, cached, and reasoning token counts when available
- normalized cost in micros plus pricing version
- finish reason, schema-valid flag, error class/code, redacted error detail
- safety/policy result and tool-call counts

`ToolInvocation`

- run_id, tool name/version, request/response references and hashes
- started/completed timestamps, status, error class
- mutating flag, idempotency key, affected subject IDs

`ExecutionEvent`

- append-only lifecycle and provider events with sequence number
- powers debugging and trace reconstruction

`ArtifactVersion`

- immutable output version, media type, checksum, storage key
- producing run_id, source citations, supersedes ID

### Evaluation domain

`EvaluatorDefinition` and `EvaluatorVersion`

- stable evaluator key and immutable rubric/implementation
- type: deterministic, model-graded, human, or outcome
- metric name, direction, range, applicability predicate

`EvaluationResult`

- evaluated run/output and evaluator version
- score, label, pass/fail, explanation reference
- evaluator run ID when model-graded
- created_at and invalidation state

`HumanFeedback`

- run/output/evidence target
- action: accept, edit, reject, useful, irrelevant, save, action, dismiss
- optional rating/reason and before/after content hashes
- actor and timestamp

`OutcomeEvent`

- append-only product event tied to an idea and, where attributable, a run
- examples: next action accepted/completed, source cited, relation accepted/rejected, result actioned, artifact reused, episode published, PR merged
- event type, value, occurred_at, attribution method and confidence

`EvaluationDataset`, `DatasetCase`, `DatasetSnapshot`

- versioned test cases sampled from production with redaction and consent controls
- fixed inputs, expected properties/labels, cohort metadata
- immutable snapshots enable reproducible offline comparison

### Experiment domain

`Experiment`

- hypothesis, primary metric, secondary metrics, guardrails
- eligible workflow versions and population predicate
- unit of randomization, allocation, start/end criteria
- state: draft, running, paused, completed, cancelled

`ExperimentVariant`

- control/treatment name and weight
- overrides for workflow, prompt, model configuration, context builder, or tool policy

`ExperimentAssignment`

- experiment, randomization-unit key, variant, assignment hash, assigned_at
- unique constraint prevents reassignment and cross-contamination

`ExperimentObservation`

- assignment, run, metric, value, timestamp, evaluator/outcome source
- retains raw observations; aggregate tables are rebuildable

`ExperimentDecision`

- analysis snapshot, decision, rationale, approver, timestamp
- promotion creates a new approved workflow version; it never mutates history

### Sources and evidence domain

`Source`

- canonical URL, kind, title, fetch policy, trust metadata, active state

`Subscription`

- source + idea/workflow intent, relevance prior, budget, pause state

`SourceItem`

- deduplicated item identity, metadata, content hash, fetch state

`EvidenceCandidate`

- source item + idea, deterministic prefilter score, LLM score, rank
- scoring run ID, decision state, display/exposure timestamp

`EvidenceAction`

- useful, irrelevant, saved, cited, action-created, dismissed
- human or downstream actor; becomes evaluation ground truth

## 5. Execution lifecycle

1. A human, schedule, or API requests a workflow for a subject.
2. The system resolves the approved workflow version.
3. Eligibility rules select active experiments; assignment is deterministic using a salted hash of experiment ID and randomization unit.
4. The complete resolved configuration is frozen on `ExecutionTrace` before work starts.
5. Context is assembled into a manifest that records each source/version and inclusion or truncation reason.
6. The gateway creates an `LLMRun`, writes the immutable request reference, and invokes the provider.
7. Streaming/provider usage events update timestamps and token/cost facts without changing the frozen configuration.
8. The response is stored, parsed, schema-validated, and projected only if terminal checks pass.
9. Synchronous deterministic evaluators run first; model graders run asynchronously and are themselves recorded as LLM runs.
10. Human feedback and downstream outcome events accrue after completion.
11. Experiment observations are derived idempotently from evaluation and outcome events.

Failed and cancelled runs remain in the ledger. Retries are new `LLMRun` attempts under the same trace, never overwritten rows.

## 6. Measurement specification

### Required metrics for every LLM run

| Dimension | Metrics |
| --- | --- |
| Reliability | completion rate, retry rate, timeout rate, schema-valid rate, tool-error rate |
| Latency | queue time, time to first token, provider time, tool time, end-to-end time |
| Usage | input, output, cached, reasoning, and total tokens |
| Cost | provider charge if supplied; otherwise normalized estimated cost using the effective dated price table |
| Quality | evaluator scores, pass rates, factual/citation checks, human acceptance/edit/rejection |
| Outcome | workflow-specific conversion events and time to outcome |
| Efficiency | cost and latency per accepted output or successful outcome |

### Workflow-level primary metrics

| Workflow | Primary metric | Guardrails |
| --- | --- | --- |
| Research | human acceptance or next-action adoption within 7 days | citation validity, cost, latency, unsupported-claim rate |
| Review | correct disposition agreement and useful change rate | unnecessary churn, repeated-review rate |
| Summary | accepted without material edit | coverage, factual consistency, length, cost |
| Feed/evidence scoring | precision@K based on useful/save/action labels | recall on curated set, cost per accepted item, queue age |
| Relationship suggestion | accepted-edge precision | contradiction/duplicate errors, coverage, cost |
| Repeat discovery | interested/actioned results per run | duplicate rate, stale result rate, cost |
| Execute/critique | merged/accepted change rate | tests, regressions, security findings, cycle time |
| Podcast script | approved/published without major edit | citation coverage, duration fit, regeneration rate |

The current 1–5 `quality` field may be migrated as legacy feedback, but it cannot be the sole primary metric because it conflates evidence confidence, writing quality, and usefulness.

### Attribution windows

- Immediate: validation, deterministic checks, explicit accept/reject/edit.
- 7 days: next-action adoption, evidence save/citation, relation decision.
- 30 days: completed action, actioned repeat result, merged change, published content.
- Record late outcomes without rewriting earlier experiment snapshots.

### Metric integrity

- Define each metric once in a versioned metric registry.
- Store raw events and evaluator results; aggregates are derived.
- Exclude evaluator calls from generation-quality denominators but include their cost in workflow total cost.
- Distinguish missing feedback from negative feedback.
- Report exposure: an item cannot count as rejected if it was never shown.
- Segment by workflow, idea type, model, prompt version, and trigger.
- Mark imported/estimated measurements explicitly.

## 7. Experimentation design

### Supported tests

- prompt revision versus prompt revision;
- model or model-configuration comparison;
- context-selection and token-budget strategy;
- tool availability or tool-policy change;
- evaluator/ranking threshold;
- complete workflow version comparison.

### Assignment and isolation

- Default randomization unit is `idea_id`, preventing one idea from oscillating between variants during a test.
- Use `source_item_id` for high-volume evidence-ranking tests when cross-idea contamination is acceptable.
- Assignment is sticky and stored before execution.
- Concurrent experiments require declared namespaces; conflicting experiments cannot enroll the same run.
- Internal evaluator runs inherit the parent experiment metadata but are not independently randomized.

### Analysis and decision rules

- Every experiment declares one primary metric before starting.
- Define minimum sample size, minimum detectable effect, maximum duration, and guardrails before enrollment.
- Show effect size and uncertainty, not only a winner label.
- Do not repeatedly peek and stop using ordinary fixed-horizon significance logic. Use a preselected fixed-horizon or sequential method and store the analysis method/version.
- Report sample-ratio mismatch, missing outcome rate, crossovers, retries, and segment imbalance.
- A variant can auto-pause on reliability, safety, or cost guardrails.
- Promotion requires human approval and creates a new workflow version.

### Offline before online

1. Replay both candidates on the same immutable dataset snapshot.
2. Run deterministic checks and blinded evaluators.
3. Inspect disagreements and evaluator bias.
4. Admit the candidate to a small online allocation only after guardrails pass.
5. Expand allocation gradually; retain an explicit control until decision.

## 8. LLM gateway contract

Request:

```json
{
  "trace_id": "uuid",
  "purpose": "generation",
  "model_configuration_id": "uuid",
  "messages_ref": "object://...",
  "response_schema": {},
  "tools": [],
  "timeout_ms": 120000,
  "idempotency_key": "workflow:subject:version:attempt"
}
```

Normalized response:

```json
{
  "provider_request_id": "string",
  "status": "succeeded",
  "output_ref": "object://...",
  "parsed_output": {},
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cached_tokens": 0,
    "reasoning_tokens": 0
  },
  "timing": {
    "started_at": "timestamp",
    "first_token_at": "timestamp",
    "completed_at": "timestamp"
  },
  "error": null
}
```

The adapter must preserve the raw provider response in protected storage for audit while exposing a stable normalized contract. Provider credentials never enter run records or logs.

## 9. APIs and user experience

### Core API resources

- `/api/v1/ideas`, `/next-actions`, `/resources`, `/artifacts`
- `/api/v1/workflows`, `/prompt-revisions`, `/model-configurations`
- `/api/v1/traces`, `/runs`, `/tool-invocations`, `/events`
- `/api/v1/evaluations`, `/feedback`, `/outcomes`, `/datasets`
- `/api/v1/experiments`, `/variants`, `/assignments`, `/decisions`
- `/api/v1/sources`, `/subscriptions`, `/evidence-candidates`, `/evidence-actions`
- `/api/v1/relations`, `/relation-suggestions`

All mutating workflow endpoints accept an idempotency key. List APIs use cursor pagination. Machine clients receive scoped service credentials rather than one global bearer token.

### Required UI

- Idea workspace: state, actions, evidence, research, artifacts, relationships, and run history.
- Run inspector: rendered input, configuration, trace tree, tools, output, tokens, cost, latency, evaluations, feedback, and outcomes.
- Prompt/workflow governance: diff, offline evaluation comparison, approval, and rollback-by-new-version.
- Experiment dashboard: hypothesis, allocation, exposure, metrics, uncertainty, segments, guardrails, and decision log.
- Evidence queue: top-ranked items only, with one-click labels and downstream actions.
- Operations view: queue depth, error rate, stuck leases, provider health, and budget consumption.

## 10. Security, privacy, and safety

- Workspace-scope every query and object key.
- Encrypt transport and stored prompt/response artifacts.
- Redact configured secrets and personal data before telemetry export.
- Store hashes for integrity and minimize duplicated sensitive content.
- Apply URL allow/deny and public-IP checks to all source fetches; retain current SSRF protections.
- Use scoped, rotatable service principals for workers and external agents.
- Log all approvals, prompt promotions, experiment changes, and mutating tool calls.
- Separate public publishing state from authenticated IdeaFlow visibility.
- Set retention rules independently for metadata, prompts/responses, tool payloads, and media.
- Never let model-generated text alter workflow policy, experiment assignment, or tool permissions.

## 11. Reliability and operations

- At-least-once job delivery with idempotent handlers.
- Lease + heartbeat + bounded retry with exponential backoff and dead-letter state.
- Transactional outbox for enqueue and projection events.
- Per-provider and per-workflow concurrency/rate limits.
- Workspace/workflow budgets for tokens, cost, and runs; reject or pause before overspend.
- Structured logs include trace ID and run ID but not raw prompts by default.
- Metrics and alerts cover queue age, completion, schema failure, provider errors, costs, and experiment guardrails.
- Nightly reconciliation checks traces, run usage, costs, projections, object references, and experiment observations.
- Backups cover PostgreSQL and object storage; restore is tested.

Service objectives for the initial release:

- 99.9% control-plane availability monthly.
- 99% of accepted jobs begin within 5 minutes under normal provider availability.
- 99.5% of terminal provider calls have a complete usage/timing record or an explicit `measurement_unavailable` reason.
- No untraceable user-visible AI output: every generated projection has a producing run ID.

## 12. Migration plan

### Phase 0: instrument the existing application

- Add trace/run IDs around current research, review, feed scoring, relationship, weekly, and podcast-script calls.
- Capture prompt revision, provider/model, usage, latency, and status immediately.
- Begin collecting explicit human feedback before the rewrite ships.

This phase establishes a baseline and avoids launching an experimentation system with no comparison data.

### Phase 1: new control plane and execution ledger

- Deploy new schema alongside the current tables.
- Route one low-risk workflow through the gateway in shadow mode.
- Compare outputs and telemetry without changing user-visible state.
- Validate cost reconciliation and trace completeness.

### Phase 2: portfolio and core workflows

- Import users, ideas, categories/tags, stages/lifecycle, next actions, resources, research, artifacts, prompt revisions, and model labels.
- Dual-write new research/review/summary activity temporarily.
- Cut reads to the new projections after reconciliation.

Category mapping:

| Legacy | New type | Additional metadata |
| --- | --- | --- |
| Research, Research Effort | Research | preserve original label |
| App, Side Project | Product | add legacy label tag |
| Project | Project | none |
| App Improvement | inherit parent type or Project | mark as improvement and link parent |
| Book, Podcast | Content | content-format tag |
| Passive Income | inferred Product/Project | `passive-income` goal tag; flag for review |
| News, Focus Project | no automatic active mapping | unused; retain in legacy metadata |

Lifecycle mapping requires review because production has no Current records and 54/102 ideas have no Stage. Default conservatively to `exploring` for tracking ideas without a stage, retain archived as archived, and emit a migration-review report.

### Phase 3: sources, graph, and repeat work

- Import feeds as sources, idea feeds as subscriptions, items as source items, and assessments/ratings as legacy feedback.
- Do not enqueue all 20,460 imported items for LLM processing.
- Import relations, suggestions, reviews, repeat results, and their decisions with legacy provenance.
- Start new prefilter/ranking tests only on newly ingested items.

### Phase 4: optional verticals and retirement

- Move councils, weekly reports, repository workflows, and podcast production.
- Verify published podcast URLs and media before cutover.
- Freeze old writes, reconcile counts/hashes, switch traffic, and retain the old database read-only for the agreed audit period.

## 13. Acceptance criteria

The rewrite cannot launch until:

- 100% of user-visible AI outputs in migrated workflows have a producing trace/run.
- At least 99.5% of successful calls capture provider, model configuration, timing, and token usage or an explicit unavailable reason.
- Costs reconcile against provider reporting within an agreed tolerance.
- Prompt/workflow/model configurations used by a run are immutable and reproducible.
- A dry-run A/B experiment can assign stably, collect observations, enforce a guardrail, and promote a winner through approval.
- Evaluator calls are distinguishable from production generation calls and included in total workflow cost.
- Human feedback distinguishes non-exposure, no feedback, rejection, edit, acceptance, and downstream action.
- Imported entity counts and sampled content hashes reconcile with production.
- Source ingestion demonstrates bounded backlog and reports precision@K and cost per accepted item.
- Access-control, SSRF, idempotency, retry, lease, and backup/restore tests pass.

## 14. Initial implementation sequence

1. Metric registry, execution ledger, object references, provider-normalized usage, and trace UI.
2. Versioned workflow/model configuration and immutable prompt linkage.
3. Research workflow plus deterministic validation and human accept/edit/reject feedback.
4. Dataset snapshots and offline comparison runner.
5. Experiment assignment, observations, guardrails, and decision workflow.
6. Portfolio migration and next-action outcome tracking.
7. Sources/evidence pipeline with prefiltering and ranking experiments.
8. Relationship and repeat-result outcome integrations.
9. Remaining councils, reporting, repository, and podcast modules.

This order makes measurement infrastructure precede feature migration, so the rewrite does not reproduce the present attribution gaps.
