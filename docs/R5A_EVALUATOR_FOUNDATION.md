# R5A — Evaluator, Feedback, and Dataset Foundation

Status: A1/A2 deployed; initial deterministic production canary verified on 2026-09-16; A3–A6 remain planned
Plan date: 2026-09-16
Parent plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

A1/A2 usage and limits: [R5A_OPERATIONS.md](R5A_OPERATIONS.md). The initial
command evaluates research structure deterministically; human/model rubric
definitions are seeded but uncalibrated. Datasets, feedback UI, and model-grader
execution remain in their later slices.

## 1. Outcome and scope

Make an existing research output evaluable against exact, immutable rubric
versions; record human feedback and exposure separately; and freeze redacted
examples for calibration and later paired comparisons. Production research
generation, projection authority, and scheduling remain unchanged.

R5A delivers:

- Versioned metrics, evaluators, criterion definitions, and auditable results.
- Separate research-quality diagnostics and 1–5 research-progress scores.
- Deterministic checks and the measured model-grader integration contract.
- A shared feedback/exposure service, initially surfaced on research and summary
  outputs, plus evaluator/feedback inspection for operators.
- Immutable dataset snapshots and human calibration records.
- Feature flags, migrations, tests, and a production acceptance report.

R5B owns research checkpoints and context-policy construction. R5C owns
`OfflineEvaluationRun`, paired generation, the three-role blinded council,
order-reversal experiments, and paired quality/cost reports. R5A does not enable
online experiments, migrate taxonomy, replace feed UI, or automatically promote
workflow versions. Research criteria do not substitute for workflow-specific
feed, relationship, repeat, or podcast evaluators.

## 2. Existing integration points

The repository already provides `ExecutionTrace`, `LLMRun.parent_run`,
`RunPurpose.EVALUATION`, protected payload storage, canonical hashing,
`OutcomeEvent`, service principals, execution events, and immutable configuration
admin views. Reuse these rather than introducing another execution ledger.

Create an `evaluations` application for evaluation and dataset records. Keep
provider usage and execution lifecycle in `executions`. Integrate user feedback
through existing idea ownership and access checks, with UI entry points in
`ideas/templates/ideas/research_entry_view.html` and the relevant summary views.
Reuse `IDEAFLOW_EXECUTION_FEEDBACK`; add separately controlled evaluator and
model-grader flags, both disabled by default. Names below describe proposed
contracts, not APIs already present in the repository.

Existing immutability base classes guard instance saves/deletes only. R5A must
also reject bulk update/delete paths for its immutable records and enforce
immutable content at the database layer. Do not claim that admin read-only
fields alone provide immutability.

## 3. Data model

| Record | Required contract |
| --- | --- |
| `MetricDefinition` | Immutable `(key, version)`; description, unit, direction, range, aggregation, applicability, missing-value policy, content hash, and creation actor/time. Evaluators reference an exact version. |
| `EvaluatorDefinition` | Stable key, name, description, method family, and active flag. Activation selects eligibility; it does not rewrite previous versions. |
| `EvaluatorVersion` | Immutable evaluator/version pair; exact metric references, rubric schema, required inputs, applicable workflows/types, implementation identifier, aggregation and critical-failure rules, prompt/model configuration where relevant, hash, and creation metadata. |
| `EvaluatorApproval` | Append-only approval/rejection/supersession decision linked to an exact evaluator version, actor, reason, and calibration evidence. Approval state must not mutate frozen rubric content. |
| `EvaluationResult` | Immutable completed assessment: evaluator version, evaluated run and/or frozen case, output hash, input-manifest hash, criterion results, aggregate values, coverage, critical failures, concise rationale, evidence references, evaluator actor or grader run, timestamps, idempotency key, and optional superseded-result link. |
| `EvaluationExposure` | Append-only actor/session-scoped event identifying the rendered output version/hash, producing run where available, UI surface, occurrence time, and idempotency key. This exists independently of feedback. |
| `HumanFeedback` | Append-only target run/output version, action, actor, optional rating/reason, exposure link when known, feedback time, before/after hashes for actual edits, source, idempotency key, and optional correction link. |
| `EvaluationDataset` | Stable dataset identity, purpose, ownership, eligibility policy, and access classification. |
| `DatasetCase` | Immutable case revision with frozen redacted objective, prior state, candidate output where applicable, source/evidence manifest, evidence cutoff, explicit rubric assignments, cohort metadata, content hashes, and origin references. Corrections create a new revision. |
| `DatasetSnapshot` | Immutable manifest of ordered case revisions and hashes, sampling/eligibility rules, redaction policy version, and creator/time. No mutable business row is its source of truth. |
| `HumanCalibrationLabel` | Append-only reviewer assessment of a frozen case under exact rubric versions, criterion labels, 1–5 progress score, supporting references, and optional adjudication links. Preserve original independent labels. |

Represent criterion definitions as validated, versioned structured content in
`EvaluatorVersion`; a separate criterion table is unnecessary for the first
release. Each item carries ID, dimension, applicability, required evidence,
method, severity, and pass/fail examples. Validate result criterion IDs and
evidence references against that version's schema.

An assessment of a production output must identify its producing run and frozen
output hash. A legacy calibration case may have no producing run, but must
record that provenance is unavailable. Never invent a historical execution.
Preserve snapshot labels/hashes if a business object disappears. Protect audit
links from cascading deletion; use opaque references or nullable business links
where historical retention must not prevent ordinary business deletion.

Retries with the same idempotency key and identical content return the original
record; conflicting content is rejected. A genuine reevaluation or correction
creates a new record with explicit lineage.

## 4. Rubric and scoring contracts

### Research progress

Seed `research.answer_progress` version 1 with the parent plan's anchors:

| Score | Meaning |
| --- | --- |
| 1 | No supported progress toward the objective. |
| 2 | Useful observation without closing a meaningful gap. |
| 3 | Resolves part of the objective or materially narrows alternatives. |
| 4 | Supports a defensible decision with a small explicit uncertainty remaining. |
| 5 | Fully answers the scoped objective with sufficient evidence, addresses material counterarguments, and leaves no decision-relevant gap. |

Use the full definitions from the parent plan in seeded content. Do not infer
progress from presentation, quality pass rates, or `ResearchEntry.quality`.
Freeze `rubric_key` explicitly on each case until authoritative idea types exist.
Define the remaining type-specific rubric contracts from the parent plan, but
leave them unavailable for decision use until calibrated for their own type.

### Research quality

Turn the amended Idea 111 draft into atomic criteria under explicit requirements,
implicit requirements, synthesis, evidence/references, communication, and
instruction/format following. Preserve it as repository-owned seed content; do
not depend on a Downloads file at runtime. Verify external benchmark claims
before using them as scientific justification; they are not acceptance gates.

- Requirements, including expected contextual checks, are frozen before grading.
- References may identify external sources, internal artifacts, or observations.
- Reference resolution and factual support are different criteria.
- Process compliance requires execution evidence, not claims in the report.
- Presentation is reported separately and cannot offset substantive failure.
- Binary criteria return pass, fail, not applicable, or insufficient evidence.
  Inapplicability requires a reason; unavailable grader inputs are distinct from
  an observed absence of required support, which fails.
- Pass rate is `passed / (passed + failed)` for applicable completed judgments.
  An empty denominator yields no score. Also report expected/applicable counts,
  insufficient-evidence counts, applicability uncertainty, and judgment coverage.
- Critical failures remain visible independently of the aggregate. Freeze which
  criteria block decision use and how much missing evidence is acceptable.
- Store concise rationales and evidence references, not detailed chain-of-thought.

Implement six-dimensional diagnostics before considering weighted composite
scores. Model-family diversity is a calibration variable, not a guarantee of
judge independence. R5C adds independent roles and order-bias testing.

## 5. Evaluation services and execution

Provide service boundaries for publishing evaluator versions, recording approval
decisions, evaluating a frozen output, validating/storing results, recording
feedback/exposure, freezing cases, and creating dataset snapshots. Operator
commands invoke these services; web handlers do not call providers.

The first deterministic evaluator set covers schema validity, required fields,
empty/truncated output indicators, and referenced-object validity. Explicitly
declare workflow applicability: a JSON schema check cannot be applied blindly
to a free-text report. Reuse existing workflow validators where suitable;
record their implementation version. Citation URL checks use the existing safe
fetch protections, bounded requests, and timestamped observations. A timeout
is not proof that a citation is fabricated. Semantic duplicate detection and
claim support remain separate from exact duplicate/reference checks.

R5A includes an operator-invoked, bounded model-grader path for calibration,
disabled by default. It consumes frozen inputs and a selected evaluator version,
creates a measured evaluation-purpose child `LLMRun` under the evaluated run's
trace, and stores its result only after schema validation. Do not reopen or
change the completed generation run, completion schedule, or trace outcome.
For a snapshot without a producing run, create a dedicated evaluation trace
with explicit dataset/case provenance; never fabricate a generation parent.

Record prompt/configuration, input/output references and hashes, usage, cost,
timing, and unavailable reasons through existing execution services. Strip
treatment/model identity from judge-visible inputs where it is not required;
retain the complete operator audit manifest separately. Treat report content
as untrusted data, never grader instructions.

Use bounded attempts and record failures as execution failures, not negative
quality labels. Deterministic/human results must not fabricate LLM runs. Report
generation and grader cost separately and combined without double-counting;
incomplete costs remain explicitly incomplete. No model calls run in migrations,
seeding, ordinary page rendering, or default test execution.

## 6. Feedback and inspection

Research and summary views get shared accept/reject/useful controls and show
feedback history. Record edit feedback only after a real authorized edit has
succeeded; capture before/after hashes rather than adding a button that claims
an edit occurred. The common action vocabulary also supports save, cite, action,
irrelevant, and dismiss for later workflow integrations.

Exposure is recorded when the output is rendered in the active UI, not when an
API fetch or background prefetch occurs. Deduplicate retries within a view
session while retaining later exposures. Record feedback without inventing
exposure when it arrives from another surface. Derive not-exposed, exposed with
no feedback, positive, negative, and mixed histories for a defined actor/output
and reporting window. Missing exposure telemetry is reported as unknown where
instrumentation was unavailable, rather than asserting the user never saw it.

Authenticated users can act only on outputs they can access. Machine evaluator
and dataset operations use separately scoped permissions and cannot impersonate
human feedback. Raw payload access retains its separate operator permission.
Feedback linking to `OutcomeEvent` is idempotent where an existing outcome
applies; feedback is not automatically synonymous with downstream success.

Admin/run inspection displays evaluator version, target hash, per-criterion
results, progress score, coverage, critical failures, grader cost, and feedback.
Unauthenticated or cross-owner requests cannot expose results or frozen inputs.

## 7. Dataset and calibration workflow

1. Sample authorized production outputs and their available prior context.
   Include short/long histories, conflicting/stale evidence, accepted/edited/
   rejected examples, polished repetition, concise decisive findings, and missing
   inputs. Do not process the historical feed backlog or claim representativeness
   from a convenience sample.
2. Preview redaction and obtain an operator approval before freezing each case.
   Record exclusions and unavailable evidence explicitly.
3. Freeze objective, output, source excerpts/manifests, cutoff, rubric assignments,
   and cohorts. Hash the actual redacted content used for evaluation.
4. Build an initial 30-case research seed snapshot as a planning target, with
   coverage across the named conditions. This is a calibration pilot, not a
   statistically powered promotion dataset.
5. Collect two independent human assessments per case, then adjudicate
   disagreements while preserving both original labels. Reviewer availability
   is an external dependency; automated labels must not stand in for humans.
6. Compare deterministic/model results to the labels. Report criterion agreement,
   critical-failure misses, coverage/abstentions, progress-score error and
   disagreement, cohorts, and grader cost. Keep rubric-development examples
   separate from a held-out calibration check and record membership.
7. Before examining held-out results, record operator-approved agreement/error
   tolerances and critical-failure rules. Passing them permits the exact rubric
   version's declared use; otherwise revise, create a new version, and reassess.
   No promotion decision is allowed merely because the pilot ran successfully.

Dataset retention is distinct from transient execution payload retention. Store
approved redacted cases under an explicit dataset retention/access policy; do
not silently copy raw payloads into indefinite storage. Metadata remains
immutable, but required content deletion leaves a tombstone and makes the case
unavailable. An expired or deleted case cannot silently be rebuilt from current
idea state or treated as reproducible.

## 8. Delivery slices

| Slice | Deliverable | Acceptance |
| --- | --- | --- |
| A1 — Schema and invariants | Evaluations app, metric/evaluator/result/approval schema, immutable content enforcement, admin inspection, additive migrations. | Exact versions are traceable; invalid/cross-target results and mutation attempts fail; idempotent writes do not duplicate. |
| A2 — Rubrics and deterministic checks | Repository-owned seed definitions, research progress anchors, six quality dimensions, validators and operator evaluation command. | A frozen existing research output receives valid, inspectable deterministic results; quality/progress and missing judgments remain distinct. |
| A3 — Feedback and exposure | Shared services, permissions, research/summary UI, edit integration, and outcome links. | Authorized interactions retain exact output identity; exposure-only and feedback states are distinct; retries and actual edits are audited correctly. |
| A4 — Frozen datasets | Case/snapshot/label schema, authorized sampling, redaction preview/approval, immutable manifests and export. | Cases remain stable after source edits; missing/expired content is explicit; snapshot export verifies hashes. |
| A5 — Measured calibration | Bounded grader adapter, frozen-input command, human labeling workflow, calibration report and version approval. | Every model judgment is measured; human labels and adjudications are retained; decision eligibility follows prespecified calibration gates. |
| A6 — Production acceptance | Canary rollout, reconciliation extension, operator runbook, rollback exercise and evidence report. | All release checks below pass with no change to research generation or scheduling. |

Implement in this order, with A1/A2 as the first mergeable development slice.
A3 and A4 can be implemented independently after A1's contracts settle. Human
labeling and threshold approval are explicit prerequisites for A5 completion,
not reasons to block schema, deterministic evaluators, or feedback development.

## 9. Verification

- Model/database tests: version uniqueness; immutable updates/deletes including
  bulk/raw database paths; content hashes; correction lineage; retained audit
  links; score/range checks; criterion IDs; atomic idempotency under concurrency.
- Evaluator tests: applicability, pass-rate denominator, missing inputs versus
  observed failures, critical failures, and prevention of quality-to-progress
  conversion. Include polished repetition and concise decisive-answer cases.
- Access/UI tests: ownership, service scopes, CSRF for browser mutations,
  exposure deduplication, no prefetch exposure, feedback correction, real edit
  hashes, and no leakage of protected payloads.
- Dataset tests: redaction approval, snapshot reconstruction/hashes, source edits
  and deletion, retention expiry/tombstones, explicit rubric assignment, and
  preserved independent labels.
- Execution integration tests with fake providers: child evaluation attribution,
  schema rejection, retries, timeouts, measurement unavailability, blinding,
  total-cost accounting, and unchanged generation/schedule completion state.
- Migration/rollout tests: existing rows require no fabricated feedback/scores;
  disabled flags prevent new evaluator/feedback writes while preserving reads;
  toggling flags does not alter authoritative workflow selection.

Run relevant new suites plus existing execution API/services/storage and affected
idea-view tests. Run application checks and migration consistency checks. No
real provider call is needed for ordinary tests; use one explicitly budgeted
production calibration canary to verify the deployed measured path.

## 10. Rollout, rollback, and definition of done

Apply additive migrations with evaluator/model-grader/feedback flags disabled.
Seed proposed definitions idempotently, verify their hashes, and approve only
validated uses. Enable deterministic evaluation for an operator-selected canary,
then feedback on the initial surfaces. Enable the bounded grader only after
dataset approval and an explicit call/token/cost budget. Do not enable existing
experiment flags or change `WorkflowCutover` modes.

Extend reconciliation to report evaluated targets with valid attribution,
evaluator-version/hash consistency, missing/invalid results, critical failures,
feedback/exposure coverage, dataset availability, and grader telemetry/cost.
Separate failed evaluations from failed quality criteria. Report historical
unknowns explicitly; never backfill synthetic exposure, feedback, or scores.

R5A is accepted when:

- A production output has a reproducible evaluation linked to exact immutable
  metric/rubric versions and frozen inputs.
- Quality diagnostics and progress scores remain distinct throughout storage,
  UI, exports, and reports; any uncalibrated result is visibly non-decision-grade.
- At least one applicable deterministic evaluator is registered for each current
  structured workflow; research-only criteria are not applied indiscriminately.
- Human exposure/feedback works on research and summary outputs with valid actor
  attribution; feedback, exposure, and missing instrumentation remain distinct.
- An approved research seed snapshot and independent human labels exist, with a
  reproducible calibration report. Any rubric authorized for decision use has
  passed its prespecified calibration gates.
- Model-grader calls have complete measured-execution links and usage facts or
  explicit unavailable reasons; combined costs do not omit or double-count them.
- Canary evidence demonstrates unchanged authoritative research output paths and
  schedule advancement, and permissions/rollback checks pass.

Rollback disables new grader work first, then evaluation/feedback writers as
needed; in-flight attempts reach an audited terminal state without rerunning generation.
Keep results, versions, snapshots, and audit history readable under existing
permissions. Reverting schema or deleting historical records is not rollback.
Store dated acceptance evidence under `docs/evidence/` and update the parent
plan only after deployed behavior, rather than code presence alone, is verified.
