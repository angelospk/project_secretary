"""The `secretary serve` HTTP receiver.

A `ThreadingHTTPServer` whose handler is deliberately thin: read body, verify the
HMAC, map (event, action) → task, enqueue, ACK. All DB/network work happens later on
the worker pool, so the ACK never waits on triage (GitHub times out deliveries at
~10s). Everything testable without a socket lives in verify.py / routing.py / triage.py.

Status codes (spec):
  bad/missing signature       -> 401
  wrong path                  -> 404
  ping                        -> 200
  unhandled (event, action)   -> 204
  accepted + enqueued         -> 202
  queue full (overflow)       -> 503  (GitHub retries; reconcile covers it regardless)
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.db.connection import surreal
from secretary.embeddings.embedder import Embedder, LocalEmbedder
from secretary.github.client import GitHubClient
from secretary.serve import triage as triage_mod
from secretary.serve.pool import WorkerPool
from secretary.serve.routing import TriageTask, build_task
from secretary.serve.verify import verify_signature

log = logging.getLogger(__name__)

# GitHub caps webhook payloads at 25 MB; reject anything larger outright.
_MAX_BODY = 25 * 1024 * 1024


class _Submittable(Protocol):
    def submit(self, item: TriageTask) -> bool: ...


def build_handler(
    settings: Settings, pool: _Submittable, allowed_repos: set[str]
) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class closed over the settings, pool, and repo allowlist."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet the default stderr access log
            return

        def _reply(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != settings.webhook_path:
                self._reply(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._reply(400)
                return
            if length < 0 or length > _MAX_BODY:
                self._reply(400)
                return
            body = self.rfile.read(length) if length else b""

            sig = self.headers.get("X-Hub-Signature-256")
            if not verify_signature(body, sig, settings.webhook_secret):
                self._reply(401)
                return

            event = self.headers.get("X-GitHub-Event", "")
            if event == "ping":
                self._reply(200)
                return

            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                self._reply(400)
                return

            task = build_task(event, payload, allowed_repos)
            if task is None:
                self._reply(204)
                return

            if pool.submit(task):
                self._reply(202)
            else:
                log.warning(
                    "triage queue full; dropping %s#%s (reconcile will catch it)",
                    task.repo, task.number,
                )
                self._reply(503)

    return Handler


def serve(settings: Settings) -> None:
    """Run the webhook receiver until SIGTERM/SIGINT, then drain and exit."""
    if not settings.webhook_secret:
        raise SystemExit(
            "SECRETARY_WEBHOOK_SECRET is empty; refusing to start an unauthenticated "
            "endpoint. Set a secret (the same one configured on the GitHub webhook)."
        )

    allowed_repos = set(settings.repo_list)
    embedder: Embedder = LocalEmbedder()

    def handle(task: TriageTask) -> None:
        # One DB connection + one GitHubClient per task — nothing shared across threads,
        # mirroring how the poll loop opens one of each per cycle.
        with surreal(settings) as db, GitHubClient(settings, repo=task.repo) as client:
            db_repo.apply_schema(db)
            triage_mod.run_task(task, db, embedder, settings, client)

    pool = WorkerPool(settings.serve_workers, settings.serve_queue_max, handle)
    pool.start()

    handler = build_handler(settings, pool, allowed_repos)
    httpd = ThreadingHTTPServer((settings.webhook_host, settings.webhook_port), handler)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    log.info(
        "serving webhooks on http://%s:%s%s for %s (triage=%s, workers=%s)",
        settings.webhook_host, settings.webhook_port, settings.webhook_path,
        ", ".join(allowed_repos), settings.serve_triage, settings.serve_workers,
    )

    stop.wait()
    log.info("shutdown signal received; draining triage pool")
    httpd.shutdown()
    pool.shutdown()
    httpd.server_close()
    log.info("serve exited cleanly")
