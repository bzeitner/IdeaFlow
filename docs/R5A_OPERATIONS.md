# R5A A1/A2 operator guide

Status: A1/A2 deployed to production on 2026-09-16 at `1af80f3`; initial deterministic canary verified. Evaluators enabled; model graders disabled.

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
The remaining A3–A6 work and broader release acceptance remain outstanding.
