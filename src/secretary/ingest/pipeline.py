"""Normalize → upsert → relate. Shared by every change source (polling now,
webhooks later): a source decides *which* raw objects to feed; the pipeline owns
*how* they land in SurrealDB."""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary.db import repo as db_repo
from secretary.github.client import GitHubClient
from secretary.github.crossrefs import parse_timeline
from secretary.github.models import Comment, Issue, PullRequest

log = logging.getLogger(__name__)


def is_pull(raw: dict) -> bool:
    return "pull_request" in raw


def kind_of(db: Surreal, repo: str, number: int, pr_numbers: set[int]) -> str:
    """Resolve whether a GitHub number is a PR or an issue, within `repo`.

    Numbers are unique across issues and PRs in a repo. During backfill the
    in-memory `pr_numbers` set is authoritative; otherwise fall back to the DB.
    """
    if number in pr_numbers:
        return "pr"
    return "pr" if db_repo.pr_exists(db, repo, number) else "issue"


def ingest_issue_or_pr(db: Surreal, repo: str, client: GitHubClient, raw: dict) -> str:
    """Upsert one item from the issues listing. Returns 'issue' or 'pr'."""
    if is_pull(raw):
        full = client.get_pull(raw["number"])
        pr = PullRequest.from_api(full, repo)
        db_repo.upsert_pr(db, pr)
        for issue_number in pr.linked_issues:
            db_repo.relate(
                db, ("pr", repo, pr.number), "relates_to", ("issue", repo, issue_number)
            )
        return "pr"
    db_repo.upsert_issue(db, Issue.from_api(raw, repo))
    return "issue"


def ingest_comment(db: Surreal, repo: str, raw: dict, pr_numbers: set[int]) -> None:
    comment = Comment.from_api(raw, repo)
    parent_kind = kind_of(db, repo, comment.parent_number, pr_numbers)
    db_repo.upsert_comment(db, comment, parent_kind)


def ingest_crossrefs(
    db: Surreal, repo: str, client: GitHubClient, number: int, pr_numbers: set[int]
) -> None:
    """Best-effort: fetch the item's timeline and record `mentions` edges."""
    try:
        events = client.get_timeline(number)
    except Exception as exc:  # noqa: BLE001 - cross-refs are non-critical
        log.warning("timeline fetch failed for #%s: %s", number, exc)
        return
    target_kind = kind_of(db, repo, number, pr_numbers)
    for ref in parse_timeline(number, events):
        source_kind = kind_of(db, repo, ref.source, pr_numbers)
        db_repo.relate(
            db,
            (source_kind, repo, ref.source),
            "mentions",
            (target_kind, repo, ref.target),
        )
