"""Digest orchestration: window from the watermark, write the section + Discord, advance.

The window runs from the last digest watermark (or `interval_days` back on first run) to
`now`. After a successful write the watermark advances to `now` and the current release-
plan fingerprints are snapshotted, so the next digest reports only fresh drift. An empty
window writes nothing — re-running immediately is a no-op.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.github.client import GitHubClient
from secretary.organizer.drift import fingerprint_key
from secretary.reporter.digest import DigestData, build_digest, render
from secretary.reporter.discord import discord_payload, post_discord
from secretary.responder import section

log = logging.getLogger(__name__)

DIGEST_WATERMARK = "digest"
_DIGEST_ISSUE_KEY = "digest_issue"
_SNAPSHOT_KEY = "digest_plan_fingerprints"
_INTRO = (
    "Secretary digest. Everything below the marker is regenerated each cycle — a "
    "read-only summary of what moved and what the secretary noticed."
)


def run_digest(
    db: Surreal,
    embedder: Embedder,
    client: GitHubClient | None,
    settings: Settings,
    repo: str,
    *,
    now: datetime,
    since: datetime | None = None,
    apply: bool = False,
) -> tuple[DigestData, str]:
    """Build the digest and (when apply) write the section + Discord, then advance state."""
    if since is None:
        since = db_repo.get_watermark(db, repo, DIGEST_WATERMARK) or (
            now - timedelta(days=settings.digest_interval_days)
        )
    data = build_digest(db, embedder, settings, repo, since=since, now=now)
    body = render(data)

    if not apply:
        return data, body
    if data.is_empty:
        _advance(db, settings, repo, now)  # nothing to report; just move the window
        return data, body

    if client is not None:
        _write_section(client, db, settings, repo, body)
    if settings.digest_discord_webhook:
        post_discord(
            settings.digest_discord_webhook,
            discord_payload(settings.digest_issue_title, body),
        )
    _advance(db, settings, repo, now)
    return data, body


def _advance(db: Surreal, settings: Settings, repo: str, now: datetime) -> None:
    db_repo.set_watermark(db, repo, DIGEST_WATERMARK, now)
    snapshot = {
        m: db_repo.kv_get(db, repo, fingerprint_key(m))
        for m in settings.plan_milestone_list
    }
    snapshot = {m: fp for m, fp in snapshot.items() if fp is not None}
    db_repo.kv_set(db, repo, _SNAPSHOT_KEY, snapshot)


def _write_section(
    client: GitHubClient, db: Surreal, settings: Settings, repo: str, content: str
) -> None:
    title = settings.digest_issue_title
    stored = db_repo.kv_get(db, repo, _DIGEST_ISSUE_KEY)
    number = int(stored) if isinstance(stored, (int, float)) and not isinstance(stored, bool) else None

    if number is None:
        created = client.create_issue(title, _INTRO, labels=[settings.plan_issue_label])
        number = int(created["number"])
        db_repo.kv_set(db, repo, _DIGEST_ISSUE_KEY, number)
        body = created.get("body") or _INTRO
    else:
        body = client.get_issue(number).get("body") or ""
        if section.was_human_edited(body):
            log.warning("digest issue #%s edited by hand; leaving it alone", number)
            return

    new_body = section.upsert(body, number, content)
    if new_body != body:
        client.update_issue_body(number, new_body)
