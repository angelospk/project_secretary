"""Webhook change source — stub.

Activated in a later iteration. It will receive GitHub `issues` / `issue_comment`
/ `pull_request` / `projects_v2_item` webhook payloads and feed the SAME ingest
pipeline used by polling, so realtime updates drop in without reworking ingestion.

Sketch of the eventual flow::

    payload = verify_signature(request)          # X-Hub-Signature-256
    match payload["action"], event_type:
        issues / pull_request -> pipeline.ingest_issue_or_pr(db, client, obj)
        issue_comment         -> pipeline.ingest_comment(db, comment_raw, pr_numbers)
        ...

For now it exists only to lock the interface boundary.
"""

from __future__ import annotations

from surrealdb import Surreal

from secretary.github.client import GitHubClient
from secretary.ingest.reconcile import SyncReport
from secretary.sources.base import ChangeSource


class WebhookSource(ChangeSource):
    def run_once(self, db: Surreal, client: GitHubClient) -> SyncReport:
        raise NotImplementedError(
            "WebhookSource is a planned realtime source; use PollingSource for now."
        )

    def handle(self, db: Surreal, client: GitHubClient, event: str, payload: dict) -> None:
        raise NotImplementedError("webhook ingestion not implemented yet")
