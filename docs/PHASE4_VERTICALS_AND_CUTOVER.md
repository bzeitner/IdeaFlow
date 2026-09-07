# Phase 4: vertical workflows and reversible cutover

Phase 4 finishes the measured-execution foundation around the optional workflows that create durable outputs: persona and relationship councils, weekly summaries, repository reconciliation, artifacts, and podcast production. It also adds the controls needed to move each workflow from compatibility mode to execution-ledger authority independently.

## What is measured

- Every council projection can identify both its coordinating run and the individual run that produced each vote. Legacy persona clients may omit per-vote run IDs until that workflow is cut over; the coordinating run is retained as the fallback attribution.
- Weekly summary metrics include completed execution count, success/failure count, tokens, and cost for the report period.
- Artifact uploads and external-link artifacts create immutable, checksum-addressed `ArtifactVersion` records. Replacements form a `supersedes` chain and historical uploaded files are retained.
- Podcast script creation is attributed to its LLM run. Audio rendering is a separate `DeterministicJob` whose queued, running, failed, and succeeded lifecycle follows the worker. Verified media creates an immutable version with checksum and citations.
- Repository PR reconciliation is a deterministic job linked to the originating execute/critique run when supplied.
- Observable product results are recorded as idempotent `OutcomeEvent` rows. These include council decisions, PR closure/merge, podcast script creation, verified audio, publishing, and unpublishing.

Deterministic jobs are deliberately not represented as LLM runs. This keeps token/cost and experiment analysis restricted to actual inference while preserving an end-to-end trace of non-LLM work.

## Cutover modes

Each workflow has one independently reversible mode:

- `legacy`: accept attributed and unattributed writes; the existing projection remains authoritative.
- `shadow`: same write behavior as legacy while reconciliation is observed.
- `authoritative`: reject projection writes that do not identify an execution run.
- `frozen`: reject all projection writes during a migration or incident.

Phase 4 seeds these workflows in `legacy`: `persona_council`, `relationship_council`, `weekly_summary`, `execute`, `critique`, and `podcast_script`.

Inspect readiness before every mode change:

```sh
.venv/bin/python manage.py phase4_reconcile
```

For the authoritative R4.1 audit, bound the population to the production R4
deployment time and supply independently recorded rollback-test evidence:

```sh
.venv/bin/python manage.py phase4_reconcile \
  --since 2026-09-01T00:00:00Z \
  --attribution-evidence /path/to/r4.1-attribution-evidence.json \
  --rollback-evidence /path/to/r4.1-rollback-evidence.json \
  --fail-on-issues > /path/to/r4.1-reconciliation.json
```

Irrecoverable compatibility-window outputs may be covered by reviewed
attribution evidence instead of fabricating historical runs. The evidence file
is keyed by projection type; each entry identifies the record and includes an
owner, review timestamp inside the audit window, and a specific reason:

```json
{
  "research_entries": [
    {
      "id": 452,
      "owner": "operations@example.com",
      "reviewed_at": "2026-09-07T04:00:00Z",
      "reason": "Legacy caller wrote the output before execution-run propagation was available."
    }
  ]
}
```

Evidence may only reference an in-scope record that is still unattributed; stale,
duplicate, out-of-window, or already-attributed references are rejected. Council
review outcomes are deterministic aggregates and are attributed through their
complete set of successful vote runs on one trace. Podcast media versions are
attributed to deterministic rendering jobs and are not counted as LLM projections.

The rollback evidence file is an object keyed by Phase 4 workflow. Each value
has `owner`, `tested_at`, `result` (`pass` for a successful test), and optional
`notes`. For example:

```json
{
  "podcast_script": {
    "owner": "operations@example.com",
    "tested_at": "2026-09-02T18:30:00Z",
    "result": "pass",
    "notes": "Moved authoritative to shadow, verified a legacy write, restored authoritative."
  }
}
```

The command is read-only. Its versioned JSON report includes the audit window,
an overall readiness verdict, per-workflow trace and projection coverage,
global projection attribution (including legacy unattributed writes), token,
cost, timing and unavailable-reason coverage, payload existence/hash checks,
cutover state, and rollback evidence. Projection coverage is limited to known
AI-output candidates: manual research and manually managed artifacts are not
expected to have producing runs. A producer is valid only when both its run and
trace succeeded. The audit counters and readiness inputs use the bounded audit
window; the backward-compatible `provenance` counters are explicitly labeled
all-time, and cutovers are a current configuration snapshot.

Rollback evidence is rejected when its owner is blank, its timestamp is invalid,
in the future, or older than the audit window, or its result is not `pass` or
`fail`. `--fail-on-issues` returns a non-zero exit after emitting the JSON when
the result is `warning` or `fail`; omit it while collecting an initial baseline.
The launch coverage threshold is 99.5%.

Move one workflow to shadow mode:

```sh
.venv/bin/python manage.py set_workflow_cutover podcast_script shadow \
  --reason "Observe provenance completeness before authority"
```

After reconciliation shows complete attribution and every caller has been updated, make it authoritative:

```sh
.venv/bin/python manage.py set_workflow_cutover podcast_script authoritative \
  --reason "Validated in shadow mode" --confirm
```

Rollback is an immediate mode change back to `shadow` or `legacy`; no schema rollback is required.

## Deployment

Phase 4 adds no environment variables and does not require a worker software update. Existing workers remain protocol-compatible. Deploy in this order:

1. Back up the database and media directory.
2. Pull the release and install dependencies as usual.
3. Run `.venv/bin/python manage.py migrate`.
4. Restart the web application. Restart the podcast worker only if its local checkout includes the `tools/ideaflow` CLI used for PR reconciliation; otherwise no worker restart is necessary.
5. Run `.venv/bin/python manage.py phase4_reconcile` and retain its JSON output as the pre-cutover baseline.
6. Leave all workflows in `legacy` for the first deployment. Move them individually through `shadow` and `authoritative`; do not cut over all workflows together.

Callers of `tools/ideaflow reconcile-pr` should export `IDEAFLOW_RUN_ID` or pass `--execution-run-id <uuid>`. It is optional in legacy/shadow mode and required once `execute` or `critique` is authoritative.

## Acceptance checks

- A legacy request still succeeds after migration.
- An unattributed request receives HTTP 409 when its workflow is authoritative.
- Every new podcast episode has a queued deterministic job; claim/failure/completion updates the same job.
- Successful audio upload creates one media version with the verified SHA-256 digest.
- Repeating an idempotent reconciliation does not create duplicate deterministic jobs or outcomes.
- `phase4_reconcile` reports no terminal partial/unavailable runs without an explicit measurement reason.
