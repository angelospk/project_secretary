"""Steward orchestration: derive board actions and, in sync mode, apply them.

Cumulative trust: report/place return the actions without touching the board; only
sync writes. Status and the real Priority field are human-owned and veto-guarded; the
informational score field is the steward's own, so it is overwritten freely. Every
board write is best-effort — a failure logs and the run continues.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.steward import veto
from secretary.steward.board import BoardClient
from secretary.steward.decide import (
    desired_status_from_prs,
    next_status,
    priority_bucket,
)

log = logging.getLogger(__name__)

STATUS = "status"
SCORE = "score"
PRIORITY = "priority"
VETOED = "vetoed"


@dataclass(frozen=True)
class StewardAction:
    number: int
    item_id: str
    field: str
    value: str
    kind: str  # status | score | priority | vetoed


def run_steward(
    db: Surreal,
    board: BoardClient | None,
    settings: Settings,
    repo: str,
    *,
    ranked: list[tuple[int, float]] | None = None,
    apply: bool = False,
) -> list[StewardAction]:
    """Compute (and in sync mode apply) Status and priority actions for board items."""
    do_board = apply and settings.steward_mode == "sync" and board is not None

    rank_index: dict[int, int] = {}
    score_of: dict[int, float] = {}
    total = len(ranked or [])
    for i, (number, score) in enumerate(ranked or []):
        rank_index[number] = i
        score_of[number] = score

    actions: list[StewardAction] = []
    for item in db_repo.project_items(db, repo):
        if item.get("kind") != "issue" or item.get("number") is None:
            continue
        number = int(item["number"])
        item_id = str(item["gh_id"])
        fields = item.get("fields") or {}

        _status_action(db, board, settings, repo, number, item_id, item.get("status"),
                       do_board, actions)
        if number in rank_index:
            _priority_action(db, board, settings, repo, number, item_id, fields,
                             rank_index[number], total, score_of[number], do_board,
                             actions)
    return actions


def _status_action(db, board, settings, repo, number, item_id, current_status,
                   do_board, actions: list[StewardAction]) -> None:
    tokens = db_repo.linked_pr_status_tokens(db, repo, number)
    nxt = next_status(current_status, desired_status_from_prs(tokens))
    if nxt is None:
        return
    if not veto.can_write(db, repo, item_id, settings.status_field, current_status):
        actions.append(StewardAction(number, item_id, settings.status_field, nxt, VETOED))
        return
    actions.append(StewardAction(number, item_id, settings.status_field, nxt, STATUS))
    if do_board:
        _safe(lambda: board.set_status(item_id, nxt),
              db, repo, item_id, settings.status_field, nxt)


def _priority_action(db, board, settings, repo, number, item_id, fields,
                     index, total, score, do_board, actions: list[StewardAction]) -> None:
    if settings.steward_fill_priority:
        field = settings.priority_field
        current = fields.get(field)
        if current:  # only-when-empty; never overwrite a human's priority
            return
        bucket = priority_bucket(index, total)
        if not veto.can_write(db, repo, item_id, field, current):
            actions.append(StewardAction(number, item_id, field, bucket, VETOED))
            return
        actions.append(StewardAction(number, item_id, field, bucket, PRIORITY))
        if do_board:
            _safe(lambda: board.set_single_select(item_id, field, bucket),
                  db, repo, item_id, field, bucket)
    else:
        # The score field is the steward's own informational field — overwrite freely.
        field = settings.score_field
        value = round(score, 4)
        actions.append(StewardAction(number, item_id, field, f"{value:.4f}", SCORE))
        if do_board:
            try:
                board.set_score(item_id, field, value)
            except Exception as exc:  # noqa: BLE001 - advisory, never fatal
                log.warning("board.set_score failed for item %s: %s", item_id, exc)


def _safe(write, db, repo, item_id, field, value) -> None:
    """Run a veto-guarded board write, recording it only on success."""
    try:
        write()
    except Exception as exc:  # noqa: BLE001 - advisory, never fatal
        log.warning("board write failed for item %s field %r: %s", item_id, field, exc)
        return
    veto.record_write(db, repo, item_id, field, value)
