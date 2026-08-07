#!/usr/bin/env bash
#
# Back up the Postgres database, keeping the last 14 dumps. Run as the ideaflow
# user (peer auth to the local ideaflow DB). Schedule it from cron, e.g.:
#   15 3 * * *  /home/ideaflow/IdeaFlow/deploy/backup_db.sh

set -euo pipefail

BACKUP_DIR="${IDEAFLOW_BACKUP_DIR:-/home/ideaflow/backups}"
DB_NAME="${IDEAFLOW_DB_NAME:-ideaflow}"
KEEP=14

mkdir -p "$BACKUP_DIR"
stamp="$(date +%F-%H%M)"
out="$BACKUP_DIR/ideaflow-$stamp.sql.gz"

pg_dump "$DB_NAME" | gzip > "$out"
echo "Wrote $out"

# Prune all but the newest $KEEP dumps.
ls -1t "$BACKUP_DIR"/ideaflow-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
