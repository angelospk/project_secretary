"""Batch embeddings: member vectors are fetched once and threaded through expand."""

from __future__ import annotations

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.organizer import expand
from secretary.organizer.models import Item
from secretary.semantic import related


def _settings(**overrides) -> Settings:
    return Settings(github_repo="o/r", **overrides)


def _member(number: int) -> Item:
    return Item(kind="issue", repo="o/r", number=number, title=f"#{number}",
                state="open", milestone="v1")


def test_member_vectors_are_threaded_into_find_related(monkeypatch):
    members = [_member(1), _member(2)]
    seen: dict[int, object] = {}

    def fake_find_related(db, embedder, repo, number, **kwargs):
        seen[number] = kwargs.get("vector")
        return []

    monkeypatch.setattr(expand, "find_related", fake_find_related)
    vecs = {("issue", 1): [0.1, 0.2], ("issue", 2): [0.3, 0.4]}
    expand.suggested_adds(None, None, "o/r", members,
                          settings=_settings(expand_max=2), member_vectors=vecs)

    assert seen[1] == [0.1, 0.2]
    assert seen[2] == [0.3, 0.4]


def test_find_related_uses_provided_vector_and_skips_db_fetch(monkeypatch):
    monkeypatch.setattr(db_repo, "pr_exists", lambda db, repo, n: False)
    monkeypatch.setattr(db_repo, "get_meta", lambda *a: {
        "repo": "o/r", "number": 1, "title": "t", "body": "b",
        "state": "open", "labels": [], "milestone": None,
    })

    def boom(*a, **k):
        raise AssertionError("get_embedding must not be called when a vector is given")

    monkeypatch.setattr(db_repo, "get_embedding", boom)
    monkeypatch.setattr(db_repo, "neighbors", lambda *a: set())
    monkeypatch.setattr(db_repo, "similar", lambda db, kind, vector, k: [])

    out = related.find_related(None, None, "o/r", 1, vector=[0.5, 0.5])
    assert out == []
