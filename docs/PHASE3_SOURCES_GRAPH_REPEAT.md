# Phase 3: Sources, Graph, and Repeat Work

Status: implemented behind `IDEAFLOW_SOURCES_PHASE3_ENABLED=false`

## Delivered

- A source/evidence domain alongside the legacy feed tables:
  - `Source`, `Subscription`, and `SourceItem`;
  - `EvidenceCandidate` and append-only `EvidenceAction`;
  - sticky `EvidenceAssignment` and raw `EvidenceObservation` records.
- An idempotent legacy importer for feeds, idea subscriptions, feed items,
  assessments, relationships, relationship suggestions/council decisions, and
  repeat results.
- Immutable checksummed snapshots for graph/repeat history and its original
  run/provenance identifiers.
- A hard backlog boundary: every imported feed item has
  `eligible_for_processing=false` and can never enroll in a Phase 3 ranking
  experiment.
- Dual-write of genuinely new feed ingress into source items and candidates.
- Deterministic, salted, sticky assignment for one bounded evidence-ranking
  experiment at a time.
- Control scoring from the subscription relevance prior and treatment scoring
  from deterministic title/summary overlap plus that prior.
- Phase 3 queue reads expose only new eligible candidates when the rollout flag
  is enabled. Exposure time and subsequent legacy feed assessments become raw
  experiment observations.
- Admin inspection and commands for import, experiment start, and status.

No historical source item is sent to an LLM by this phase. `llm_score` and
`scoring_run` exist for later gateway ranking, but Phase 3 begins with a
zero-cost deterministic prefilter experiment.

## Deployment and import

1. Back up PostgreSQL.
2. Deploy and migrate with both flags off:

   ```dotenv
   IDEAFLOW_SOURCES_PHASE3_ENABLED=false
   IDEAFLOW_EXECUTION_EXPERIMENTS=false
   ```

3. Preview legacy counts:

   ```bash
   .venv/bin/python manage.py import_phase3_legacy --dry-run
   ```

4. Run the idempotent import:

   ```bash
   .venv/bin/python manage.py import_phase3_legacy
   .venv/bin/python manage.py phase3_status
   ```

5. Confirm `historical_ineligible` matches the imported feed-item count and
   `new_eligible` is zero. Sample source, subscription, feedback, graph, and
   repeat snapshots in admin.
6. Set `IDEAFLOW_SOURCES_PHASE3_ENABLED=true`, restart the web application,
   and run one bounded feed refresh. Confirm only the new item appears under
   `new_eligible` and receives candidates.
7. Keep experiments off until the new-item dual-write has been observed.

## Starting the first experiment

Enable experiment enrollment and restart:

```dotenv
IDEAFLOW_EXECUTION_EXPERIMENTS=true
```

Create the declared experiment:

```bash
.venv/bin/python manage.py start_evidence_experiment evidence-ranking-v1 \
  --hypothesis "Deterministic semantic overlap improves useful evidence precision" \
  --primary-metric useful \
  --treatment-percent 50 \
  --minimum-sample-size 100
```

Only items ingested after the command's timestamp are assigned. Assignment is
stable across retries. Existing assignments never change if allocation is
later adjusted.

Inspect raw allocation and observations with:

```bash
.venv/bin/python manage.py phase3_status
```

Experiment records are evidence, not an automatic decision system. Phase 3
does not automatically promote a treatment.

## Rollback

Set `IDEAFLOW_SOURCES_PHASE3_ENABLED=false` and restart. Legacy feed reads and
writes immediately become authoritative again. Pause any running evidence
experiment in admin. Retain Phase 3 import, assignment, exposure, and
observation records for audit; no destructive migration rollback is required.
