"""Multi-repo eval (Codex-recommended first test).

Two repos with the SAME issue number sync into one DB without collision, and
cross-repo related search returns a justified cross-repo candidate while an
unrelated same-number issue stays below threshold.

Requires a running SurrealDB on 127.0.0.1:8000; skipped otherwise.
"""

from __future__ import annotations

import math

import pytest

from secretary.config import Settings
from secretary.db import repo
from secretary.db.connection import surreal
from secretary.github.models import Issue

APP = "owner/app"
TASKS = "owner/tasks"
OTHER = "owner/unrelated"


def _settings() -> Settings:
    return Settings(
        surreal_url="ws://127.0.0.1:8000/rpc",
        surreal_user="root",
        surreal_pass="root",
        surreal_ns="opencouncil",
        surreal_db="secretary_multirepo_itest",
    )


def _vec(*pairs: tuple[int, float]) -> list[float]:
    v = [0.0] * 384
    for idx, val in pairs:
        v[idx] = val
    norm = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / norm for c in v]


@pytest.fixture()
def db():
    settings = _settings()
    try:
        cm = surreal(settings)
        conn = cm.__enter__()
    except Exception:  # noqa: BLE001
        pytest.skip("no SurrealDB server reachable on 127.0.0.1:8000")
    for table in ("issue", "pr", "comment", "project_item", "sync_state", "relates_to", "mentions"):
        conn.query(f"REMOVE TABLE IF EXISTS {table};")
    repo.apply_schema(conn)
    try:
        yield conn
    finally:
        cm.__exit__(None, None, None)


def _seed(db) -> None:
    repo.upsert_issue(db, Issue(repo=APP, number=1, title="Import council meeting transcript", state="open", labels=["ingest"]))
    repo.set_embedding(db, "issue", APP, 1, _vec((0, 1.0)))
    repo.upsert_issue(db, Issue(repo=TASKS, number=1, title="Transcript import task tracking", state="open", labels=["ingest"]))
    repo.set_embedding(db, "issue", TASKS, 1, _vec((0, 1.0), (1, 0.05)))
    repo.upsert_issue(db, Issue(repo=OTHER, number=1, title="Fix login button", state="open", labels=["bug"]))
    repo.set_embedding(db, "issue", OTHER, 1, _vec((1, 1.0)))


def test_same_number_across_repos_no_collision(db):
    _seed(db)
    count = db.query("SELECT count() FROM issue GROUP ALL;")
    assert count[0]["count"] == 3  # three distinct records, all number=1

    app = repo.get_meta(db, "issue", APP, 1)
    tasks = repo.get_meta(db, "issue", TASKS, 1)
    assert app["title"].startswith("Import")
    assert tasks["title"].startswith("Transcript")


def test_watermarks_are_per_repo(db):
    from datetime import datetime, timezone

    ts = datetime(2024, 5, 1, tzinfo=timezone.utc)
    repo.set_watermark(db, APP, "items", ts)
    assert repo.get_watermark(db, TASKS, "items") is None
    assert repo.get_watermark(db, APP, "items") is not None


def test_cross_repo_similarity_returns_candidate(db):
    _seed(db)
    hits = repo.similar(db, "issue", _vec((0, 1.0)), k=5)  # repo=None => all repos
    found = {(h["repo"], h["number"]) for h in hits}
    assert (APP, 1) in found
    assert (TASKS, 1) in found  # the cross-repo neighbour surfaces


def test_scoped_similarity_stays_in_repo(db):
    _seed(db)
    hits = repo.similar(db, "issue", _vec((0, 1.0)), k=5, repo=TASKS)
    assert {h["repo"] for h in hits} == {TASKS}


def test_relation_roundtrips_across_repos(db):
    _seed(db)
    repo.relate(db, ("issue", APP, 1), "relates_to", ("issue", TASKS, 1))
    nbrs = repo.neighbors(db, "issue", APP, 1)
    assert ("issue", TASKS, 1) in nbrs


def test_cross_repo_mention_from_body(db):
    from secretary.github.models import Issue
    from secretary.ingest.pipeline import link_cross_repo_mentions

    # Source references the target before the target exists: the final pass must
    # still link them (order-independent), and skip a ref to an un-indexed repo.
    repo.upsert_issue(db, Issue(repo=APP, number=7, title="frontend bug", state="open",
                               body=f"root cause is in {TASKS}#3 and ghost/repo#9"))
    repo.upsert_issue(db, Issue(repo=TASKS, number=3, title="backend cause", state="open"))

    links = link_cross_repo_mentions(db)
    assert links == 1  # only the indexed target is linked; ghost/repo#9 is skipped

    nbrs = repo.neighbors(db, "issue", APP, 7)
    assert ("issue", TASKS, 3) in nbrs
    assert ("issue", "ghost/repo", 9) not in nbrs


def test_unrelated_same_number_is_weak(db):
    from secretary.semantic.related import find_related

    _seed(db)

    class FakeEmbedder:
        dim = 384

        def encode_passages(self, texts):
            return [_vec((0, 1.0)) for _ in texts]

        def encode_query(self, text):
            return _vec((0, 1.0))

    items = find_related(db, FakeEmbedder(), APP, 1, k=10, include_weak=True)
    by = {(i.repo, i.kind, i.number): i for i in items}
    # the unrelated login issue, despite sharing number=1, must not be a strong match
    other = by.get((OTHER, "issue", 1))
    assert other is None or other.category == "weak_match"
