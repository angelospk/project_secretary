# Deployment

Two ways to run the secretary as a service against a repo's backlog, both putting
SurrealDB + the secretary on one box with persistent storage. The target repo never
depends on the secretary's code — it is an external bot pointed at the GitHub repo.

- **Option A — systemd** (below): install on the box directly. Lightest footprint.
- **Option B — Docker Compose** ([Option B](#option-b--docker-compose)): self-contained,
  reproducible; nicest for an experiments box. Worked example: `find_doctors_server`.

Either way, since #2 the sync entry points run `ensure_schema`, so a newer build
auto-applies pending migrations and refuses an accidental downgrade.

## Option A — systemd (provider-agnostic)

The memory backbone runs as two units on any small Linux VM (1–2 GB is plenty for
this dataset):

1. **`surrealdb.service`** — SurrealDB server, persistent on-disk (`surrealkv`).
2. **`secretary-sync.timer` + `.service`** — incremental `reconcile` every 5 min.

## One-time provisioning

```bash
# 1. SurrealDB binary
curl -sSf https://install.surrealdb.com | sh

# 2. App
sudo useradd -r -s /usr/sbin/nologin secretary
sudo mkdir -p /opt/secretary /var/lib/secretary
sudo chown -R secretary: /opt/secretary /var/lib/secretary
# deploy the repo to /opt/secretary, then:
cd /opt/secretary && uv sync          # creates .venv with the `secretary` entrypoint
cp .env.example .env && $EDITOR .env  # set SECRETARY_GITHUB_TOKEN etc.

# 3. systemd
sudo cp deploy/surrealdb.service deploy/secretary-sync.service deploy/secretary-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
# SurrealDB credentials, outside the repo, readable only by root/secretary
sudo install -d -m 700 /etc/secretary
printf 'SURREAL_USER=root\nSURREAL_PASS=%s\n' "$(openssl rand -hex 16)" \
  | sudo tee /etc/secretary/surrealdb.env >/dev/null
sudo chmod 600 /etc/secretary/surrealdb.env
# keep the app's .env SECRETARY_SURREAL_USER/PASS in sync with this file

sudo systemctl enable --now surrealdb.service
sudo -u secretary /opt/secretary/.venv/bin/secretary backfill   # one-time full ingest
sudo systemctl enable --now secretary-sync.timer
```

## Realtime (later)

When webhooks are added (`sources/webhook.py`), expose an HTTPS endpoint and
register a GitHub webhook; the same ingest pipeline handles the payloads. Polling
can stay on as a safety-net reconcile.

## Option B — Docker Compose

Self-contained: SurrealDB (persistent volume) + the poll loop in one stack, console
and one-shot tools behind opt-in profiles. No surreal binary or system user to
provision. Files: `Dockerfile`, `docker-compose.yml`, `.env.example`.

### First run (worked example: find_doctors_server)

```bash
cd deploy
cp .env.example .env
# edit .env: SECRETARY_GITHUB_TOKEN, a strong SECRETARY_SURREAL_PASS, and confirm
#            SECRETARY_GITHUB_REPOS=angelospk/find_doctors_server

docker compose up -d surreal secretary-run     # DB + poll loop (secretary run)
docker compose run --rm secretary-backfill     # one-time full ingest
docker compose run --rm secretary-run embed    # one-time: embed for related/QA
docker compose logs -f secretary-run           # watch cycles
```

`secretary-run` keeps the memory current after that. Stop with `docker compose down`
(the volume persists; add `-v` to wipe it).

> The poll loop reconciles and maintains release plans, but does not re-embed every
> issue. Schedule `docker compose run --rm secretary-run embed` periodically (host cron)
> if you rely on `ask`/related staying fresh — same gap as the systemd timer.

### Optional console

```bash
# in .env set SECRETARY_CONSOLE_HOST=0.0.0.0, then for admin:
docker compose run --rm secretary-run console-hash   # paste hash into .env
docker compose --profile console up -d secretary-console
# → http://127.0.0.1:8088  (front it with a TLS proxy to expose; then set
#    SECRETARY_CONSOLE_HTTPS=true so the admin cookie is Secure)
```

Empty `SECRETARY_CONSOLE_PASSWORD` ⇒ viewer-only, safe to expose read-only.

### Updating without breaking

```bash
git -C .. fetch --tags && git -C .. checkout v0.2.0   # pin a release tag
docker compose build && docker compose up -d           # rebuild + restart
```

`secretary-run` applies pending migrations on its next cycle. If the running image is
*older* than the database, it refuses to start rather than corrupt data.
