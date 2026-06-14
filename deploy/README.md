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
provision. Works on any Docker host — an Ubuntu/Debian server, a NAS, a Raspberry Pi,
a local laptop. Files: `Dockerfile`, `docker-compose.yml`, `.env.example`.

### Image source — pull a release, or build from source

Two ways to get the image, switched entirely by `.env`:

| | `.env` settings | get the image |
|---|---|---|
| **Pull a published release** (default) | `SECRETARY_TAG=0.2` (a channel — see [Updating](#updating--manual-and-opt-in-auto-update)) | `docker compose pull` |
| **Build from source** (no registry) | `SECRETARY_TAG=local` | `docker compose build` |

Published images come from the `release` GitHub Action (on every `vX.Y.Z` tag) at
`ghcr.io/angelospk/project-secretary`. Their visibility follows the repo's — a private
repo means `docker login ghcr.io` first.

### First run (worked example: find_doctors_server)

```bash
cd deploy
cp .env.example .env
# edit .env: SECRETARY_GITHUB_TOKEN, a strong SECRETARY_SURREAL_PASS, and confirm
#            SECRETARY_GITHUB_REPOS=angelospk/find_doctors_server

docker compose pull                            # or: docker compose build (source mode)
docker compose up -d surreal secretary-run     # DB + poll loop (secretary run)
docker compose run --rm secretary-backfill     # one-time full ingest
docker compose run --rm secretary-run embed    # one-time: embed for related/QA
docker compose logs -f secretary-run           # watch cycles
```

`secretary-run` keeps the memory current after that. Stop with `docker compose down`
(the volume persists; add `-v` to wipe it).

> The poll loop (and the systemd `reconcile` timer) auto-embed new and changed
> issues/PRs each cycle, so `ask`/related stay fresh on their own. The one-time
> `embed` above is only because the bulk **backfill** is intentionally left
> embed-free — after that first pass, embedding is automatic.

### Optional console

```bash
# in .env set SECRETARY_CONSOLE_HOST=0.0.0.0, then for admin:
docker compose run --rm secretary-run console-hash   # paste hash into .env
docker compose --profile console up -d secretary-console
# → http://127.0.0.1:8088  (front it with a TLS proxy to expose; then set
#    SECRETARY_CONSOLE_HTTPS=true so the admin cookie is Secure)
```

Empty `SECRETARY_CONSOLE_PASSWORD` ⇒ viewer-only, safe to expose read-only.

### Updating — manual, and opt-in auto-update

**You choose how much updates by the tag you track** in `.env` (`SECRETARY_TAG`):

| `SECRETARY_TAG` | behaviour |
|---|---|
| `0.2.1` | exact pin — never moves |
| `0.2` | minor channel — picks up `0.2.x` patches |
| `latest` | every new release |
| `local` | build from source (no auto-update) |

**Manual update** (any time):

```bash
# pull mode:
docker compose pull && docker compose up -d
# source mode:
git -C .. fetch --tags && git -C .. checkout v0.2.1 && docker compose build && docker compose up -d
```

**Opt-in auto-update** — run Watchtower; it checks the registry hourly and pulls +
restarts only the secretary services when your tracked tag moves (SurrealDB is left
alone). Turn it on with a profile; turn it off by not running it:

```bash
docker compose --profile autoupdate up -d        # auto-update ON
docker compose stop watchtower                    # auto-update OFF
```

So a new release reaches the box automatically only if (a) you track a moving tag
(`0.2` / `latest`) **and** (b) Watchtower is running. Pin an exact tag, or don't run
Watchtower, and nothing changes until you say so.

**Why it's safe either way:** a freshly pulled image runs `ensure_schema` on its next
cycle — it applies pending migrations, and if it is somehow *older* than the database
it refuses to run rather than corrupt data (the #2 downgrade guard). Auto-update on a
moving minor channel (`0.2`) only ever moves forward within a compatible line.
