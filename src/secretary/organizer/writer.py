"""Write a ReleasePlan to its GitHub issue as a managed, idempotent section.

The plan issue is found-or-created and keyed in `organizer_kv` by milestone, so a
renamed milestone or a cross-repo title collision can't spawn duplicates (Codex review
#9). The rendered plan goes inside the same checksummed managed block the responder
uses, so re-runs replace it in place and a human editing the block is detected and
left alone.
"""

from __future__ import annotations

import logging

import httpx
from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.github.client import GitHubClient
from secretary.organizer.models import ReleasePlan
from secretary.organizer.render import render
from secretary.responder import section

log = logging.getLogger(__name__)

_INTRO = (
    "Maintained by the secretary from this milestone's issues. Assign/unassign issues "
    "to the milestone on GitHub; everything below the marker is regenerated."
)


def _plan_key(milestone: str) -> str:
    return f"plan:{milestone}"


def _find_plan_issue(
    db: Surreal, repo: str, milestone: str, title: str, label: str
) -> int | None:
    stored = db_repo.kv_get(db, repo, _plan_key(milestone))
    if isinstance(stored, (int, float)) and not isinstance(stored, bool):
        return int(stored)
    # First run: adopt an existing plan issue (exact title + plan label) if present.
    number = db_repo.find_issue_by_title_and_label(db, repo, title, label)
    if number is not None:
        db_repo.kv_set(db, repo, _plan_key(milestone), number)
    return number


def write_plan(
    client: GitHubClient, db: Surreal, settings: Settings, plan: ReleasePlan
) -> str:
    repo = plan.repo
    title = f"Release plan: {plan.milestone}"
    label = settings.plan_issue_label
    content = render(plan)

    number = _find_plan_issue(db, repo, plan.milestone, title, label)
    body = ""
    if number is not None:
        try:
            body = client.get_issue(number).get("body") or ""
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise  # auth/network/server errors must surface, not spawn a duplicate
            # The stored plan issue was deleted on GitHub: forget it and recreate below.
            log.warning("stored plan issue #%s for %s is gone; recreating",
                        number, plan.milestone)
            number = None
        else:
            if section.was_human_edited(body):
                return f"refusing to overwrite #{number}: its managed block was edited by hand"

    if number is None:
        created = client.create_issue(title, _INTRO, labels=[label])
        number = int(created["number"])
        db_repo.kv_set(db, repo, _plan_key(plan.milestone), number)
        body = created.get("body") or _INTRO

    new_body = section.upsert(body, number, content)
    if new_body != body:
        client.update_issue_body(number, new_body)
        return f"updated release plan in #{number}"
    return f"release plan #{number} already up to date"
