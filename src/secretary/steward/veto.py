"""Per-field human veto — the one invariant the steward never violates.

For every (project item, field) the steward writes, it records the value it set. Before
any later write it re-reads the current value: if it still matches what we set, updating
is safe; if a human changed it, that pair is vetoed permanently and the steward never
touches it again. Same philosophy as the managed-section checksum, applied field-wise.
Without this guard, no write should ship.
"""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary.db import repo as db_repo

log = logging.getLogger(__name__)


def _set_key(item_id: str, field: str) -> str:
    return f"steward_set:{item_id}:{field}"


def _veto_key(item_id: str, field: str) -> str:
    return f"steward_veto:{item_id}:{field}"


def can_write(
    db: Surreal, repo: str, item_id: str, field: str, current_value: object
) -> bool:
    """Whether the steward may write `field` on `item_id`.

    False if the pair is vetoed, or if the current board value differs from what the
    steward last set (a human edited it) — in which case the veto is recorded now so it
    survives even if the set-record is later lost.
    """
    if db_repo.kv_get(db, repo, _veto_key(item_id, field)) is True:
        return False
    last = db_repo.kv_get(db, repo, _set_key(item_id, field))
    if last is not None and current_value != last:
        db_repo.kv_set(db, repo, _veto_key(item_id, field), True)
        log.info("item %s field %r changed by a human — vetoing it", item_id, field)
        return False
    return True


def record_write(
    db: Surreal, repo: str, item_id: str, field: str, value: object
) -> None:
    """Remember the value the steward just wrote, so a later human edit is detectable."""
    db_repo.kv_set(db, repo, _set_key(item_id, field), value)
