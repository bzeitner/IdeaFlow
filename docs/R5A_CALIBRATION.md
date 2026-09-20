# R5A A5 — Measured calibration

Status: implemented locally on 2026-09-20; production deployment and real calibration pending. Model graders remain disabled in production. No real provider calls or human labels were created by implementation tests. A4 remains verified; A5 is not yet verified.

Reviewer selection: **Brad and Zak**, designated by the user on 2026-09-20. Confirm their distinct active application user IDs when creating the frozen plan. This designation does not constitute completed reviews or approve a model, thresholds, or spending budget.

## Scope and safeguards

`calibrate_research` is a trusted local operator command, not a web or machine-token endpoint. It adds immutable plans, attempts, human reviews, frozen-case results, and reports with database update/delete/truncate guards. Admin exposes restricted metadata; packets and reports are written to new private files with mode 0600.

The first transport supports Anthropic Messages with an exact model identifier, temperature zero, standard service tier, no tools, no caching, and no automatic retries. The operator supplies `ANTHROPIC_API_KEY` in the process environment; never put credentials in requests or documentation. Model and pricing records must be explicit, effective, USD-denominated, and bound into the approved plan. No model or spending allowance is selected by default. Protocol fields follow the [official Anthropic client](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_client.py) and [Messages implementation](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/resources/messages/messages.py).

Provider calls run only after a durable database reservation commits. Call count, attempts per case, input bytes, output tokens, total reserved tokens, cost, and wall-clock timeout are bounded. Failed calls retain their reservation. Replaying a successful request returns its result; an incomplete or failed request never silently repeats a paid call. A new key permits a bounded retry only after the old attempt is terminal. Successful judgments cannot be retried for a preferable answer. Unexpected usage beyond a reservation stops subsequent calls for the plan.

Matched cases create measured evaluation child runs without reopening or changing the original generation trace. Legacy cases receive a dedicated evaluation trace, with no invented generation parent. Schema errors, truncation, transport failures, and missing evidence do not become negative quality labels. Available usage and protected raw responses are retained even when judgment parsing fails. Costs from provider token usage and frozen prices are **estimates**, not billed amounts; unavailable measurements remain explicit.

## Prepare the pilot

1. Use the [A4 workflow](R5A_OPERATIONS.md#a4--frozen-dataset-operator-workflow) to prepare the approved 30-case pilot, representative cohorts, distinct development/held-out cases, redacted context, and frozen evidence. Set snapshot sampling metadata `calibration_eligible` to `true` only for an approved calibration pilot. Production snapshot 1 is a storage canary with this value false and cannot be used.
2. Assign exact human rubric versions to every case. Missing source, execution, or requirements evidence must be recorded as unavailable, not reconstructed. Evidence entries default to `source_evidence`; an optional `kind` can explicitly identify `execution_evidence` or `frozen_requirements`.
3. Name two distinct active human reviewers. Each must independently author their own assessment. An operator can import those assessments with an explicit authorship attestation; an agent-generated assessment is not a human label.
4. Approve the exact grader model, current price source, maximum budget, and held-out thresholds before viewing held-out judgments. Prepare all rubric plans before collecting any held-out labels or grader results. A source family with prior held-out judgments cannot be reused to approve revised thresholds, even through another dataset revision.

Enable `IDEAFLOW_EXECUTION_EVALUATORS=true` and `IDEAFLOW_EXECUTION_DATASETS=true` only in the authorized operator process. `IDEAFLOW_EXECUTION_MODEL_GRADERS=true` is additionally required for a provider call. Configure protected payload storage and credentials before grading. Keep global production flags unchanged during preparation.

## Operator sequence

All examples use placeholders that must be replaced with reviewed IDs and private paths. Every command takes `--user-id`; this is a trusted shell identity assertion, not a login mechanism.

Publish a model evaluator using a request file containing `human_version_id`, `configuration_id`, and a new `version_number`:

```sh
.venv/bin/python manage.py calibrate_research publish-grader \
  --user-id OPERATOR_ID --request-file /private/path/grader.json
```

The published evaluator clones the human rubric, applicability, aggregation, required inputs, and metric exactly. It freezes the grader prompt but does not grant decision-use approval.

Prepare a plan request with these fields (values require explicit approval):

| Field | Required content |
| --- | --- |
| `snapshot_id`, `human_version_id`, `grader_version_id` | Exact frozen records |
| `reviewer_ids` | Two distinct active human user IDs |
| `thresholds` | `min_held_out_cases`, `min_agreement`, `min_coverage`, `max_progress_mae`, `max_critical_misses`, `min_critical_failures` |
| `budget` | `max_calls`, `max_input_bytes`, `max_output_tokens`, `max_total_tokens`, `max_cost_micros`, `timeout_seconds`, `max_attempts_per_case` |
| `supersedes_plan_id` | Optional exact active plan being replaced before any review, attempt, or report exists. Required when the held-out source family is already reserved. |

Agreement and coverage are fractions from 0 to 1; progress error is on the separate 1–5 ordinal scale. Cost is USD millionths. Budgets are conservative reservations, so the affordable number of calls can be smaller than `max_calls`.

```sh
.venv/bin/python manage.py calibrate_research plan-preview \
  --user-id OPERATOR_ID --request-file /private/path/plan-request.json \
  --output-file /private/path/plan-preview.json
```

Review the resulting plan, execution/pricing binding, and `approval_hash`. Then pass that same preview file and approved hash:

```sh
.venv/bin/python manage.py calibrate_research plan \
  --user-id OPERATOR_ID --request-file /private/path/plan-preview.json \
  --approve-hash APPROVED_HASH --idempotency-key UNIQUE_PLAN_KEY
```

Obtain a separate packet for each assigned reviewer:

```sh
.venv/bin/python manage.py calibrate_research packet \
  --user-id REVIEWER_ID --plan-id PLAN_ID --case-id CASE_ID \
  --output-file /private/path/reviewer-case.json
```

Packets omit producer/model metadata, cohort, split, and other judgments. This is metadata blinding: report prose itself can reveal identity, so redact revealing content during case preparation. The assessment JSON contains `criterion_results` with one `{id, status, reason, evidence_refs}` per criterion and `progress_score`. Reasons must be nonempty and bounded; references must name available frozen evidence. Pass/fail requires the criterion's required evidence kinds. Missing evidence requires abstention. Only judgeable ordinal progress gets an integer 1–5; quality and incomplete assessments require null.

```sh
.venv/bin/python manage.py calibrate_research review \
  --user-id REVIEWER_ID --plan-id PLAN_ID --case-id CASE_ID \
  --request-file /private/path/human-assessment.json \
  --human-attested --idempotency-key UNIQUE_REVIEW_KEY
```

Corrections use a new key and append a superseding record. Both independent originals remain. After both reviews exist, the operator can export `adjudication-packet` and submit `adjudicate` with the same options as `review`. The adjudication references the current two human labels; correcting either invalidates an older adjudication. Model judgments are excluded from the adjudication packet.

Each human-rubric/source-family combination can have only one active calibration
plan. A replacement must explicitly name the active plan and is permitted only
before that plan has any review, grader attempt, or report. Superseded plans
cannot accept new packets, labels, calls, reports, or approvals.

For each case, under the additionally enabled model-grader flag:

```sh
.venv/bin/python manage.py calibrate_research grade \
  --user-id OPERATOR_ID --plan-id PLAN_ID --case-id CASE_ID \
  --idempotency-key UNIQUE_GRADING_KEY
```

Inspect failed/in-flight attempts before retrying. `recover --user-id OPERATOR_ID --attempt-id ATTEMPT_ID` closes a stranded attempt only after its timeout plus five minutes; it retains the reservation and does not replay the provider call.

## Report and approval

```sh
.venv/bin/python manage.py calibrate_research report \
  --user-id OPERATOR_ID --plan-id PLAN_ID \
  --output-file /private/path/calibration-report.json
```

Reports separate development from held-out cases and include criterion/cohort agreement, coverage, critical misses, progress error, human disagreement, and generation/grading/combined costs with unknowns explicit. Optional communication criteria cannot compensate for critical failures. A report is a pilot calibration observation, not a statistical-power claim. Review development diagnostics as well as the held-out gates.

Incomplete gold labels or missing model results in either development or held-out cases, unresolved disagreements, unfinished attempts, insufficient critical-failure examples, expired content, unknown grader costs, and exceeded reservations block approval. Report inputs are hashed. Approval recomputes the current report and rejects stale evidence after any label correction or additional attempt. A review correction submitted after approval appends an immutable approval-supersession record immediately; the evaluator has no effective approval until a new current report passes and is explicitly approved.

Only after the report is eligible and reviewed:

```sh
.venv/bin/python manage.py calibrate_research approve-report \
  --user-id OPERATOR_ID --report-id REPORT_ID \
  --reason 'Explicit approved decision-use scope and evidence rationale'
```

This appends an evaluator approval. It does not change workflow selection, projections, schedules, or global flags. A5 closeout still requires deployment, real pilot/human/provider evidence, reviewed reports, and explicit version approval.

## Retention and rollback

Grader input/output payload copies expire no later than the source case's remaining lifetime, rounded down to whole days and capped by execution retention. Cases with less than a day remaining cannot be graded. Dataset body deletion does not itself immediately purge separate execution payload copies; required early deletion must also address those protected copies through the execution payload retention procedure. Immutable assessment rationales are audit records: reviewers must avoid copying sensitive source text into them. Private packet/report exports remain the operator's retention responsibility.

Rollback disables model graders and dataset writers; retain immutable audit rows and migrations. Stop outstanding calls and inspect attempt state before retrying. Do not edit frozen thresholds, labels, pricing bindings, or results in place.

## Local validation

The regression scope is `evaluations executions ideas.tests.test_migrations ideas.tests.test_views`. On 2026-09-20 the SQLite run passed 375 tests (four PostgreSQL-specific tests skipped). The same 375 tests passed on PostgreSQL with no skips, including concurrent reservation and audit-guard checks. Migration drift and whitespace checks passed. Tests use synthetic fixtures and fake providers; they provide no real calibration evidence.
