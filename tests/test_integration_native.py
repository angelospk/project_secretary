"""Native-dependency integration: seed a depends_native edge with NO body text and
assert it both lands in native_depends_map and drives milestone ordering.

Requires a running SurrealDB on 127.0.0.1:8000; skipped otherwise.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from secretary.config import Settings
from secretary.db import repo
from secretary.db.connection import surreal
from secretary.github.models import Issue
from secretary.organizer.models import Item
from secretary.organizer.order import dependency_order

REPO = "owner/native"
MS = "v1"


def _settings() -> Settings:
    return Settings(
        surreal_url="ws://127.0.0.1:8000/rpc",
        surreal_user="root", surreal_pass="root",
        surreal_ns="opencouncil", surreal_db="secretary_native_itest",
        github_repo=REPO, native_dependencies=True,
    )


@pytest.fixture()
def db():
    settings = _settings()
    try:
        cm = surreal(settings)
        conn = cm.__enter__()
    except Exception:  # noqa: BLE001
        pytest.skip("no SurrealDB server reachable on 127.0.0.1:8000")
    for table in ("issue", "pr", "depends_native", "subissue_of",
                  "relates_to", "mentions", "organizer_kv"):
        conn.query(f"REMOVE TABLE IF EXISTS {table};")
    repo.apply_schema(conn)
    try:
        yield conn
    finally:
        cm.__exit__(None, None, None)


def _dt(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=timezone.utc)


def test_native_edge_orders_without_body(db):
    # #9 and #1 in the milestone; #9 is blocked-by #1 only via a native edge.
    db_settings = _settings()  # noqa: F841 - documents intent
    repo.upsert_issue(db, Issue(repo=REPO, number=1, title="dependency",
                                state="open", milestone=MS, updated_at=_dt(1)))
    repo.upsert_issue(db, Issue(repo=REPO, number=9, title="dependent",
                                state="open", milestone=MS, updated_at=_dt(2)))
    # A -depends_native-> B  ==  #9 blocked-by #1.
    repo.relate(db, ("issue", REPO, 9), "depends_native", ("issue", REPO, 1))

    # The DB-side repo filter resolves and returns exactly this edge.
    assert repo.native_depends_map(db, REPO) == {9: [1]}

    members = [
        Item.from_row(r)
        for r in repo.milestone_members(db, REPO, MS, include_native=True)
    ]
    by_number = {m.number: m for m in members}
    assert by_number[9].depends_on == [1]  # attached from the native edge, no body

    order = [m.number for m in dependency_order(members)]
    assert order.index(1) < order.index(9)


def test_native_map_scopes_to_repo(db):
    # An edge in another repo must not leak into this repo's map.
    repo.upsert_issue(db, Issue(repo=REPO, number=1, title="a", state="open"))
    repo.upsert_issue(db, Issue(repo=REPO, number=9, title="b", state="open"))
    repo.upsert_issue(db, Issue(repo="other/repo", number=2, title="c", state="open"))
    repo.upsert_issue(db, Issue(repo="other/repo", number=3, title="d", state="open"))
    repo.relate(db, ("issue", REPO, 9), "depends_native", ("issue", REPO, 1))
    repo.relate(db, ("issue", "other/repo", 3), "depends_native", ("issue", "other/repo", 2))

    assert repo.native_depends_map(db, REPO) == {9: [1]}
    assert repo.native_depends_map(db, "other/repo") == {3: [2]}
