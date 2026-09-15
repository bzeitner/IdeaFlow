# Execution payload capture operations

Raw capture preserves the exact rendered input and provider response, separately
from user-facing projections. Stored bytes retain the existing SHA-256 execution
hash. Known configured credentials and private-key content are rejected before
storage rather than silently changing those bytes. This is not a general-purpose
PII detector: only send task-relevant content to the model in the first place.

## Enable capture

Set the server's environment:

```dotenv
IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS=true
IDEAFLOW_EXECUTION_CAPTURE_SINCE=<actual activation timestamp with UTC offset>
IDEAFLOW_EXECUTION_PAYLOAD_RETENTION_DAYS=30
IDEAFLOW_EXECUTION_PAYLOAD_ROOT=/home/ideaflow/IdeaFlow/private_execution_payloads
IDEAFLOW_EXECUTION_PAYLOAD_BACKUP_ROOT=/home/ideaflow/backups/execution-payloads
IDEAFLOW_EXECUTION_API_MAX_BYTES=67108864
```

The API envelope allows JSON escaping around the 10 MiB payload limit. Requests
are bounded even without a Content-Length header. Restart `ideaflow` after
changing server settings. The activation timestamp must reflect the actual
change, never a later timestamp chosen to exclude capture failures.

Agent clients must send payloads too. Either export
`IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS=true` in their environment or set the
following in `~/.ideaflow/client.json` for each client OS user:

```json
{"capture_payloads": true}
```

The CLI reads this file on each invocation. An explicit environment variable
takes precedence. This allows already-running schedulers to pick up capture on
their next API call. Server-side semantic inference also captures its request
and response when the server flag is enabled. The execution API rejects new
registrations/completions that omit content while capture is enabled.

## Private access

Live storage and backup directories use mode 0700; files use 0600 and belong to
the service user. Neither directory may be under MEDIA_ROOT. Payloads are not
served as public media and are not included in ordinary run API responses.

An operator principal needs the separate `execution:payload:read` scope to use:

```text
GET /api/executions/v1/runs/<uuid>/payloads/input/
GET /api/executions/v1/runs/<uuid>/payloads/output/
```

Successful reads verify the hash, emit a `payload.accessed` event containing
the principal ID and hash (no raw text), and return an attachment with
`Cache-Control: no-store`. Regular execution writers/readers have no payload-read
scope. Keep operator credentials separate from agent credentials. Expired
content returns HTTP 410, unavailable content 404, and hash mismatches 409.

## Retention, backup, and restoration

```sh
.venv/bin/python manage.py maintain_execution_payloads          # dry run
.venv/bin/python manage.py maintain_execution_payloads --apply  # apply and verify
```

Install `deploy/ideaflow-execution-payloads.service` and its timer under
`/etc/systemd/system`, run `systemctl daemon-reload`, and enable/start
`ideaflow-execution-payloads.timer`. It runs hourly. Check failures with
`systemctl --failed` and `journalctl -u ideaflow-execution-payloads`.

The job removes expired raw content from live storage and its private backup,
preserves metadata tombstones and immutable run references/hashes, and copies
only unexpired payloads. Every backup pass verifies checksums and restores the
retained backup files into a separate temporary private directory, verifying
those bytes before deleting the temporary recovery copy. Corruption, missing
retained files, or unsafe roots cause a nonzero exit. Read access expires at the
recorded deadline even before the next hourly physical-deletion pass.

Backups are a local protected mirror, not off-host disaster recovery or
encryption at rest. Do not put this directory in indefinite historical archives;
additional backup systems must honor the same content-expiry policy. PostgreSQL
backups retain references/hashes, not raw payload bytes.

## Verify activation

After a new measured provider call, verify both run references and hashes, read
the content through the scoped operator endpoint, then run maintenance and
retain its nonzero `backup_restored_verified` result. A provider call should
retain real measurement facts; do not manufacture an LLM run for a storage test.

`phase4_reconcile` reports `before_capture_enabled` separately from new missing
payloads and `expired` separately from unexpectedly missing files. Historical
uncaptured content remains unavailable; enabling capture cannot reconstruct it.
