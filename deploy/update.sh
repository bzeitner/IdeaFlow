#!/usr/bin/env bash
#
# Deploy the latest code. Run as the ideaflow user from the project dir:
#   ~/IdeaFlow/deploy/update.sh
#
# Pulls, installs deps, migrates, rebuilds static, and restarts the service.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ Pulling latest code"
git pull --ff-only

echo "→ Installing dependencies"
.venv/bin/pip install -r requirements.txt

echo "→ Applying migrations"
.venv/bin/python manage.py migrate --noinput

echo "→ Collecting static files"
.venv/bin/python manage.py collectstatic --noinput

echo "→ Restarting service"
sudo systemctl restart ideaflow

echo "✓ Deployed. Recent status:"
systemctl --no-pager --lines=0 status ideaflow || true
