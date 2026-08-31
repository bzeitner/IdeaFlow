# Phase 1: Execution Ledger

Status: Implemented, not yet connected to live workflow execution

## Delivered

- Separate `executions` Django application.
- Versioned workflow definitions, model configurations, and effective-dated pricing.
- UUID execution traces and LLM attempts with parent/child call support.
- Prompt revision manifests, context manifests, payload references, and SHA-256 hashes.
- Token-class, cost, latency, provider request, validation, finish, and measurement-availability fields.
- Idempotent trace, run, and tool-invocation service boundaries.
- Append-only sequenced execution events.
- Protected, size-limited payload storage outside public media/static roots.
- Error credential redaction.
- Admin inspection with deletion disabled for audit records.
- Nullable provenance links from research, artifacts, summaries, feed outputs, graph/council outputs, repeat results, and podcast scripts.
- Seeded version-1 definitions for all current workflows and known model configurations.

## Runtime behavior

The ledger is additive. Existing scripts and APIs do not create traces yet, and existing projections remain authoritative. All rollout flags remain disabled by default.

Phase 2 will add authenticated compatibility endpoints and wrap the existing Claude/Codex shell workflows. Until then, a non-null provenance link may only be created through the internal execution services or an explicitly controlled administrative migration.

## Data rules

- Approved configuration is never modified in place after creation.
- Trace, run, event, and tool records cannot be deleted through model instances or admin.
- Retry attempts are separate runs under one trace.
- Model-graded evaluations will be child runs, not opaque helper calls.
- Partial or unavailable measurements require explicit reason codes.
- Projection attribution cannot silently replace a prior producing run.
- Idea-scoped projections must match the trace’s idea subject.
- Raw payload capture remains disabled unless the Phase 0 policy prerequisites are approved.

## Deployment

Deployment applies `executions.0001_initial`, `executions.0002_seed_phase1_workflows`, and `ideas.0059_*`. The migrations are additive and nullable; they do not rewrite existing production rows.

After migration, verify:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py showmigrations executions ideas
.venv/bin/python manage.py shell -c \
  "from executions.models import WorkflowDefinition, WorkflowVersion; print(WorkflowDefinition.objects.count(), WorkflowVersion.objects.count())"
```

Expected initial workflow counts are 14 definitions and 14 approved version-1 records.
