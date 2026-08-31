# Execution Telemetry Retention and Redaction Policy

Status: Phase 0 decision record
Date: 2026-08-31

## Policy

Aggregate execution metadata is retained for audit and longitudinal comparison. Raw prompts, responses, and tool payloads are treated as sensitive content and are not captured by default.

### Always record

- Trace/run identifiers, workflow key/version, subject reference, trigger, and timestamps.
- Provider and exact model configuration.
- Prompt revision identifiers and content hashes.
- Context-source identifiers, versions, inclusion decisions, and hashes.
- Token counts, normalized cost, latency, status, retries, and redacted errors.
- Evaluation, exposure, feedback, and outcome events.
- Tool names, versions, status, timing, mutation flag, and affected object identifiers.

### Protected payloads

Rendered prompts, model responses, and tool request/response bodies may contain private notes, unpublished work, source text, personal information, or credentials accidentally supplied by an external system.

- Capture is controlled by `IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS` and defaults to false.
- When enabled, payloads live in protected storage; PostgreSQL holds only references, hashes, metadata, and approved excerpts.
- Raw payload access requires an operator-level permission and produces an audit event.
- Raw payloads default to 30-day retention through `IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS`.
- Artifact versions deliberately retained by a user follow the artifact retention policy instead.

### Redaction

Before persistence or logging, redact:

- API keys, bearer tokens, cookies, passwords, private keys, and authorization headers.
- Values matching configured secret environment variables.
- Provider credentials and signed storage URLs.
- Sensitive tool output not required to reproduce the execution.

Application logs must never include raw prompts or responses by default. Command-line arguments must not carry raw payloads; use protected files or standard input.

### Deletion and audit integrity

- Deleting an idea or user-visible artifact may remove protected payload content according to policy.
- Minimal audit metadata, hashes, aggregate measurements, and tombstoned subject references remain unless a legal/privacy requirement mandates removal.
- Redaction or deletion never silently rewrites experimental aggregates; affected observations are marked invalidated and aggregates are rebuilt.

### Legacy data

Legacy research and feed records may be summarized into baseline measurements but must be labeled imported. Do not infer exact prompt revisions, latency, token classes, cost, exposure, or missing feedback.

## Review required before payload capture

Before enabling raw capture in production, approve:

- storage location and encryption;
- operator permission and access audit;
- redaction tests and secret-pattern configuration;
- retention deletion job and failure alerts;
- backup behavior for expired payloads;
- incident response for sensitive-content exposure.
