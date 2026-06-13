# Deploying `secretary serve` (event-driven triage)

`secretary serve` is a **latency optimization**: it triages a freshly opened issue in
seconds instead of waiting for the next `secretary run` reconcile interval. It is
opt-in and safe to skip — if you don't run it, you lose latency, never functionality.
The reconcile loop remains the single source of truth.

The receiver binds `127.0.0.1` and verifies `X-Hub-Signature-256`. It does not know or
care how GitHub's POST reaches it. Pick the exposure path that fits your setup.

## Configure the webhook on GitHub

Repo (or org) **Settings → Webhooks → Add webhook**:

- **Payload URL:** the public URL your chosen exposure path gives you.
- **Content type:** `application/json`.
- **Secret:** a strong random string. Set the **same** value as `SECRETARY_WEBHOOK_SECRET`.
- **Events:** Issues, Issue comments, Pull requests (the events #7 handles).

## Settings

| Env var | Default | Meaning |
|---|---|---|
| `SECRETARY_WEBHOOK_SECRET` | _(empty)_ | HMAC secret. **Empty → serve refuses to start.** |
| `SECRETARY_WEBHOOK_HOST` | `127.0.0.1` | Bind address. |
| `SECRETARY_WEBHOOK_PORT` | `8077` | Listen port. |
| `SECRETARY_WEBHOOK_PATH` | `/webhook` | Endpoint path. |
| `SECRETARY_SERVE_TRIAGE` | `true` | `false` → ingest-only realtime (no enrich/labels on webhook). |
| `SECRETARY_SERVE_WORKERS` | `2` | Worker threads. |
| `SECRETARY_SERVE_QUEUE_MAX` | `64` | Bounded queue depth before overflow drop. |

## Exposure ladder

| Path | Friction | When |
|---|---|---|
| **smee.io** | None — outbound only, no port/IP/TLS | Get started in minutes; dev & small deployments |
| **Cloudflare Tunnel** (`cloudflared`) | Low — free, TLS, outbound | Recommended for real deployments |
| **Direct port + reverse proxy** (caddy/nginx) | High — public IP, TLS cert, firewall | Advanced / existing infra |

### smee.io (fastest start)

1. Go to https://smee.io/new, copy the channel URL, use it as the GitHub Payload URL.
2. Run the forwarder next to the secretary:
   ```bash
   npx smee-client --url https://smee.io/YOUR_CHANNEL --target http://127.0.0.1:8077/webhook
   ```
3. `secretary serve` receives the forwarded, still-signed deliveries.

### Cloudflare Tunnel (recommended)

```bash
cloudflared tunnel --url http://127.0.0.1:8077
```
Use the printed `https://<random>.trycloudflare.com/webhook` as the Payload URL (or a
named tunnel + DNS route for a stable hostname).

### Direct port + reverse proxy

Terminate TLS at caddy/nginx and `proxy_pass` to `127.0.0.1:8077`. You own the public
IP, certificate, and firewall. Only worth it if you already run this infra.

## systemd unit

Run it as its own unit alongside `secretary run` — independent restart, so if `serve`
dies the reconcile loop is untouched.

```ini
# /etc/systemd/system/secretary-serve.service
[Unit]
Description=OpenCouncil secretary webhook receiver
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/secretary
EnvironmentFile=/opt/secretary/.env
ExecStart=/opt/secretary/.venv/bin/secretary serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
