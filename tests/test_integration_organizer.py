"""Organizer eval (Codex-recommended). Seeds a milestone with the tricky cases the
ordering/priority/expand logic must get right, builds a plan, and asserts on it.

Requires a running SurrealDB on 127.0.0.1:8000; skipped otherwise.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from secretary.config import Settings
from secretary.db import repo
from secretary.db.connection import surreal
from secretary.github.models import Issue, PullRequest
from secretary.organizer import plan as organizer_plan

REPO = "owner/app"
MS = "v1"


def _settings() -> Settings:
    return Settings(
        surreal_url="ws://127.0.0.1:8000/rpc",
        surreal_user="root", surreal_pass="root",
        surreal_ns="opencouncil", surreal_db="secretary_organizer_itest",
        github_repo=REPO,
    )


def _vec(*pairs: tuple[int, float]) -> list[float]:
    v = [0.0] * 384
    for idx, val in pairs:
        v[idx] = val
    norm = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / norm for c in v]


def _dt(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=timezone.utc)


class FakeEmbedder:
    dim = 384

    def encode_passages(self, texts):
        return [_vec((0, 1.0)) for _ in texts]

    def encode_query(self, text):
        return _vec((0, 1.0))


@pytest.fixture()
def db():
    settings = _settings()
    try:
        cm = surreal(settings)
        conn = cm.__enter__()
    except Exception:  # noqa: BLE001
        pytest.skip("no SurrealDB server reachable on 127.0.0.1:8000")
    for table in ("issue", "pr", "comment", "project_item", "sync_state",
                  "organizer_kv", "relates_to", "mentions"):
        conn.query(f"REMOVE TABLE IF EXISTS {table};")
    repo.apply_schema(conn)
    try:
        yield conn
    finally:
        cm.__exit__(None, None, None)


def _seed(db) -> None:
    # #1 — P1, stale, depended on by #3 (and #10). In milestone.
    repo.upsert_issue(db, Issue(repo=REPO, number=1, title="Search index rewrite",
                               state="open", labels=["p1", "search"], milestone=MS,
                               updated_at=_dt(1)))
    repo.set_embedding(db, "issue", REPO, 1, _vec((0, 1.0)))
    # #2 — recent, many comments, no dependents. In milestone.
    repo.upsert_issue(db, Issue(repo=REPO, number=2, title="Tweak button colour",
                               state="open", labels=["ui"], milestone=MS,
                               comments_count=20, updated_at=_dt(28)))
    repo.set_embedding(db, "issue", REPO, 2, _vec((2, 1.0)))
    # #3 — depends on #1. In milestone.
    repo.upsert_issue(db, Issue(repo=REPO, number=3, title="Search ranking",
                               state="open", labels=["search"], milestone=MS,
                               body="blocked by #1", updated_at=_dt(10)))
    repo.set_embedding(db, "issue", REPO, 3, _vec((0, 1.0), (3, 0.2)))
    # #10 — also depends on #1, so #1 has "many" dependents. In milestone.
    repo.upsert_issue(db, Issue(repo=REPO, number=10, title="Search pagination",
                               state="open", labels=["search"], milestone=MS,
                               body="depends on #1", updated_at=_dt(11)))
    repo.set_embedding(db, "issue", REPO, 10, _vec((0, 1.0), (4, 0.3)))
    # PR #4 — "closes #3": a resolves edge, NOT #3 depending on #4. In milestone.
    repo.upsert_pr(db, PullRequest(repo=REPO, number=4, title="Implement ranking",
                                  state="open", milestone=MS, body="closes #3",
                                  updated_at=_dt(12)))
    repo.set_embedding(db, "pr", REPO, 4, _vec((0, 1.0), (3, 0.25)))
    # #7 — only mentions #1, no dependency phrasing. In milestone.
    repo.upsert_issue(db, Issue(repo=REPO, number=7, title="Docs for search",
                               state="open", labels=["docs"], milestone=MS,
                               body="see #1 for background", updated_at=_dt(9)))
    repo.set_embedding(db, "issue", REPO, 7, _vec((7, 1.0)))
    # #8 / #9 — duplicate-like pair (near-identical embedding + shared label). In milestone.
    repo.upsert_issue(db, Issue(repo=REPO, number=8, title="Export CSV",
                               state="open", labels=["export"], milestone=MS,
                               updated_at=_dt(5)))
    repo.set_embedding(db, "issue", REPO, 8, _vec((5, 1.0)))
    repo.upsert_issue(db, Issue(repo=REPO, number=9, title="CSV export option",
                               state="open", labels=["export"], milestone=MS,
                               updated_at=_dt(6)))
    repo.set_embedding(db, "issue", REPO, 9, _vec((5, 1.0), (6, 0.02)))
    # #5 — outside milestone, semantically related to #1, but CLOSED → not suggested.
    repo.upsert_issue(db, Issue(repo=REPO, number=5, title="Old search spike",
                               state="closed", labels=["search"], updated_at=_dt(2)))
    repo.set_embedding(db, "issue", REPO, 5, _vec((0, 1.0), (1, 0.03)))
    # #6 — outside milestone, open, semantically related to #1 → should be suggested.
    repo.upsert_issue(db, Issue(repo=REPO, number=6, title="Search synonyms",
                               state="open", labels=["search"], updated_at=_dt(20)))
    repo.set_embedding(db, "issue", REPO, 6, _vec((0, 1.0), (1, 0.02)))


def test_organizer_eval(db):
    _seed(db)
    settings = _settings()
    release = organizer_plan.build(db, FakeEmbedder(), settings, REPO, MS)

    order = [i.number for i in release.ordered]
    # dependency drives order: #1 before #3 and #10.
    assert order.index(1) < order.index(3)
    assert order.index(1) < order.index(10)

    # "closes #3" did not make #3 depend on PR #4; the plain mention #7→#1 did not order.
    by_number = {i.number: i for i in release.ordered}
    assert by_number[3].depends_on == [1]  # only the real dependency
    assert by_number[4].depends_on == []   # closes is not depends_on
    assert by_number[7].depends_on == []   # mention is not depends_on

    # suggested adds: #6 (open, related) in; #5 (closed) out; no members suggested.
    suggested = {a.number for a in release.suggested_adds}
    assert 6 in suggested
    assert 5 not in suggested
    assert suggested.isdisjoint({1, 2, 3, 4, 7, 8, 9, 10})

    # duplicate warning for #8/#9.
    dup = [w for w in release.warnings if w.kind == "duplicate"]
    assert any(set(w.numbers) == {8, 9} for w in dup)

    # priority: high-dependent #1 outranks recent zero-structure #2 under default weights.
    rank = [item.number for item, _ in release.ranked]
    assert rank.index(1) < rank.index(2)
