# Phase 2: Compatibility Instrumentation

Status: Implemented behind a disabled-by-default rollout flag

## Delivered

- Scoped execution service principals with hashed, non-recoverable tokens.
- Separate execution API authentication and `execution:read` / `execution:write` scopes.
- Idempotent HTTP endpoints for traces, runs, provider events, tools, completion, and failure.
- Request-size limits, prompt/output hashing, optional protected payload capture, and error redaction.
- `tools/ideaflow` commands for the execution API.
- A shared shell telemetry wrapper that fails open during the compatibility period.
- Compatibility instrumentation for:
  - feed scoring;
  - research, review, execute, critique, summary, repeat, and persona runs;
  - weekly summaries;
  - portfolio reflection;
  - semantic-graph embedding and relationship classification;
  - each independent provider/persona vote in relationship councils.
- Automatic run-ID propagation into existing client mutations.
- Provenance attachment for research entries, artifacts, weekly summaries, feed summaries, feed assessments, repeat results, persona reviews, podcast scripts, semantic suggestions, and council votes.
- API responses expose execution run IDs for research and feed outputs.

Semantic processing creates a trace lazily when the first provider request is made, then records embedding and classification as distinct runs. A relationship council creates one trace per suggestion and a separate evaluation run for every provider/persona vote.

## Authentication setup

Create a worker credential on the server:

```bash
.venv/bin/python manage.py create_execution_principal scheduler \
  --scope execution:write --scope execution:read
```

The command prints the token once. Store it only on the worker:

```dotenv
IDEAFLOW_EXECUTION_API_TOKEN=<one-time-token>
```

The worker still needs `IDEAFLOW_API_TOKEN` for existing idea/reporting operations. The execution credential cannot call those broader endpoints.

## Canary rollout

1. Deploy with `IDEAFLOW_EXECUTION_INSTRUMENTATION=false`.
2. Create the scoped scheduler principal.
3. Put its token on the machine running `score_items.sh`.
4. Set `IDEAFLOW_EXECUTION_INSTRUMENTATION=true` on the server and restart.
5. Run one bounded feed-scoring job.
6. Verify one trace, one run, projection provenance, event sequence, partial-measurement reason codes, and unchanged feed assessment output.
7. Expand to scheduled feed scoring.
8. Add the token to research/weekly workers and canary each workflow independently.

Do not enable `IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS`. Compatibility mode records hashes and metadata; payload capture still requires the retention/access prerequisites in `EXECUTION_DATA_POLICY.md`.

## Recovery behavior

- If trace creation fails, the existing workflow continues uninstrumented and emits a warning.
- If run creation fails after trace creation, the trace is marked failed where possible and the workflow continues.
- If provider execution fails, the run and trace are marked failed without changing the provider exit status.
- If completion reporting fails, the successful provider output is not rerun; the incomplete run remains available for reconciliation.
- Idempotency keys prevent retries from duplicating traces, attempts, and tool records.

## API surface

- `POST /api/executions/v1/traces/`
- `POST /api/executions/v1/traces/<trace>/runs/`
- `POST /api/executions/v1/traces/<trace>/complete/`
- `POST /api/executions/v1/traces/<trace>/fail/`
- `POST /api/executions/v1/runs/<run>/events/`
- `POST /api/executions/v1/runs/<run>/tools/`
- `POST /api/executions/v1/runs/<run>/complete/`
- `POST /api/executions/v1/runs/<run>/fail/`
- `POST /api/executions/v1/tools/<tool>/complete/`
- `POST /api/executions/v1/tools/<tool>/fail/`
