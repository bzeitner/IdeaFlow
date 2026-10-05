# OpenRouter task routing implementation plan

## Objective

Allow an administrator to route one currently actionable IdeaFlow task to
OpenRouter and choose the exact OpenRouter model that must execute it. A new
local worker will be started manually, connect to the deployed IdeaFlow server,
claim only work explicitly routed to OpenRouter, and report through the existing
IdeaFlow write-back and execution-ledger paths.

The first release is deliberately opt-in:

- Existing task selection and existing Claude, Codex, and Antigravity execution
  remain the default.
- An OpenRouter route applies to one Idea/workflow pair and is consumed after
  that work succeeds.
- A stopped OpenRouter worker leaves its assigned work pending. There is no
  automatic provider or model fallback.
- The OpenRouter API key exists only on the machine running the local worker.

## Current architecture

IdeaFlow currently has four relevant mechanisms:

1. `tools/task_selection.py` computes actionable `(idea_id, workflow)` pairs.
2. `research_all.sh` claims each Idea through the server and invokes
   `research_idea.sh` with a locally configured agent.
3. `Idea.job_lease_*` fields prevent concurrent workers from running the same
   Idea.
4. The execution ledger records the actual provider and model through
   `ModelConfiguration` and `LLMRun`; `ResearchEntry` separately retains exact
   `execution_provider` and `execution_model` strings.

The existing lease remains authoritative for in-flight ownership, and the
execution ledger remains authoritative for execution history. OpenRouter
routing must not introduce a second job lifecycle.

## Design decisions

### Route override, not a new job system

Add a small persisted `TaskRouteOverride` record. It represents a human routing
decision only; it does not duplicate pending, running, failed, or succeeded
execution state.

Suggested fields:

| Field | Purpose |
| --- | --- |
| `idea` | Idea whose currently selected work is being routed |
| `workflow` | Exact workflow selected by the normal task-selection rules |
| `provider` | Initially restricted to `openrouter` |
| `model_identifier` | Exact OpenRouter model ID, for example `anthropic/claude-sonnet-4` |
| `selected_by` | Administrator who made the selection |
| `selected_at` | Audit timestamp |
| `updated_at` | Last route change timestamp |

There may be at most one route override per Idea. This matches the current
one-lease-per-Idea invariant and the selector's behavior of choosing at most one
workflow for an Idea in a pass.

Absence of an override means default routing. A successful completion consumes
the matching override. A provider failure releases the lease but retains the
override so that the same task can be retried or manually reset.

### One task-dispatch module

Create `ideas/task_dispatch.py` as the seam between task selection, route
selection, and job claiming. Its external interface should remain small:

```python
set_route(*, idea, workflow, provider, model_identifier, actor)
clear_route(*, idea, actor=None)
resolve_route(*, idea, workflow) -> EffectiveTaskRoute
validate_claim(*, idea, workflow, provider, model_identifier) -> EffectiveTaskRoute
consume_route(*, idea, workflow, provider, model_identifier)
```

The module owns these invariants:

- Default routing is represented by no database record.
- OpenRouter always requires a non-empty exact model identifier.
- A route applies only to its recorded workflow.
- A worker must identify its provider and model when claiming work.
- The server, not the worker's environment, decides the effective model.
- Only a matching successful workflow completion consumes the override.

Views, API handlers, and runners must use this interface rather than inspecting
route records directly.

### Exact-model semantics

OpenRouter supports an exact `model` identifier and also supports fallback
routing. This integration uses only the exact `model` field. It must not send a
fallback model list or request OpenRouter automatic model selection.

For the first release, the selectable models come from a server-side allowlist:

```dotenv
IDEAFLOW_OPENROUTER_MODELS=anthropic/claude-sonnet-4,openai/gpt-5.2
```

The UI shows these identifiers verbatim. Dynamic synchronization with
OpenRouter's models endpoint is deferred until the static allowlist proves
operationally burdensome.

### Provider adapter seam

Provider invocation should move behind a common local interface rather than
adding more provider-specific branches throughout the shell scripts:

```python
run_agent(*, provider, model, prompt, context) -> AgentResult
```

`AgentResult` normalizes:

- final text or structured result;
- provider request/generation identifier;
- input, output, cached, reasoning, and total tokens when available;
- finish reason;
- provider-reported cost when reliable; and
- a bounded, sanitized error.

Existing providers may remain on their current shell paths during the first
OpenRouter vertical slice. The interface must nevertheless be the test seam for
the new OpenRouter adapter so its HTTP transport is replaceable with an
in-memory fake.

## Delivery phases

### Phase 0: OpenRouter worker spike

Prove the uncertain part before adding the routing UI.

Build a one-shot local command:

```sh
./research_idea_openrouter.sh <idea-id> research --model <model-identifier>
```

The spike must:

1. Claim an Idea with the existing job-token mechanism.
2. Load the existing managed research prompt and Idea context.
3. Call OpenRouter's chat-completions endpoint with the exact model.
4. Run a bounded local tool loop.
5. Write the result through the existing effort endpoint.
6. Register and complete an execution trace and LLM run.
7. Release the Idea lease on failure and complete it on success.

Initially support only the `research` workflow. The minimum tool set is:

- read complete Idea detail;
- read bounded graph context and approved prompts;
- search and fetch web sources;
- add relevant resources or feeds;
- log one research effort; and
- update the Idea's permitted research disposition fields.

Tool names and arguments must be explicit and validated. Do not expose an
unrestricted shell tool in this phase. Limit tool iterations, response size,
wall-clock duration, and individual tool output. Treat all model-generated tool
arguments as untrusted input.

Exit criteria:

- A fake OpenRouter adapter completes a research task in an automated test.
- One manually selected low-risk research task succeeds against OpenRouter.
- The ResearchEntry and execution ledger show `openrouter` and the exact model.
- A simulated provider failure leaves no active lease.

### Phase 1: Persist route overrides

Add `TaskRouteOverride` and a migration. Register it in Django admin as
read-only audit context except for deletion/reset if useful to operators.

Add `IDEAFLOW_OPENROUTER_MODELS` to settings using the existing comma-separated
environment parsing helper. Reject a route selection when its model is not in
the allowlist.

Add unit tests for:

- creating and replacing a route;
- requiring a model for OpenRouter;
- rejecting a model outside the allowlist;
- default resolution when no override exists;
- workflow mismatch; and
- consuming only a matching route.

### Phase 2: Manual route selection UI

Extend the admin-only **Next Research Run** page with:

- a route selector containing `Default` and `OpenRouter`;
- a required model selector when OpenRouter is chosen;
- the currently stored provider and model;
- a clear/reset action; and
- a visible stale warning when the stored workflow no longer matches the
  workflow selected for the Idea.

Add one POST endpoint for setting or clearing the route. It must use Django's
normal CSRF protection and the same administrator authorization as the queue
page. After a successful change, redirect back to the queue.

The GET view continues to compute work through the existing selector, then asks
the task-dispatch module for each row's effective route. The template does not
inspect the database model directly.

### Phase 3: Route-aware claims

Extend the existing claim request with `provider` and `model` fields. Inside the
same database transaction that acquires the Idea lease:

1. Resolve the effective route for the requested workflow.
2. Reject a provider or model that does not match it.
3. Acquire the lease if it is available.
4. Return the authoritative provider and model in the claim response.

This server-side check is required even when workers filter their local work
lists. It closes the race in which a default worker computes its list just
before an administrator routes one of those tasks to OpenRouter.

Update `tools/ideaflow claim-job` to send the provider/model and expose the
authoritative values returned by the server. Existing callers identify their
current provider. For backward compatibility during deployment, requests that
omit provider/model may claim only default-routed work; they must never claim an
OpenRouter override.

Refactor lease release into one shared helper that can optionally consume a
matching route after successful completion. All successful workflow endpoints
that currently release leases must use it. Failure and explicit release paths
must retain the override.

Tests must cover:

- default worker rejected for an OpenRouter route;
- OpenRouter worker rejected for default work;
- wrong OpenRouter model rejected;
- correct provider/model accepted;
- stale precomputed work rejected after a route change;
- two workers racing for one Idea;
- successful completion consuming the route; and
- failure or explicit release retaining the route.

### Phase 4: Manually started OpenRouter batch worker

Add:

```text
research_all_openrouter.sh
tools/openrouter_agent.py
```

The wrapper supplies:

```text
IDEAFLOW_AGENT=openrouter
IDEAFLOW_AGENT_BIN=<local worker command, if overridden>
```

Required local configuration:

```dotenv
IDEAFLOW_API_BASE=https://ideaflow.bitesoftheweek.com
IDEAFLOW_API_TOKEN=...
IDEAFLOW_EXECUTION_API_TOKEN=...
OPENROUTER_API_KEY=...
```

The worker is explicitly manual; do not add a systemd unit or timer. A batch
invocation:

1. Runs local credential and connectivity preflight before claiming anything.
2. Computes the normal work list.
3. Retains only work whose effective route is OpenRouter.
4. Claims one task at a time.
5. Uses the provider/model returned by the claim response.
6. Executes the supported workflow.
7. Continues to the next task after a cleanly reported provider failure.

If the worker is not running, assigned tasks remain visible and pending. The
default batch runner must skip them.

### Phase 5: Telemetry and attribution

Extend `tools/llm_usage.py` with an OpenRouter parser or have the adapter emit
the normalized `AgentResult` schema directly. Record:

- `ModelConfiguration.provider = "openrouter"`;
- `ModelConfiguration.model_identifier` equal to the selected identifier;
- provider request/generation ID when returned;
- token counts when returned; and
- cost only when reported or deterministically computed from an approved
  pricing record.

Do not fabricate missing usage or cost. Use the ledger's existing partial or
unavailable measurement states and reasons.

The legacy `ResearchEntry.model` foreign key must use the existing generic
`other` AIModel unless the chosen OpenRouter model already has a deliberate
curated entry. Exact attribution belongs in `execution_provider`,
`execution_model`, and the execution ledger; dynamic AIModel rows must not be
created for every OpenRouter catalog entry.

### Phase 6: Expand workflow support

Enable workflows only after their required tools and output contracts have
automated coverage. Recommended order:

1. `research`
2. `review`
3. `summary`
4. ordinary `repeat`
5. `persona`
6. `execute`
7. `critique`

`execute` and `critique` remain unavailable in the route selector until the
OpenRouter worker has repository-scoped filesystem access, a command policy,
Git/GitHub operations, check inspection, and the required multi-role critique
behavior. Podcast repeat tasks remain unavailable until the structured episode
and audio-job contract is implemented.

## File-level change map

| Area | Planned changes |
| --- | --- |
| `ideas/models.py` | Add `TaskRouteOverride` unless implementation review places it in `executions`; no duplicated run status |
| `ideas/migrations/` | Create the route-override table and uniqueness constraint |
| `ideas/task_dispatch.py` | Own route invariants, claim validation, and successful consumption |
| `ideas/views.py` | Add route context to the research queue and handle route changes |
| `ideas/urls.py` | Add the admin-only route POST endpoint |
| `ideas/templates/ideas/research_queue.html` | Add provider/model controls and stale-route display |
| `ideas/api.py` | Enforce provider/model during claim and consume routes on successful completion |
| `ideaflow/settings.py` | Parse `IDEAFLOW_OPENROUTER_MODELS` |
| `tools/ideaflow` | Send route identity during claims and expose authoritative claim results |
| `tools/task_selection.py` | Keep selection provider-neutral; allow callers to filter using resolved route data |
| `research_idea_openrouter.sh` | One-shot compatibility wrapper |
| `research_all_openrouter.sh` | Manually started OpenRouter-only batch wrapper |
| `tools/openrouter_agent.py` | OpenRouter transport, tool loop, limits, and normalized result |
| `tools/llm_usage.py` | OpenRouter normalization if it is not handled inside the adapter |
| `deploy/env.production.example` | Document the server-side model allowlist only |
| `README.md`, `deploy/README.md`, `docs/agent-workflows.md` | Document route semantics, local secrets, startup, supported workflows, and recovery |

## Security requirements

- `OPENROUTER_API_KEY` is read only by the local worker and is never submitted
  to IdeaFlow, stored in execution payloads, or printed.
- The server's existing API and execution tokens remain separate from the
  OpenRouter credential.
- Manual route changes require the existing administrator role and CSRF
  protection.
- Provider responses and tool arguments are untrusted data.
- Tool calls are allowlisted, schema-validated, bounded, and logged without
  secrets.
- Provider errors are single-line, length-bounded, and passed through the
  execution ledger's existing redaction behavior.
- The exact model returned by the claim response is immutable for that attempt.
- No automatic provider fallback occurs after a failed or unavailable model.

## Failure and recovery behavior

| Condition | Required behavior |
| --- | --- |
| Local worker is stopped | Override remains; default workers skip the task |
| OpenRouter preflight fails | No task is claimed |
| Provider fails after claim | Run/trace fail, lease releases, override remains |
| Tool loop exceeds a limit | Treat as a provider execution failure; retain override |
| Model removed or unavailable | Retain override and display failure until an administrator selects another model or resets it |
| Idea workflow changes before claim | Reject workflow mismatch and show the override as stale |
| Worker dies while leased | Existing lease expiry permits later recovery; override remains |
| Successful workflow write-back | Complete telemetry, release lease, and consume the matching override |
| Administrator resets route | Delete override; the next normal selection is eligible for the default worker |

## Verification strategy

### Unit tests

- Task-dispatch interface and invariants
- Allowlist parsing and validation
- OpenRouter request construction with an exact model
- Tool argument validation and iteration limits
- Response, usage, cost, and error normalization
- Secret redaction

### Django integration tests

- Admin route selection and reset
- Non-admin denial and CSRF behavior
- Claim authorization by provider/model
- Claim race behavior
- Lease release and route consumption across every supported completion endpoint
- Stale workflow handling
- Research history displaying exact provider/model attribution

### Worker integration tests

- End-to-end research workflow using an in-memory OpenRouter adapter
- Tool-call round trip with mocked IdeaFlow and web adapters
- Provider timeout, malformed response, invalid tool arguments, and exhausted tool-loop budget
- Process interruption followed by lease-expiry recovery

### Production canary

1. Deploy schema and claim enforcement while the model allowlist is empty.
2. Update existing local workers so they identify their provider when claiming.
3. Add one inexpensive tool-capable OpenRouter model to the allowlist.
4. Route one low-risk research task through the UI.
5. Start the local OpenRouter worker manually for one pass.
6. Verify the ResearchEntry, execution trace/run, token/cost measurement state,
   lease cleanup, and route consumption.
7. Stop the worker and verify that another OpenRouter-routed task remains
   pending and cannot be taken by the default runner.
8. Enable additional workflows only after their individual canaries pass.

## Acceptance criteria

- Administrators can select OpenRouter and an allowlisted exact model for an
  actionable task, and can reset it to default.
- A default worker cannot claim OpenRouter-routed work, even from a stale local
  work list.
- An OpenRouter worker cannot claim default work or use a different model.
- The local worker can complete a research task using the current IdeaFlow
  prompt, write-back, lease, and telemetry contracts.
- Stopping the local worker does not lose, reroute, or execute assigned work.
- Successful completion consumes the override; failure retains it for retry.
- Execution history records `openrouter` and the exact selected model.
- Missing token, cost, or provider metadata is recorded as unavailable rather
  than invented.
- The OpenRouter API key never leaves the local worker.
- Existing Claude, Codex, and Antigravity workflows continue unchanged.

## Deferred work

- Dynamic synchronization with OpenRouter's model catalog
- Worker heartbeat or online/offline presence in the UI
- Multiple concurrent OpenRouter workers
- Automatic retry/backoff policy beyond the current batch behavior
- Automatic provider or model fallback
- Per-user OpenRouter credentials
- OpenRouter routing for portfolio reflection
- `execute`, `critique`, and podcast workflows before tool parity exists

## External contract references

- OpenRouter authentication:
  <https://github.com/openrouterteam/docs/blob/main/api_reference/authentication.mdx>
- OpenRouter chat-completions interface:
  <https://github.com/openrouterteam/docs/blob/main/api_reference/overview.mdx>
- OpenRouter model identifiers and metadata:
  <https://github.com/openrouterteam/docs/blob/main/guides/overview/models.mdx>
