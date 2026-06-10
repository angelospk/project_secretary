"""Normalize → upsert → relate. Shared by every change source (polling now,
webhooks later): a source decides *which* raw objects to feed; the pipeline owns
*how* they land in SurrealDB."""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary.db import repo as db_repo
from secretary.github.client import GitHubClient
from secretary.github.crossrefs import parse_timeline
from secretary.github.models import Comment, Issue, PullRequest, cross_repo_refs

log = logging.getLogger(__name__)


def is_pull(raw: dict) -> bool:
    return "pull_request" in raw


def link_cross_repo_mentions(db: Surreal) -> int:
    """Scan every indexed body for `owner/repo#N` refs and record cross-repo edges.

    Run as a final pass after all repos are ingested, so it is order-independent:
    a reference resolves whether the target was indexed before or after the source.
    The target's table is resolved (never guessed) — a reference to a not-yet-indexed
    item is skipped rather than written as a wrong-typed dangling edge. Idempotent.
    Returns the number of edges written.
    """
    count = 0
    for kind in ("issue", "pr"):
        for row in db_repo.iter_bodies(db, kind):
            for other_repo, other_num in cross_repo_refs(row.get("body"), row["repo"]):
                if db_repo.pr_exists(db, other_repo, other_num):
                    target_kind = "pr"
                elif db_repo.issue_exists(db, other_repo, other_num):
                    target_kind = "issue"
                else:
                    log.debug(
                        "skipping cross-repo mention to un-indexed %s#%s",
                        other_repo, other_num,
                    )
                    continue
                db_repo.relate(
                    db,
                    (kind, row["repo"], row["number"]),
                    "mentions",
                    (target_kind, other_repo, other_num),
                )
                count += 1
    return count


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
