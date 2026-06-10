"""Polling change source: periodic incremental reconcile via the GitHub API."""

from __future__ import annotations

from surrealdb import Surreal

from secretary.github.client import GitHubClient
from secretary.ingest import reconcile
from secretary.ingest.reconcile import SyncReport
from secretary.sources.base import ChangeSource


class PollingSource(ChangeSource):
    def run_once(self, db: Surreal, client: GitHubClient, repo: str) -> SyncReport:
        return reconcile.reconcile(db, client, repo)
