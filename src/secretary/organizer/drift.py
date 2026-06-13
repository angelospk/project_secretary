"""Change detection so plans are living, not one-shot.

A plan only needs rebuilding when its inputs move: the milestone's members (added,
removed, retitled-by-label, touched) or the config that shapes the ranking (weights,
judge, expand knobs). We fingerprint exactly those, store it per milestone in
`organizer_kv`, and short-circuit a poll cycle when the fingerprint is unchanged —
skipping the judge calls and expand queries entirely.

The fingerprint deliberately ignores *suggested adds* drift (related issues elsewhere
in the repo). Those can go stale between member changes; that's accepted staleness, not
a correctness bug — the next member change refreshes them.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.github.client import GitHubClient
from secretary.organizer import plan as organizer_plan
from secretary.organizer import writer as organizer_writer
from secretary.organizer.judge import LLMJudge
from secretary.organizer.models import Item, ReleasePlan

log = logging.getLogger(__name__)


def fingerprint_key(milestone: str) -> str:
    return f"plan_fingerprint:{milestone}"


def member_signature(members: list[Item]) -> list[list]:
    """The plan-relevant slice of each member, sorted so order can't change the hash."""
    rows = [
        [m.kind, m.number, round(m.updated_at_epoch, 3), m.milestone or "",
         m.state, sorted(m.labels)]
        for m in members
    ]
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def config_signature(settings: Settings) -> dict:
    """Config that changes the rendered plan. Judge knobs count only while it's on."""
    sig: dict = {
        "priority_weights": settings.priority_weights,
        "priority_labels": settings.priority_labels,
        "expand_threshold": settings.expand_threshold,
        "expand_max": settings.expand_max,
        "expand_include_closed": settings.expand_include_closed,
        "expand_cross_repo": settings.expand_cross_repo,
        "native_dependencies": settings.native_dependencies,
        "judge_enabled": settings.judge_enabled,
    }
    if settings.judge_enabled:
        sig["judge_model"] = settings.judge_model
        sig["judge_rubric"] = settings.judge_rubric
        sig["judge_provider"] = settings.judge_provider
    return sig


def plan_fingerprint(members: list[Item], settings: Settings) -> str:
    payload = json.dumps(
        {"members": member_signature(members), "config": config_signature(settings)},
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@dataclass
class MaintainResult:
    milestone: str
    changed: bool
    fingerprint: str
    message: str
    plan: ReleasePlan | None = None


def maintain_plan(
    db: Surreal,
    embedder: Embedder,
    client: GitHubClient | None,
    settings: Settings,
    repo: str,
    milestone: str,
    *,
    judge: LLMJudge | None = None,
    write: bool = False,
    force: bool = False,
) -> MaintainResult:
    """Rebuild a milestone's plan only when its fingerprint moved (or `force`).

    The members are loaded once here and threaded into `build`, so the short-circuit
    costs a single membership query and nothing else. On a real (`write`) cycle the new
    fingerprint is stored only after the writer returns, so a failed write retries next
    cycle instead of being silently marked done.
    """
    members = [
        Item.from_row(r)
        for r in db_repo.milestone_members(
            db, repo, milestone, include_native=settings.native_dependencies
        )
    ]
    if not members:
        return MaintainResult(milestone, False, "", f"no members in milestone {milestone!r}")

    fingerprint = plan_fingerprint(members, settings)
    stored = db_repo.kv_get(db, repo, fingerprint_key(milestone))
    if not force and stored == fingerprint:
        return MaintainResult(
            milestone, False, fingerprint,
            f"{milestone}: unchanged (fingerprint {fingerprint[:8]})",
        )

    release = organizer_plan.build(
        db, embedder, settings, repo, milestone, judge=judge, members=members
    )
    if not write:
        return MaintainResult(
            milestone, True, fingerprint, f"{milestone}: rebuilt (dry-run)", plan=release
        )

    if client is None:
        raise ValueError("maintain_plan(write=True) requires a GitHubClient")
    message = organizer_writer.write_plan(client, db, settings, release)
    db_repo.kv_set(db, repo, fingerprint_key(milestone), fingerprint)
    return MaintainResult(milestone, True, fingerprint, message, plan=release)
