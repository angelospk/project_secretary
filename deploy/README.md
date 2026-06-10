# Deployment (provider-agnostic, systemd)

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
