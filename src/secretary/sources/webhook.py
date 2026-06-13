"""Webhook change source: realtime per-item triage (subsystem #7).

The `secretary serve` receiver (`secretary.serve.server`) is the production entry
point; this `ChangeSource` subclass keeps the `sources` abstraction honest by routing
a single verified webhook payload into the same triage path. It is the boundary the
poll/webhook split promised — `run_once` (bulk dirty-set ingest) is not how webhooks
work, so it stays unsupported; `handle` does the single-item work.
"""

from __future__ import annotations

from surrealdb import Surreal

from secretary.config import Settings
from secretary.embeddings.embedder import Embedder
from secretary.github.client import GitHubClient
from secretary.ingest.reconcile import SyncReport
from secretary.serve.routing import build_task
from secretary.serve.triage import run_task
from secretary.sources.base import ChangeSource


class WebhookSource(ChangeSource):
    def __init__(self, settings: Settings, embedder: Embedder):
        self.settings = settings
        self.embedder = embedder
        self.allowed_repos = set(settings.repo_list)

    def run_once(self, db: Surreal, client: GitHubClient, repo: str) -> SyncReport:
        raise NotImplementedError(
            "WebhookSource is event-driven; use PollingSource for bulk reconcile."
        )

    def handle(self, db: Surreal, client: GitHubClient, event: str, payload: dict) -> None:
        """Route one verified webhook payload into the per-item triage path."""
        task = build_task(event, payload, self.allowed_repos)
        if task is None:
            return
        run_task(task, db, self.embedder, self.settings, client)
