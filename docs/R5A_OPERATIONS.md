# R5A operator guide

Status: A1/A2 deployed to production on 2026-09-16 at `1af80f3`; initial deterministic canary verified. A3 deployed and verified at user direction on 2026-09-18. Evaluators and feedback enabled; model graders disabled.

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
