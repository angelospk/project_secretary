"""Integration tests against a live SurrealDB.

Requires a running server (e.g. `surreal start --user root --pass root memory`).
Skipped automatically if no server is reachable.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from secretary.config import Settings
from secretary.db import repo
from secretary.db.connection import surreal
from secretary.github.models import Comment, Issue, PullRequest

REPO = "owner/app"


def _settings() -> Settings:
    return Settings(
        surreal_url="ws://127.0.0.1:8000/rpc",
        surreal_user="root",
        surreal_pass="root",
        surreal_ns="opencouncil",
        surreal_db="secretary_itest",
    )


@pytest.fixture()
def db():
    settings = _settings()
    try:
        cm = surreal(settings)
        conn = cm.__enter__()
    except Exception:  # noqa: BLE001
        pytest.skip("no SurrealDB server reachable on 127.0.0.1:8000")
    # clean slate
    for table in ("issue", "pr", "comment", "project_item", "sync_state", "relates_to", "mentions"):
        conn.query(f"REMOVE TABLE IF EXISTS {table};")
    repo.apply_schema(conn)
    try:
        yield conn
    finally:
        cm.__exit__(None, None, None)


def _issue(number: int, title: str) -> Issue:
    return Issue(repo=REPO, number=number, title=title, state="open")


def _pr(number: int, title: str, state: str = "open") -> PullRequest:
    return PullRequest(repo=REPO, number=number, title=title, state=state)


def test_upsert_issue_is_idempotent(db):
    repo.upsert_issue(db, _issue(100, "first"))
    repo.upsert_issue(db, _issue(100, "second"))
    res = db.query("SELECT count() FROM issue GROUP ALL;")
    assert res[0]["count"] == 1
    got = db.query("SELECT title, repo FROM type::record('issue', [$r, 100]);", {"r": REPO})
    assert got[0]["title"] == "second"
    assert got[0]["repo"] == REPO


def test_relate_is_idempotent_and_traversable(db):
    repo.upsert_pr(db, _pr(200, "p"))
    repo.upsert_issue(db, _issue(100, "i"))
    repo.relate(db, ("pr", REPO, 200), "relates_to", ("issue", REPO, 100))
    repo.relate(db, ("pr", REPO, 200), "relates_to", ("issue", REPO, 100))  # again
    edges = db.query("SELECT count() FROM relates_to GROUP ALL;")
    assert edges[0]["count"] == 1
    linked = db.query(
        "SELECT ->relates_to->issue.number AS n FROM type::record('pr', [$r, 200]);", {"r": REPO}
    )
    assert linked[0]["n"] == [100]


def test_comment_parent_link(db):
    repo.upsert_issue(db, _issue(7, "i"))
    comment = Comment(repo=REPO, gh_id=9001, parent_number=7, author="x", body="hi")
    repo.upsert_comment(db, comment, "issue")
    got = db.query("SELECT parent.number AS n FROM type::record('comment', [$r, 9001]);", {"r": REPO})
    assert got[0]["n"] == 7


def test_watermark_roundtrip(db):
    assert repo.get_watermark(db, REPO, "items") is None
    ts = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
    repo.set_watermark(db, REPO, "items", ts)
    got = repo.get_watermark(db, REPO, "items")
    assert got is not None
    assert got.astimezone(timezone.utc) == ts


def test_pr_exists(db):
    assert repo.pr_exists(db, REPO, 200) is False
    repo.upsert_pr(db, _pr(200, "p"))
    assert repo.pr_exists(db, REPO, 200) is True


def _vec(*pairs: tuple[int, float]) -> list[float]:
    import math

    v = [0.0] * 384
    for idx, val in pairs:
        v[idx] = val
    norm = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / norm for c in v]


def test_embedding_search_returns_nearest(db):
    repo.upsert_issue(db, _issue(1, "a"))
    repo.set_embedding(db, "issue", REPO, 1, _vec((0, 1.0)))
    repo.upsert_issue(db, _issue(2, "b"))
    repo.set_embedding(db, "issue", REPO, 2, _vec((0, 1.0), (1, 0.1)))
    repo.upsert_issue(db, _issue(3, "c"))
    repo.set_embedding(db, "issue", REPO, 3, _vec((1, 1.0)))

    hits = repo.similar(db, "issue", _vec((0, 1.0)), k=3)
    nums = [h["number"] for h in hits]
    assert nums[0] == 1  # exact match nearest
    assert nums.index(2) < nums.index(3)  # 2 is closer than the orthogonal 3


def test_fetch_unembedded_and_service(db):
    repo.upsert_issue(db, _issue(10, "x"))
    repo.upsert_issue(db, _issue(11, "y"))
    repo.set_embedding(db, "issue", REPO, 11, _vec((0, 1.0)))

    pending = repo.fetch_unembedded(db, "issue")
    assert [p["number"] for p in pending] == [10]
    assert pending[0]["repo"] == REPO

    from secretary.embeddings.service import embed_pending

    class FakeEmbedder:
        dim = 384

        def encode_passages(self, texts):
            return [_vec((0, 1.0)) for _ in texts]

        def encode_query(self, text):
            return _vec((0, 1.0))

    counts = embed_pending(db, FakeEmbedder(), kinds=("issue",))
    assert counts["issue"] == 1
    assert repo.fetch_unembedded(db, "issue") == []


def test_neighbors_both_directions(db):
    repo.upsert_pr(db, _pr(10, "p"))
    repo.upsert_issue(db, _issue(1, "i"))
    repo.upsert_issue(db, _issue(2, "j"))
    repo.relate(db, ("pr", REPO, 10), "relates_to", ("issue", REPO, 1))
    repo.relate(db, ("issue", REPO, 2), "mentions", ("pr", REPO, 10))
    nbrs = repo.neighbors(db, "pr", REPO, 10)
    assert ("issue", REPO, 1) in nbrs
    assert ("issue", REPO, 2) in nbrs


def test_find_related_classifies(db):
    from secretary.semantic.related import find_related

    repo.upsert_issue(db, _issue(1, "Email notifications are broken"))
    repo.set_embedding(db, "issue", REPO, 1, _vec((0, 1.0)))
    repo.upsert_issue(db, _issue(2, "Email notifications broken for users"))
    repo.set_embedding(db, "issue", REPO, 2, _vec((0, 1.0), (1, 0.05)))
    repo.upsert_issue(db, _issue(3, "Dark mode toggle"))
    repo.set_embedding(db, "issue", REPO, 3, _vec((1, 1.0)))
    repo.upsert_pr(db, _pr(10, "Fix email", state="closed"))
    repo.set_embedding(db, "pr", REPO, 10, _vec((0, 1.0), (2, 0.05)))
    repo.relate(db, ("pr", REPO, 10), "relates_to", ("issue", REPO, 1))

    class FakeEmbedder:
        dim = 384

        def encode_passages(self, texts):
            return [_vec((0, 1.0)) for _ in texts]

        def encode_query(self, text):
            return _vec((0, 1.0))

    items = find_related(db, FakeEmbedder(), REPO, 1, k=10, include_weak=True)
    by = {(i.kind, i.number): i for i in items}
    assert all(i.number != 1 or i.kind != "issue" for i in items)  # excludes self
    assert ("pr", 10) in by
    assert by[("pr", 10)].category == "historical_reference"  # closed + explicit edge
    assert "graph-edge" in by[("pr", 10)].signals
    assert ("issue", 2) in by
