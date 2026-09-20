# R5A operator guide

Status: A1/A2 deployed to production on 2026-09-16 at `1af80f3`; initial deterministic canary verified. A3 deployed and verified at user direction on 2026-09-18; A4 deployed with canary/retention verification completed on 2026-09-20. Evaluators and feedback enabled; model graders disabled.

## What is available

The `evaluations` application adds immutable metric definitions, evaluator
versions, approval decisions, and results, plus stable evaluator identities.
Admin provides inspection; audit records cannot be added, edited, or deleted
through admin. Results reference exact run/output and input-manifest hashes.

Seeded evaluator versions are:

| Key | Method | Purpose |
| --- | --- | --- |
| `research.answer_progress@1` | Human | Independent 1–5 progress anchors from the implementation plan. |
| `research.quality@1` | Human | Atomic criteria across six diagnostic dimensions, including critical failures. |
| `research.structure@1` | Deterministic | Nonempty output, flat JSON-object type checks, required fields, provider truncation metadata, and declared internal references. |

All seeds are uncalibrated and produce no decision-grade results. They do not
call a provider or infer semantic quality/progress. The command below runs only
the deterministic evaluator; human labeling UI, datasets, calibration, and
model-grader execution belong to later slices. Quality pass rates never become
progress scores. Optional communication criteria are excluded from the common
counts and shown in their own dimension.

## Install and seed

After the normal backup and deployment process:

```sh
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_evaluators
```

Seeding is idempotent and rejects content drift under an existing version. Publish
a new version to revise a rubric. Seeding does not grant decision-use approval.
`IDEAFLOW_EXECUTION_EVALUATORS` and `IDEAFLOW_EXECUTION_MODEL_GRADERS` default to
false. The model-grader flag reserves a separate future rollout control and has
no active provider-call path in A1/A2.

## Evaluate one existing research output

Use an operator shell with existing database/payload permissions; this is not a
new public or machine API. Choose a successful `research` or `review` run and
explicitly select this research rubric only for a research objective.

```sh
IDEAFLOW_EXECUTION_EVALUATORS=true .venv/bin/python manage.py evaluate_research \
  RUN_UUID --actor OPERATOR --idempotency-key UNIQUE_EVALUATION_KEY
```

The command loads retained protected output, verifies its SHA-256 against the
run, freezes the evaluation manifest, and prints structured diagnostics without
printing the raw report. It appends payload-access audit events but does not
change run/trace state, ideas, projections, or schedules.
Retrying the same request/key returns the existing result and its
original observations; conflicting inputs under that key are rejected.

If historical output was not captured, `--output-file /absolute/private/path`
accepts exact original bytes only when their hash matches the producing run.
It does not copy these bytes into new permanent storage or extend retention.
The manifest records an operator-file source and hash, not an invented storage
reference. Retain authorized inputs privately if subsequent reconstruction is
needed; a hash alone cannot recover missing or expired content.

Free text is the explicit default output contract. For structured research,
provide `--contract-file` containing a contract declared independently of the
candidate, for example:

```json
{
  "format": "json_object",
  "fields": {"answer": "string", "sources": "array", "count": "integer"},
  "required": ["answer", "sources"]
}
```

This is a flat object/type contract, not full JSON Schema. Supported types are
string, number, integer, boolean, object, array, and null. Required fields must
be declared and nonempty; zero and false are valid populated values. Nested
schemas, unknown contract keys, duplicate JSON keys, and nonstandard numeric
constants are rejected or fail validation. Extra output fields are allowed.
Missing provider finish metadata is insufficient evidence of truncation, not a
pass based on punctuation. A normal finish reason is not semantic completeness.

Optional `--references-file` accepts up to 100 declared internal references:

```json
[{"model": "ideas.researchentry", "id": 123}]
```

Only research entries and artifacts belonging to the run's idea are eligible.
The manifest retains timestamped validity observations without exposing another
idea's content. An empty reference list is not applicable, not proof that all
citations are sound. This command neither extracts every citation nor fetches
external URLs, and it does not assess whether a source supports a factual claim.

## Audit and rollback

Result summaries distinguish pass/fail, not applicable, and insufficient
evidence. They include coverage, critical failures, and per-dimension counts.
An empty judged denominator has no score. Operator identity is an audit label
supplied by a trusted local operator, not an authenticated web-user identity.

Completed criterion judgments must cite typed, hashed evidence covering both
the criterion's and evaluator's required inputs. Supported kinds include the
output, frozen objective/requirements, source/execution observations, output
contract, provider finish reason, and internal-reference observations. An
output reference alone cannot satisfy a source or execution-evidence requirement.
Content-bearing descriptors retain explicitly approved redacted excerpts and matching hashes;
an observed empty source/event list is distinct from unavailable evidence.
These checks validate evidence structure and availability, not the truth of
human-supplied observations.

Output descriptors accept only `kind`, `hash`, `source`, and `reference`;
inline output and extra descriptor/manifest/result-row fields are rejected.
Objective, frozen-requirements, source-evidence, and execution-evidence excerpts
require an `approval` object alongside `kind`, `hash`, and `value`:

```json
{"policy": "evaluation-excerpt-v1", "approved_by": "OPERATOR", "value_hash": "EXACT_VALUE_SHA256"}
```

The operator must review and redact the excerpt before submitting it. Approval
must match the result's actor label and exact value hash. Each excerpt is capped
at 4 KiB of JSON and each immutable record at 64 KiB. Configured credentials,
private keys, and common credential/header/signed-URL patterns are rejected
throughout immutable metadata, including reasons and rationales, without
echoing the offending content. This is defense in depth, not a universal secret
or personal-data detector. Excerpts persist with immutable audit records beyond
payload expiry, so retain only approved, minimal, nonsensitive observations;
full reports and source content belong in protected storage under its retention
policy. The general result admin displays metadata and criterion statuses,
withholding excerpt bodies and free-form rationales.

Protected output reads commit `payload.access_requested` before storage access,
then `payload.accessed` (including observed hash and verification outcome) or
`payload.access_failed`. Events share an access ID and operator label. These
commits precede the result transaction, so decoding or result-validation failure
cannot erase them. If initial auditing fails, no read occurs; if outcome auditing
fails, the committed attempt remains and evaluation stops. An unmatched request
event means the read outcome is unknown. Application-owned outer transactions
and manually disabled autocommit are rejected before reading. An idempotent
retry that returns an existing result performs no protected read or new access
event. Concurrent requests that actually read each produce their own audit pair.

Progress assessments with unavailable or non-applicable required judgments
must use a null score. Completed progress assessments still require an integer
from 1 to 5. Result validation reloads persisted evaluator/run identities and
does not trust unsaved changes to model instances supplied by a caller.

Disable `IDEAFLOW_EXECUTION_EVALUATORS` to prevent new result service writes;
retain records and read permissions. No generation rollback is required.
PostgreSQL and SQLite triggers block update/delete of the four frozen tables;
PostgreSQL also blocks truncate. Uninstalling those guards is an explicit schema
operation, not the normal rollback path. Database owners can still change schema;
these guards do not replace database access controls. Future migrations that
rebuild guarded tables must remove and reinstall their guards deliberately.

The existing execution service rejects new runs on terminal traces. A5 must
explicitly resolve how delayed child grading works without reopening completed
generation traces before enabling model graders. A1/A2 does not change that
execution lifecycle.

## Validation

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test evaluations executions ideas.tests.test_migrations --noinput
```

The tests cover exact-output attribution, idempotency, metric/rubric separation,
critical/missing states, schema checks, admin restrictions, flags, storage reads,
and immutable instance/bulk/direct-SQL writes. Run PostgreSQL integration tests
in an environment provisioned with the project's existing `vector` extension.

Transaction and migration tests use `evaluations.tests.base.AuditTransactionTestCase`.
Its fixture teardown removes the audit guards, flushes the test database, and
reinstalls the guards in one transaction. Failure rolls the whole reset back.
The helper checks the active test environment and captured database identity;
it introduces no production setting or trigger bypass. Tests remain subject
to normal audit guards outside fixture cleanup. New transaction tests touching
this database should use the same base class; ordinary `TestCase` needs no change.

Review-fix verification on 2026-09-16: all 124 focused tests passed on PostgreSQL
18 with pgvector 0.8.2, including actual row locking, concurrent idempotency, and
the existing migration suite. SQLite passed the same suite with the one
PostgreSQL-specific concurrency test skipped. The PostgreSQL cluster and
extension used for this verification were isolated under `/tmp`.
Security regressions cover credential/extra-field rejection, excerpt approval
and size limits, restricted admin inspection, durable access records after
decode/result failures, hash mismatches, storage/audit failures, and rejection
of enclosing transactions before a protected read.

## Initial production canary — 2026-09-16

`research.structure@1` evaluated retained output from research run
`9ca70c40-d0e0-4020-9ed3-e1f93bf78261` (idea 125), creating result 1.
The free-text contract produced two passes and three not-applicable checks;
this is an uncalibrated structural diagnostic, not a semantic quality score.
Output, manifest, and result hashes verified. The protected read produced a
matched request/access audit pair. Retrying reused result 1 without another
read; run, trace, and idea state remained unchanged.

`IDEAFLOW_EXECUTION_EVALUATORS=true` is now persistent in production;
`IDEAFLOW_EXECUTION_MODEL_GRADERS=false`. This enables operator evaluation
writes; it does not schedule automatic evaluations.
[Canary evidence](evidence/r5a-deterministic-canary-2026-09-16.json).
A4–A6 and broader release acceptance remain outstanding.

## A3 — Human feedback and exposure

Status: deployed at `c73c466` and feedback enabled on 2026-09-16; A3 verified and closed at user direction on 2026-09-18.

Apply migrations `evaluations.0003` and `0004`, collect static files, and restart
through the normal deployment procedure before enabling
`IDEAFLOW_EXECUTION_FEEDBACK=true`. This flag is separate from evaluator and
model-grader flags. It defaults to false and gates all interaction services,
including research edits and outcome links. Disabling it preserves authorized
history/evaluation inspection and does not change generation or scheduling.

Initial UI surfaces are the full research-entry page and weekly summaries.
Accept/reject/useful controls support an optional 1–5 usefulness rating and a
bounded reason; these ratings are human feedback, not calibrated progress
scores. The shared service also supports save, cite, action, irrelevant, and
dismiss for later integrations. Other output surfaces are not instrumented by
A3; absence of telemetry there remains unknown.

Each event records the authenticated user as an opaque ID, the business target
kind/ID, and a hash of the displayed projection. Research hashes cover topic,
focus, and context; summary hashes cover title, content, and reporting dates.
Where valid provenance exists, producing-run ID and raw output hash are stored
separately. They are not assumed to equal the projection hash. Unknown or
inconsistent provenance remains unavailable. No report body is copied into an
interaction record, and no protected payload is read by the feedback UI.
Business/user identifiers are retained without foreign keys that would block
ordinary deletion. History does not reconstruct deleted content.

A signed, actor-bound output descriptor expires after 24 hours. Services
recheck access and current output identity under a row lock before writes;
stale pages must reload. Private research interactions require the owner and
status role, or an administrator. Public ideas allow signed-in readers to give
their own feedback; edits still require owner/status or administrator access.
Weekly summaries retain the existing weekly-summary role requirement. History
is scoped to the current actor, including older output versions. Browser writes
require session authentication, POST, and CSRF. Machine bearer tokens cannot
submit or impersonate human feedback through these endpoints.

Exposure is a separate POST only after the report intersects the viewport in a
visible tab. Collapsed, offscreen, prefetched, and hidden-tab content does not
produce exposure. This is evidence of rendered visibility, not proof the whole
report was read or understood. A server-issued view-session UUID deduplicates
visibility retries; a new page view can record a later exposure. The server
cannot independently attest that a client actually looked at the page. If
JavaScript or visibility observation is unavailable, exposure remains unknown;
feedback can still be recorded without fabricating exposure.

Feedback retries are content-aware and preserve any original exposure link,
even if the visibility event arrives later. Corrections append a new record
pointing to the latest prior judgment; they never overwrite earlier feedback.
The reporting service takes an actor, exact output identity, and time window,
and derives positive, negative, mixed, exposed-without-feedback, or unknown.
It returns not-exposed only when the caller explicitly supplies independent
proof of complete instrumentation for that window. UI reporting does not make
that assumption.

“Edit research text” is an authorized real edit, not a feedback claim. Content
and the edit event commit atomically with before/after projection hashes.
No-op/stale edits are rejected; a retry returns the original edit without
rewriting the report. A failed audit write rolls back the edit. Edit facts
cannot be superseded as if they were subjective judgments. Existing legacy
admin/API editing paths are not retroactively labeled as human edits.

An optional link connects feedback to an existing `OutcomeEvent` for the same
idea and producing run. Links are immutable and idempotent; no success outcome
is generated merely because a user accepted a report. General admin inspection
withholds free-form reasons; the output page shows the actor's own history and
metadata-only producing-run evaluation diagnostics, explicitly separate from
any subsequent edits.

Three new tables (`EvaluationExposure`, `HumanFeedback`, `FeedbackOutcomeLink`)
use immutable model/queryset guards and database update/delete guards, plus
PostgreSQL truncate guards. Production rollback disables feedback writers; it
does not delete records or remove guards. The existing test-only fixture reset
now removes and restores both guard sets within its protected test transaction.

Validation commands:

```sh
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py test evaluations executions ideas.tests.test_migrations ideas.tests.test_views --noinput
node --test evaluations/tests/feedback_ui.test.cjs
```

Browser verification uses an isolated local database: research feedback saved,
real edits recorded before/after hashes, and a collapsed summary produced no
exposure until opened. JavaScript tests cover hidden tabs, zero-area content,
retry deduplication, failed exposure submissions, disabled instrumentation, and form
controls that shadow the action URL. Broader production acceptance, including the remaining R5A slices,
remains pending.

A3 verification on 2026-09-16: all 336 focused tests passed on PostgreSQL 18
with pgvector 0.8.2. SQLite passed the same suite with two PostgreSQL-only
concurrency tests skipped. All six JavaScript tests, Django system checks,
migration drift checks, and diff checks passed.

### A3 initial human canary — 2026-09-16

The user submitted Useful feedback on idea 125, research entry 531. Feedback
record 1 links to exposure 1 and producing run
`9ca70c40-d0e0-4020-9ed3-e1f93bf78261`. Actor, target, projection hash, and raw
run-output hash agree across the linked records; immutable content hashes
verified. A later page view created exposure 2. No feedback reason or report
body was retrieved for this verification.

Feedback and evaluators remain enabled; model graders remain disabled.
This verifies the initial research viewing/feedback path, not full R5A
acceptance. Summary feedback, corrections, edits, and outcome links retain
their automated/local-browser evidence; they were not exercised by this
production canary.
[Canary evidence](evidence/r5a-a3-feedback-canary-2026-09-16.json).

### A3 step 4 — production disable/restore exercise, 2026-09-17

The production feedback flag was disabled and restored with application
restarts. Direct deployed-handler checks rejected a valid authenticated
submission while disabled (403), preserved authorized history, and rendered
controls only while enabled. CSRF middleware rejected a missing-token request,
and an existing unauthorized actor was denied by the deployed handler.
Counts stayed at four feedback records and seven exposures throughout; the
original canary feedback hash remained unchanged. No synthetic judgment was
created. Evaluators remained enabled and model graders remained disabled.

An identical replay at the immutable storage-service boundary reused feedback
1, and changed content under its key was rejected.

**Step 4 completed on 2026-09-18.** A genuine post-restoration Accept submission
created feedback 5 for research entry 531, linked to exposure 12. A production
database check verified its immutable content hash, matching exposure, and a
single record for its request key. The user confirmed that replaying the
authenticated HTTP request returned HTTP 200 with `"id": 5` and
`"created": false`; the browser response was not independently observed by
the agent. Together with the disable/restore checks above, this closes step 4.
At the time of this exercise, production correction, edit, and outcome-link
checks and overall A3 closeout were tracked separately; see the closeout below.
[Exercise evidence](evidence/r5a-a3-step4-2026-09-17.json).

### A3 closeout — 2026-09-18

A3 is marked **verified and closed** at the user's explicit direction:
“Mark A3 as verified, update Production plan to current state.”
This is a user-directed acceptance decision, not a claim that the agent ran
additional production correction, edit, outcome-link, or summary checks.
Existing automated/local-browser evidence and the production canary and step 4
evidence above remain the recorded verification basis; no synthetic feedback
or new production verification results were created for this closeout.

A4 frozen datasets is next. A5 measured calibration and A6 overall production
acceptance remain pending. Evaluators and feedback remain enabled; model
graders remain disabled. A3 closeout does not close the whole R5A release.

## A4 — Frozen dataset operator workflow

Status: deployed at `9483687` on 2026-09-18; approved production canary passed
on 2026-09-18, retention setup and final readback verified on 2026-09-20. A4 adds an operator command and restricted metadata admin;
there is no public dataset endpoint or automatic production sampling.

### Access, policy, and storage

`IDEAFLOW_EXECUTION_DATASETS` defaults to false and gates dataset creation,
preview/freeze, snapshots, and content deletion. Disabling it retains authorized
sampling/export and metadata inspection. It does not change evaluator,
feedback, model-grader, experiment, or workflow-cutover flags.

Commands run only in the trusted local/server operator environment. `--user-id`
must identify an active user with `evaluations.operate_datasets`; the command is
not a remote authentication mechanism. Superusers have that permission.
Otherwise an operator can use only datasets they own, and sampling/freezing
also checks access to each research entry. Dataset policy is immutable: specify
`workflows: ["research"]`, an explicit boolean `allow_legacy`, a named redaction
policy, and positive retention days. Legacy outputs retain unavailable run
provenance instead of fabricated execution IDs. New policy means a new dataset.

Approved redacted content is stored in a separate `DatasetCaseContent` database
table, bounded to 64 KiB per payload/request. It is not raw execution payload
storage and has no public/admin content viewer. Dataset case metadata,
approval hash, revision lineage, rubric assignments, cohorts, split, snapshots,
labels, and tombstones have immutable model and database guards. Content cannot
be updated and can be deleted only after a tombstone exists. Retention expiry
immediately makes content unavailable to export; the purge command physically
removes expired bodies and records tombstones. Schedule that operator command
according to the approved dataset policy. Database backups and private export
files must follow the same access/deletion policy; deleting a database body
does not erase preexisting exports or backups. Restore procedures must reapply
required deletions before exposing restored datasets.

### 1. Create an explicit policy

After applying migrations `evaluations.0005` and `0006`, enable dataset writers
for the operator session. The following is an example policy, not approval to
sample or retain any particular production output:

```json
{
  "key": "research-pilot-v1",
  "purpose": "Research calibration convenience sample; not representative",
  "eligibility_policy": {"workflows": ["research"], "allow_legacy": true},
  "redaction_policy": "operator-reviewed-excerpts-v1",
  "retention_days": 30
}
```

```sh
IDEAFLOW_EXECUTION_DATASETS=true .venv/bin/python manage.py evaluation_dataset create \
  --user-id USER_ID --request-file /private/operator/policy.json
```

Keep working files in an operator-only directory (mode 0700). Content-bearing
command outputs use exclusive new files with mode 0600; they never overwrite a
file or print report bodies to stdout. Input files must also be protected.

### 2. Sample a bounded set and prepare redaction

Use a JSON file containing `{"entry_ids": [RESEARCH_ENTRY_ID]}`. The command
accepts 1–30 explicit IDs per call; it does not process the historical backlog.

```sh
.venv/bin/python manage.py evaluation_dataset sample \
  --user-id USER_ID --dataset-id DATASET_ID \
  --request-file /private/operator/selection.json \
  --output-file /private/operator/sample.json
```

Sampling returns the projection identity and visible topic/focus/report.
Prior state and source evidence are explicitly unavailable until an operator
supplies authorized, reviewed excerpts. A redacted projection is not claimed
to be identical to the original provider output: both origin projection hash
and raw producing-run hash, when known, are retained separately.

Prepare a proposal with exactly these fields:

- `case_key`: stable slug; corrections use the same key and create revisions.
- `origin`: exact identity returned by sampling; do not edit it.
- `payload`: `objective`, `prior_state`, `output`, `evidence`, `unavailable`,
  and `exclusions`. Text inputs are strings or null with a named unavailable
  reason. Each evidence item has `ref`, redacted `excerpt`, and its SHA-256
  hash using `canonical_hash(excerpt)`. Missing source evidence needs a reason.
- `rubric_assignments`: list of exact evaluator version descriptors, each with
  `id`, `hash`, and `rubric_key` from the seeded evaluator version.
- `cohorts`: nonempty condition tags, such as short, long, conflicting, stale,
  accepted, edited, rejected, polished-repetition, decisive, or missing-inputs.
- `split`: `development` or `held_out`; revisions retain that assignment.
- `evidence_cutoff`: an explicit timezone-aware, nonfuture timestamp.

Credential patterns are rejected, but this is not comprehensive automatic PII
redaction. The operator must review all proposed content and exclusions. The
same source cannot be introduced under a different case key in the dataset,
preventing an accidental duplicate across the two splits.

### 3. Preview, approve, and freeze

```sh
IDEAFLOW_EXECUTION_DATASETS=true .venv/bin/python manage.py evaluation_dataset preview \
  --user-id USER_ID --dataset-id DATASET_ID \
  --request-file /private/operator/proposal.json \
  --output-file /private/operator/preview.json
```

Review the exact proposal and policy in that private preview. Only after
explicit approval of its `approval_hash`, freeze it:

```sh
IDEAFLOW_EXECUTION_DATASETS=true .venv/bin/python manage.py evaluation_dataset freeze \
  --user-id USER_ID --dataset-id DATASET_ID \
  --request-file /private/operator/preview.json \
  --approve-hash APPROVED_HASH --idempotency-key CASE_REQUEST_KEY
```

The signed preview binds actor, dataset policy hash, and entire proposal; it
expires after 24 hours. A changed proposal requires another preview and
approval. Freeze rechecks access and current source identity under a row lock;
a changed source fails closed. Retrying the same key and content returns the
original record. A changed request under the same key fails. Approved revisions
retain the original source identity and split; prior snapshots remain intact.

### 4. Freeze an ordered snapshot and export

Snapshot request JSON contains `case_ids` (1–200 ordered revision IDs) and a
nonempty `sampling_rules` object recording selection and eligibility. Include
coverage/exclusions and the convenience-sample limitation. A snapshot cannot
include two revisions of one case or start with unavailable content.

```sh
IDEAFLOW_EXECUTION_DATASETS=true .venv/bin/python manage.py evaluation_dataset snapshot \
  --user-id USER_ID --dataset-id DATASET_ID \
  --request-file /private/operator/snapshot-request.json \
  --idempotency-key SNAPSHOT_REQUEST_KEY
.venv/bin/python manage.py evaluation_dataset export \
  --user-id USER_ID --snapshot-id SNAPSHOT_ID \
  --output-file /private/operator/snapshot-export.json
```

Export verifies dataset, snapshot, case, payload, rubric, and metric hashes.
It includes frozen redacted bodies and exact rubric/metric definitions. Missing,
expired, and deleted bodies have explicit status, null payload, and
`reproducible: false`; deleted cases include the tombstone. No source-row reads
or reconstruction occur. Export time/availability may change without changing
the immutable snapshot. Human labels are separate append-only records; label
collection, adjudication operations, and calibration remain A5 work.

### 5. Retention and rollback

```sh
IDEAFLOW_EXECUTION_DATASETS=true .venv/bin/python manage.py evaluation_dataset purge-expired \
  --user-id USER_ID --dataset-id DATASET_ID
IDEAFLOW_EXECUTION_DATASETS=true .venv/bin/python manage.py evaluation_dataset delete-content \
  --user-id USER_ID --case-id CASE_ID --reason required_deletion
```

These remove only protected bodies and preserve metadata, hashes, snapshot
membership, and labels. They cannot restore a deleted body. Disable dataset
writers to roll back rollout; do not reverse migrations or remove audit guards.

### A4 acceptance checklist

- Apply migrations and verify PostgreSQL audit/content guards.
- Approve one concrete redacted production case and its retention policy.
- Freeze/retry the case, create/export a snapshot, and verify hashes and exact
  rubric assignments. Record metadata-only evidence under `docs/evidence/`.
- Verify source changes do not alter frozen inputs, permissions fail closed,
  unavailable content is explicit, and rollback retains authorized reads.
- Build toward the 30-case research pilot across the planned conditions. It is
  a planning target, not evidence of statistical power or rubric calibration.

Production approval and canary verification completed; see the closeout below.

A4 local verification on 2026-09-18: the 354-test evaluations/executions/
migrations/views regression suite passed on PostgreSQL 18 with pgvector 0.8.2.
SQLite passed the 354-test suite with three PostgreSQL-only tests skipped;
a final 49-test dataset/foundation run also passed with one concurrency skip.
Django system checks, migration drift checks, and diff checks passed.
[Local verification evidence](evidence/r5a-a4-local-verification-2026-09-18.json).
At the end of local verification, production rollout and approval were still
pending. The subsequent approved production canary and closeout are recorded below.

### A4 production acceptance — completed 2026-09-20

PR #67 was merged and deployed at `9483687` on September 18, following a fresh
database backup. Migrations 0005/0006 applied successfully; the application
restarted normally. The user explicitly approved the exact canary proposal
hash and 30-day restricted retention policy before any case was frozen.

The deployed operator command created dataset **1**, case **1**, and snapshot
**1** from research entry 531. Preview/freeze/export ran through private files
that were removed after verification. Case and snapshot retries returned the
original records; changed request content was rejected. Dataset, case, payload,
snapshot, rubric, and metric hashes verified. An existing unauthorized actor
could not export. Disabling dataset writers in the operator process rejected
writes while preserving authorized exports.

A rollback-only source-row update proved that the snapshot retains its frozen
content; the original research entry, idea, and producing run were unchanged.
A simulated future clock verified explicit expiry/unavailability without
changing stored dates. PostgreSQL rejected metadata/content mutation and
content deletion without a tombstone; all 19 dataset guard triggers exist.
These probes do not claim a real source deletion or naturally elapsed expiry.

The approved body expires at **2026-10-18 23:47:05 UTC**. Access stops at expiry;
the daily `ideaflow-dataset-retention@1.timer` physically removes expired bodies
at the next run and preserves immutable metadata. Installed on September 20,
the service passed systemd unit verification and a real initial run (exit 0).
Its restricted configuration identifies operator user 2. The timer enables
writers only within its process; the global dataset flag remains false.
Evaluators and human feedback remain enabled; model graders remain disabled.

Install the checked-in `deploy/ideaflow-dataset-retention@.service` and `.timer`
under `/etc/systemd/system/`. For each approved dataset, create a root-owned
mode-0600 `/etc/ideaflow/dataset-retention-DATASET_ID.env` containing
`IDEAFLOW_DATASET_OPERATOR_ID=OWNER_USER_ID`, then reload systemd and enable
`ideaflow-dataset-retention@DATASET_ID.timer`. The configured actor must remain
active and authorized; monitor failed service runs. Disabling the timer stops
physical purge scheduling, not the expiry checks on reads.

September 20 readback confirmed one case, one snapshot, one retained body,
matching hashes, and zero human calibration labels. **A4 is verified.** The
30-case calibration pilot, independent human assessments, A5 measured grading,
and A6 overall acceptance remain outstanding. No rubric has gained
decision-grade approval from this storage canary.
[Production evidence](evidence/r5a-a4-production-canary-2026-09-18.json).
