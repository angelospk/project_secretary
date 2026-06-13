"""Q&A retrieval against a live SurrealDB: vector hit, edge expansion, read-only.

Skipped automatically if no server is reachable (same gate as test_integration_db).
"""

from __future__ import annotations

import math

import pytest

from secretary.config import Settings
from secretary.db import repo
from secretary.db.connection import surreal
from secretary.github.models import Issue, PullRequest
from secretary.qa import retrieve
from secretary.qa.tools import BacklogTools, NotFound

REPO = "owner/app"


def _settings() -> Settings:
    return Settings(
        github_repo=REPO,
        surreal_url="ws://127.0.0.1:8000/rpc",
        surreal_user="root",
        surreal_pass="root",
        surreal_ns="opencouncil",
        surreal_db="secretary_qa_itest",
    )


def _vec(*pairs: tuple[int, float]) -> list[float]:
    v = [0.0] * 384
    for idx, val in pairs:
        v[idx] = val
    norm = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / norm for c in v]


class FixedEmbedder:
    """Encodes any query to a fixed direction so retrieval is deterministic."""

    def __init__(self, vec):
        self._vec = vec

    def encode_query(self, text):
        return self._vec

    def encode_passages(self, texts):
        return [self._vec for _ in texts]


@pytest.fixture()
def db():
    settings = _settings()
    try:
        cm = surreal(settings)
        conn = cm.__enter__()
    except Exception:  # noqa: BLE001
        pytest.skip("no SurrealDB server reachable on 127.0.0.1:8000")
    try:
        for table in ("issue", "pr", "comment", "relates_to", "mentions",
                      "sync_state", "organizer_kv"):
            conn.query(f"REMOVE TABLE IF EXISTS {table};")
        repo.apply_schema(conn)
        yield conn
    finally:
        cm.__exit__(None, None, None)


def test_query_returns_vector_hit_then_edge_neighbor(db):
    repo.upsert_issue(db, Issue(repo=REPO, number=1, title="login fails", state="open"))
    repo.set_embedding(db, "issue", REPO, 1, _vec((0, 1.0)))
    # An orthogonal PR that fixes #1 — only reachable via the edge, not the vector.
    repo.upsert_pr(db, PullRequest(repo=REPO, number=50, title="fix login", state="merged"))
    repo.set_embedding(db, "pr", REPO, 50, _vec((5, 1.0)))
    repo.relate(db, ("issue", REPO, 1), "relates_to", ("pr", REPO, 50))

    settings = _settings()
    # k=1 keeps the orthogonal PR out of the vector hits, so it can only arrive via the
    # edge (similar() has no distance cutoff — with a tiny corpus it would otherwise be
    # returned as a far vector hit).
    hits = retrieve.query(db, FixedEmbedder(_vec((0, 1.0))), settings, "why login broken?", k=1)
    by = [(h.why, h.ref.kind, h.ref.number) for h in hits]
    assert ("vector", "issue", 1) in by
    assert ("edge", "pr", 50) in by  # surfaced through the graph, not the vector
    edge = next(h for h in hits if h.why == "edge")
    assert edge.score is None


def test_get_item_is_read_only(db):
    repo.upsert_issue(db, Issue(repo=REPO, number=7, title="needs notes", state="open"))
    before = db.query("SELECT count() FROM issue GROUP ALL;")
    tools = BacklogTools(_settings(), FixedEmbedder(_vec((0, 1.0))))
    out = tools.get_item("issue", REPO, 7)
    assert out["item"]["number"] == 7
    after = db.query("SELECT count() FROM issue GROUP ALL;")
    assert before == after  # no write happened

    with pytest.raises(NotFound):
        tools.get_item("issue", REPO, 999)
