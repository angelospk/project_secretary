"""Backfill and incremental reconcile orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from surrealdb import Surreal

from secretary.github.client import GitHubClient
from secretary.ingest import pipeline

log = logging.getLogger(__name__)

WATERMARK_KEY = "items"


@dataclass
class SyncReport:
    issues: int = 0
    prs: int = 0
    comments: int = 0
    project_items: int = 0
    touched_numbers: set[int] = field(default_factory=set)

    def __str__(self) -> str:
        return (
            f"{self.issues} issues, {self.prs} PRs, {self.comments} comments, "
            f"{self.project_items} project items, "
            f"{len(self.touched_numbers)} items checked for cross-refs"
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(ts: datetime | None) -> str | None:
    return ts.astimezone(timezone.utc).isoformat() if ts else None


def sync(db: Surreal, client: GitHubClient, repo: str, *, since: datetime | None) -> SyncReport:
    """Ingest everything in `repo` updated since `since` (None = full backfill)."""
    report = SyncReport()
    since_iso = _to_iso(since)
    pr_numbers: set[int] = set()

    # 1. Issues + PRs (the listing returns both).
    for raw in client.list_issues(since=since_iso):
        kind = pipeline.ingest_issue_or_pr(db, repo, client, raw)
        if kind == "pr":
            report.prs += 1
            pr_numbers.add(raw["number"])
        else:
            report.issues += 1
        report.touched_numbers.add(raw["number"])

    # 2. Comments across the repo.
    for raw in client.list_issue_comments(since=since_iso):
        pipeline.ingest_comment(db, repo, raw, pr_numbers)
        report.comments += 1

    # 3. Cross-references for the items we just touched (best-effort).
    for number in sorted(report.touched_numbers):
        pipeline.ingest_crossrefs(db, repo, client, number, pr_numbers)

    # 4. Projects v2 items (best-effort; GraphQL may lack scope).
    try:
        from secretary.github.projects import ingest_projects

        report.project_items = ingest_projects(db, repo, client)
    except Exception as exc:  # noqa: BLE001 - projects are non-critical
        log.warning("project ingestion skipped: %s", exc)

    # 5. Native dependencies / sub-issues (opt-in; same GraphQL budget as projects).
    from secretary.config import get_settings

    if get_settings().native_dependencies:
        try:
            from secretary.github.native import ingest_native

            edges = ingest_native(db, repo, client)
            log.info("native edges ingested for %s: %d", repo, edges)
        except Exception as exc:  # noqa: BLE001 - native edges are non-critical
            log.warning("native dependency ingestion skipped: %s", exc)

    return report


def backfill(db: Surreal, client: GitHubClient, repo: str) -> SyncReport:
    from secretary.db import repo as db_repo

    started = _now()
    report = sync(db, client, repo, since=None)
    db_repo.set_watermark(db, repo, WATERMARK_KEY, started)
    log.info("backfill complete for %s: %s", repo, report)
    return report


def reconcile(db: Surreal, client: GitHubClient, repo: str) -> SyncReport:
    from secretary.db import repo as db_repo

    started = _now()
    since = db_repo.get_watermark(db, repo, WATERMARK_KEY)
    report = sync(db, client, repo, since=since)
    db_repo.set_watermark(db, repo, WATERMARK_KEY, started)
    log.info("reconcile %s (since=%s) complete: %s", repo, _to_iso(since), report)
    return report
