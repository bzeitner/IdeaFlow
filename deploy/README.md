# Deploying IdeaFlow to DigitalOcean (ideaflow.bitesoftheweek.com)

A start-to-finish runbook: one $12 droplet running Django (gunicorn) + nginx +
Postgres, fronted by Cloudflare for DNS and TLS. The app is served at
**ideaflow.bitesoftheweek.com** (a subdomain of your base bitesoftheweek.com).
Follow the steps in order. Config files referenced here live in this `deploy/`
folder.

Conventions: the server app user is **`ideaflow`**, code lives at
**`/home/ideaflow/IdeaFlow`**, the virtualenv at **`.venv`** inside it.
Commands prefixed `#` run as root/sudo; `$` run as the `ideaflow` user.

---

## 0. Before you start

- A DigitalOcean account and an SSH key you can use.
- The domain **bitesoftheweek.com** registered (any registrar).
- A **Cloudflare** account (free plan is fine).
- A **Google OAuth client** — you'll create/point it at the prod domain in step 11.

---

## 1. Create the droplet

1. DigitalOcean → **Create → Droplets**.
2. Image: **Ubuntu 24.04 LTS**. Plan: **Basic → Regular → $12/mo (2 GB / 1 vCPU)**.
3. Choose a region near you, add your **SSH key**, name it `ideaflow`.
4. Create, then note the **public IPv4** (call it `SERVER_IP`).

SSH in as root:

```bash
ssh root@SERVER_IP
```

Base hardening + firewall:

```bash
# as root
apt update && apt -y upgrade
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
timedatectl set-timezone UTC
```

The host and PostgreSQL remain on UTC for predictable storage and operations.
Django renders user-facing timestamps in `America/Los_Angeles` (PST/PDT).

---

## 2. Create the app user

```bash
# as root
adduser --disabled-password --gecos "" ideaflow
usermod -aG sudo ideaflow
# copy your SSH key so you can log in as ideaflow
rsync --archive --chown=ideaflow:ideaflow ~/.ssh /home/ideaflow/
```

From now on, log in as the app user: `ssh ideaflow@SERVER_IP`.

---

## 3. Install system packages

```bash
# as root (or with sudo)
apt -y install python3-venv python3-dev build-essential \
    postgresql postgresql-contrib \
    nginx git curl
```

---

## 4. Postgres: database + user

```bash
# as root
sudo -u postgres psql <<'SQL'
CREATE DATABASE ideaflow;
CREATE USER ideaflow WITH PASSWORD 'CHANGE_ME_STRONG';
ALTER ROLE ideaflow SET client_encoding TO 'utf8';
ALTER ROLE ideaflow SET default_transaction_isolation TO 'read committed';
ALTER ROLE ideaflow SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE ideaflow TO ideaflow;
\c ideaflow
GRANT ALL ON SCHEMA public TO ideaflow;
SQL
```

Use a strong password and remember it — it goes in `DATABASE_URL`.

Ubuntu's default `pg_hba.conf` already ships `local all all peer`, so the OS user
`ideaflow` can connect to the `ideaflow` database as the `ideaflow` role with no
password — which is what `backup_db.sh` relies on. Nothing to configure; just
confirm it works:

```bash
# as ideaflow — connects via peer auth, no password prompt
psql -d ideaflow -c '\conninfo'
```

The app itself connects over TCP with the password (via `DATABASE_URL`), which
Ubuntu's default `host ... 127.0.0.1/32 scram-sha-256` rule already allows.

---

## 5. Give the server read-only access to the repo (deploy key)

So `git pull` works on the server without your personal credentials, add a
**read-only deploy key** — an SSH key whose public half is registered on just
this one GitHub repo.

```bash
# as ideaflow
ssh-keygen -t ed25519 -C "ideaflow-deploy" -f ~/.ssh/ideaflow_deploy -N ""
cat ~/.ssh/ideaflow_deploy.pub
```

Copy that public key, then in GitHub: **repo → Settings → Deploy keys → Add
deploy key** — paste it, title it `droplet`, leave **Allow write access
unchecked**. Tell SSH to use this key for GitHub, and trust the host:

```bash
# as ideaflow
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/ideaflow_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com   # expect "Hi bzeitner/IdeaFlow! You've successfully authenticated"
```

## 6. Clone the code and build the environment

```bash
# as ideaflow
cd ~
git clone git@github.com:bzeitner/IdeaFlow.git   # SSH URL — uses the deploy key
cd IdeaFlow
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create the production `.env` from the template and fill it in:

```bash
cp deploy/env.production.example .env
.venv/bin/python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# paste that into DJANGO_SECRET_KEY, set DATABASE_URL's password, etc.
nano .env
chmod 600 .env   # it holds the secret key, DB password, Google secret, API token
```

`.env` must have at least: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`,
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and `DATABASE_URL`.
Google keys come in step 11.

---

## 7. Initialize the app

```bash
# as ideaflow, in ~/IdeaFlow
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser   # optional; Google login also works

# Set the Sites-framework domain so callbacks/emails use the real host
.venv/bin/python manage.py shell --no-imports -c \
  "from django.contrib.sites.models import Site; s=Site.objects.get(id=1); s.domain='ideaflow.bitesoftheweek.com'; s.name='IdeaFlow'; s.save()"
```

Quick smoke test (bind to localhost, Ctrl-C when done):

```bash
.venv/bin/gunicorn ideaflow.wsgi:application --bind 127.0.0.1:8000
# in another shell: curl -I http://127.0.0.1:8000/  → expect 200
```

---

## 8. Run it as a service (gunicorn + systemd)

```bash
# as root
cp /home/ideaflow/IdeaFlow/deploy/ideaflow.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ideaflow
systemctl status ideaflow --no-pager      # should be active (running)
```

To generate reviewable knowledge-graph relationships whenever idea research
changes, first enable pgvector in the `ideaflow` database and configure the
`IDEAFLOW_SEMANTIC_*` values in `.env`. Then install the worker timer:

```bash
# as root
cp /home/ideaflow/IdeaFlow/deploy/ideaflow-semantic-graph.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ideaflow-semantic-graph.timer
```

The web application only marks changed ideas stale. This timer performs the
external API calls and places suggestions in the Graph tab for human review.
Backfill existing ideas once after deployment:

```bash
# as ideaflow, in ~/IdeaFlow
.venv/bin/python manage.py process_semantic_graph --all --limit 100000
```

If it fails, `journalctl -u ideaflow -n 50 --no-pager` shows why (almost always
a `.env` value or DB password).

---

## 9. nginx reverse proxy + Cloudflare origin cert

First get an origin certificate from Cloudflare (valid 15 years, no renewals):

1. Cloudflare dashboard → your site → **SSL/TLS → Origin Server → Create
   Certificate**. Accept defaults (covers `bitesoftheweek.com` and
   `*.bitesoftheweek.com`).
2. Copy the **certificate** and **private key** onto the server:

```bash
# as root
mkdir -p /etc/ssl/cloudflare
nano /etc/ssl/cloudflare/bitesoftheweek.com.pem   # paste the certificate
nano /etc/ssl/cloudflare/bitesoftheweek.com.key   # paste the private key
chmod 600 /etc/ssl/cloudflare/bitesoftheweek.com.key
```

Install the site config:

```bash
# as root
cp /home/ideaflow/IdeaFlow/deploy/nginx.conf /etc/nginx/sites-available/ideaflow
ln -sf /etc/nginx/sites-available/ideaflow /etc/nginx/sites-enabled/ideaflow
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

### Install the isolated Graph Lab

Graph Lab is a static, pinned Gephi Lite build on
`graph-lab.bitesoftheweek.com`. It deliberately has a separate browser origin:
it receives neither the IdeaFlow session cookie nor the long-lived agent API
token. IdeaFlow grants an authenticated user with the Knowledge Graph role a
short-lived, read-only capability and accepts it only in an Authorization
header from the exact Graph Lab origin.

Add a proxied Cloudflare `A` record for `graph-lab` pointing to the same droplet.
The wildcard origin certificate created above already covers this hostname.
Build the reviewed upstream revision on a trusted build machine with Node.js
and npm installed:

```bash
./deploy/build_graph_lab.sh ./dist
scp dist/gephi-lite-1.0.2.tar.gz* ideaflow@SERVER_IP:/home/ideaflow/
```

The build script checks out the pinned tag and verifies commit
`d47ecb459a00e2942ee0c2b8d6630015124b9ff4` before using the upstream lockfile.
On the droplet, install the second Nginx site and the checksummed release:

```bash
# as root
cp /home/ideaflow/IdeaFlow/deploy/nginx-graph-lab.conf /etc/nginx/sites-available/ideaflow-graph-lab
ln -sfn /etc/nginx/sites-available/ideaflow-graph-lab /etc/nginx/sites-enabled/ideaflow-graph-lab
/home/ideaflow/IdeaFlow/deploy/install_graph_lab.sh \
  /home/ideaflow/gephi-lite-1.0.2.tar.gz \
  /home/ideaflow/gephi-lite-1.0.2.tar.gz.sha256 \
  1.0.2
```

Run the normal `deploy/update.sh` before enabling Graph Lab. It applies the
`GraphAccessCapability` migration, runs `collectstatic`, and restarts Django.
Then set these values in `/home/ideaflow/IdeaFlow/.env` and restart:

```dotenv
IDEAFLOW_GRAPH_LAB_ENABLED=true
IDEAFLOW_GRAPH_LAB_ORIGIN=https://graph-lab.bitesoftheweek.com
```

```bash
sudo systemctl restart ideaflow
curl -I https://graph-lab.bitesoftheweek.com/
curl -I https://ideaflow.bitesoftheweek.com/graph-lab/
```

Sign in, grant the Knowledge Graph role, open Graph → Open Graph Lab, and load
the default graph. Browser developer tools should show the GraphML request with
an Authorization header, no capability in its URL, no IdeaFlow cookie on the
Graph Lab origin, and an exact (never wildcard) CORS response. The GraphML
endpoint is read-only and bounded by the `IDEAFLOW_GRAPH_EXPORT_MAX_*` settings.

Operational controls:

- Revoke every live browser capability after an incident or role-policy change:
  `.venv/bin/python manage.py revoke_graph_capabilities --all`.
- Nginx's default access log records the export path but not Authorization
  headers; do not add `$http_authorization` to either site's log format.
- To roll back the static UI, repoint
  `/var/www/ideaflow-graph-lab/current` to a prior immutable release, run
  `nginx -t`, and reload Nginx. Disable `IDEAFLOW_GRAPH_LAB_ENABLED` for an
  immediate application-side kill switch.
- Review upstream changes and update the pinned tag, commit, attribution, and
  CSP only together. Never deploy an unchecksummed archive.
- Gephi Lite 1.0.2 generates runtime validators with `new Function`, so
  `unsafe-eval` is intentionally confined to the isolated Graph Lab origin.
  Do not add external script origins or copy this policy to IdeaFlow.
- Gephi's forms are permitted only within the isolated same origin; external
  form destinations remain blocked by `form-action 'self'`.

> **Alternative (no Cloudflare proxy):** if you'd rather use Let's Encrypt, grey-cloud
> the DNS record first, run `apt install certbot python3-certbot-nginx && certbot --nginx`,
> then set Cloudflare SSL mode to Full (strict). The Cloudflare origin cert path above is
> simpler and avoids renewals.

---

## 10. Cloudflare DNS + TLS mode ("cloudify")

1. Cloudflare → **Add a site** → `bitesoftheweek.com` → Free plan.
2. Cloudflare gives you two **nameservers**. At your **domain registrar**, replace
   the registrar's nameservers with Cloudflare's. (Propagation: minutes to a few hours.)
3. Back in Cloudflare → **DNS → Records**, add:
   | Type | Name | Content | Proxy |
   |------|------|---------|-------|
   | A | `ideaflow` | `SERVER_IP` | Proxied (orange) |
4. **SSL/TLS → Overview → Full (strict)** — this validates the origin cert from step 9.
5. **SSL/TLS → Edge Certificates → Always Use HTTPS: On.**

Verify once DNS propagates:

```bash
curl -I https://ideaflow.bitesoftheweek.com/     # 200, and the landing page
```

**Lock the origin to Cloudflare (recommended).** Until you do this, the droplet
IP still serves the app directly, letting anyone who finds it bypass
Cloudflare's WAF, rate-limiting, and bot protection. Restrict the web ports to
Cloudflare's edge ranges (SSH stays open):

```bash
# as root
ufw delete allow 'Nginx Full'
for ip in $(curl -s https://www.cloudflare.com/ips-v4) $(curl -s https://www.cloudflare.com/ips-v6); do
  ufw allow from "$ip" to any port 80,443 proto tcp
done
ufw reload
```

Keep the DNS records **Proxied** (orange) so traffic actually arrives via
Cloudflare, and re-run the loop if Cloudflare ever updates its ranges (rare).

---

## 11. Google OAuth for the production domain

In [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services
→ Credentials → your OAuth client** (or create a new Web application):

- **Authorized redirect URI:**
  `https://ideaflow.bitesoftheweek.com/accounts/google/login/callback/`
- (Add the `www` variant too if you'll use it.)

Put the client ID/secret in `.env`, then restart:

```bash
# as ideaflow
nano ~/IdeaFlow/.env         # GOOGLE_OAUTH_CLIENT_ID / _SECRET
sudo systemctl restart ideaflow
```

Sign in at https://ideaflow.bitesoftheweek.com/ — `bzeitner@gmail.com` is auto-granted
every role on first sign-in.

---

## 12. Scheduled feeds + database backups

Feeds (hourly, idempotent):

```bash
# as root
cp /home/ideaflow/IdeaFlow/deploy/ideaflow-refresh-feeds.service /etc/systemd/system/
cp /home/ideaflow/IdeaFlow/deploy/ideaflow-refresh-feeds.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ideaflow-refresh-feeds.timer
systemctl list-timers ideaflow-refresh-feeds.timer --no-pager
```

Nightly DB backup (kept 14 days):

```bash
# as ideaflow
chmod +x ~/IdeaFlow/deploy/backup_db.sh
( crontab -l 2>/dev/null; echo "15 3 * * * /home/ideaflow/IdeaFlow/deploy/backup_db.sh" ) | crontab -
~/IdeaFlow/deploy/backup_db.sh    # test it now; look in ~/backups
```

DigitalOcean weekly droplet backups (in the DO panel) are a good extra layer.

---

## 13. Day-to-day: deploying updates

Everything is a git pull away. From the server:

```bash
# as ideaflow
~/IdeaFlow/deploy/update.sh
```

That pulls, installs deps, migrates, rebuilds static, and restarts the service.
Make it executable once: `chmod +x ~/IdeaFlow/deploy/update.sh`.

Management commands run through the venv, e.g.:

```bash
cd ~/IdeaFlow
.venv/bin/python manage.py add_feed --url https://example.com/feed.xml
.venv/bin/python manage.py refresh_feeds
.venv/bin/python manage.py dump_idea 3
```

The research agents (`research_idea.sh`, `/research-idea`) are meant to run from
your **laptop**, not the droplet — keep the server sized for app + DB only. Point
them at the server's HTTP API by setting `IDEAFLOW_API_TOKEN` on both ends.

---

## 14. Running agents from another machine

The research agents (`research_idea.sh`, `research_all.sh`, `/research-idea`)
run on your laptop or any box — not the droplet. They talk to the deployed hub
over its HTTP API through `tools/ideaflow`, so a remote machine needs two things:

**1. The agent files.** Clone the repo (a read-only clone is fine):

```bash
git clone git@github.com:bzeitner/IdeaFlow.git   # or the https URL, if you have access
cd IdeaFlow
```

That gives you `tools/ideaflow` (a standalone, dependency-free Python client),
`research_idea.sh`, `research_all.sh`, and the `/research-idea` command. Only
`python3` and the `claude` CLI are needed on that machine — no venv, no database.

**2. API access.** Turn the API on by setting `IDEAFLOW_API_TOKEN` in the
server's `.env` (step 6), then give the same token to the remote agent:

```bash
export IDEAFLOW_API_BASE=https://ideaflow.bitesoftheweek.com   # already the default
export IDEAFLOW_API_TOKEN=<the token from the server .env>
```

Then the whole loop works remotely:

```bash
./tools/ideaflow list-ideas
./tools/ideaflow dump-idea 3
./research_idea.sh 3                       # research + report an idea
./tools/ideaflow feed-items --unsummarized # the ingest agent's work queue
```

`IDEAFLOW_API_BASE` defaults to the production URL, so in practice you only need
to export the token.

---

## 15. Troubleshooting

| Symptom | Check |
|---|---|
| 502 Bad Gateway | `systemctl status ideaflow`, `journalctl -u ideaflow -n 50` — app not running |
| 400 Bad Request | `DJANGO_ALLOWED_HOSTS` missing the domain |
| CSRF "origin doesn't match" on login | `DJANGO_CSRF_TRUSTED_ORIGINS` must list `https://ideaflow.bitesoftheweek.com` |
| Admin/CSS unstyled | re-run `collectstatic`; confirm WhiteNoise middleware present |
| Redirect loop | Cloudflare SSL mode must be **Full (strict)**, not Flexible |
| Google login "redirect_uri_mismatch" | the callback URL in Google must match exactly, https + trailing slash |

Logs: `journalctl -u ideaflow -f` (app), `/var/log/nginx/error.log` (proxy).
