"""Backlog Q&A retrieval: vector ranking, edge expansion, caps, isolation."""

from __future__ import annotations

import pytest

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.qa import retrieve
from secretary.qa.retrieve import Ref, query


class StubEmbedder:
    def encode_query(self, text):
        return [1.0, 0.0]

    def encode_passages(self, texts):
        return [[1.0, 0.0] for _ in texts]


def _row(number, dist, *, repo="o/r", title=None, state="open", body=None):
    return {"repo": repo, "number": number, "title": title or f"#{number}",
            "state": state, "labels": [], "milestone": None, "dist": dist, "body": body}


def _patch(monkeypatch, *, similar, neighbors=None, meta=None):
    monkeypatch.setattr(
        db_repo, "similar",
        lambda db, kind, vec, k=5, repo=None: similar(kind, repo, k),
    )
    monkeypatch.setattr(
        db_repo, "neighbors",
        lambda db, kind, repo, number: (neighbors or {}).get((kind, repo, number), set()),
    )
    monkeypatch.setattr(
        db_repo, "get_meta",
        lambda db, kind, repo, number: (meta or {}).get((kind, repo, number)),
    )


def _settings(**kw):
    return Settings(github_repo="o/r", **kw)


def test_vector_hits_from_both_kinds_sorted_by_distance(monkeypatch):
    def similar(kind, repo, k):
        if kind == "issue":
            return [_row(1, 0.30), _row(2, 0.10)]
        return [_row(50, 0.05)]  # a PR, closest of all

    _patch(monkeypatch, similar=similar)
    hits = query(None, StubEmbedder(), _settings(), "q")
    refs = [(h.ref.kind, h.ref.number) for h in hits]
    assert refs == [("pr", 50), ("issue", 2), ("issue", 1)]
    assert all(h.why == "vector" for h in hits)
    assert hits[0].score == pytest.approx(0.95)  # 1 - 0.05


def test_k_caps_vector_hits(monkeypatch):
    _patch(monkeypatch, similar=lambda kind, repo, k: (
        [_row(n, 0.1 * n) for n in range(1, 6)] if kind == "issue" else []))
    hits = query(None, StubEmbedder(), _settings(), "q", k=2)
    assert len(hits) == 2
    assert [h.ref.number for h in hits] == [1, 2]


def test_repo_filter_is_passed_through(monkeypatch):
    seen_repos = []

    def similar(kind, repo, k):
        seen_repos.append(repo)
        return []

    _patch(monkeypatch, similar=similar)
    query(None, StubEmbedder(), _settings(), "q", repo="o/r")
    assert seen_repos == ["o/r", "o/r"]  # one per kind, scoped


def test_duplicate_ref_across_kinds_is_deduped(monkeypatch):
    # same (issue, o/r, 7) surfaced twice (shouldn't, but guard anyway)
    _patch(monkeypatch, similar=lambda kind, repo, k: (
        [_row(7, 0.1), _row(7, 0.2)] if kind == "issue" else []))
    hits = query(None, StubEmbedder(), _settings(), "q")
    assert [h.ref.number for h in hits] == [7]


def test_edge_expansion_appends_after_vector_with_no_score(monkeypatch):
    _patch(
        monkeypatch,
        similar=lambda kind, repo, k: [_row(1, 0.1)] if kind == "issue" else [],
        neighbors={("issue", "o/r", 1): {("pr", "o/r", 99)}},
        meta={("pr", "o/r", 99): {"repo": "o/r", "number": 99, "title": "fix",
                                  "state": "merged", "body": "the fix body"}},
    )
    hits = query(None, StubEmbedder(), _settings(), "q")
    assert [(h.why, h.ref.kind, h.ref.number) for h in hits] == [
        ("vector", "issue", 1), ("edge", "pr", 99)]
    assert hits[1].score is None
    assert hits[1].snippet == "the fix body"


def test_edge_neighbor_already_a_vector_hit_is_not_duplicated(monkeypatch):
    _patch(
        monkeypatch,
        similar=lambda kind, repo, k: [_row(1, 0.1), _row(2, 0.2)] if kind == "issue" else [],
        neighbors={("issue", "o/r", 1): {("issue", "o/r", 2)}},
        meta={("issue", "o/r", 2): {"repo": "o/r", "number": 2, "title": "#2", "state": "open"}},
    )
    hits = query(None, StubEmbedder(), _settings(), "q")
    assert [h.ref.number for h in hits] == [1, 2]  # #2 stays a vector hit, no edge dupe


def test_missing_neighbor_metadata_is_skipped(monkeypatch):
    _patch(
        monkeypatch,
        similar=lambda kind, repo, k: [_row(1, 0.1)] if kind == "issue" else [],
        neighbors={("issue", "o/r", 1): {("issue", "other/repo", 500)}},
        meta={},  # neighbor not ingested
    )
    hits = query(None, StubEmbedder(), _settings(), "q")
    assert [h.ref.number for h in hits] == [1]


def test_edge_caps_are_respected(monkeypatch):
    nbrs = {("issue", "o/r", 1): {("issue", "o/r", n) for n in range(10, 20)}}
    meta = {("issue", "o/r", n): {"repo": "o/r", "number": n, "title": f"#{n}", "state": "open"}
            for n in range(10, 20)}
    _patch(
        monkeypatch,
        similar=lambda kind, repo, k: [_row(1, 0.1)] if kind == "issue" else [],
        neighbors=nbrs, meta=meta,
    )
    hits = query(None, StubEmbedder(), _settings(qa_edge_per_hit=2, qa_max_edge_hits=5), "q")
    edges = [h for h in hits if h.why == "edge"]
    assert len(edges) == 2  # per-hit cap bites before the global cap


def test_zero_k_is_rejected(monkeypatch):
    _patch(monkeypatch, similar=lambda kind, repo, k: [])
    with pytest.raises(ValueError):
        query(None, StubEmbedder(), _settings(), "q", k=0)


def test_snippet_prefers_body_then_title():
    assert retrieve._snippet({"body": "  hello   world ", "title": "t"}) == "hello world"
    assert retrieve._snippet({"body": "", "title": "fallback"}) == "fallback"
    assert retrieve._snippet({"body": "x" * 500}) == "x" * 200


def test_ref_is_hashable_and_ordered():
    assert Ref("issue", "o/r", 1) < Ref("issue", "o/r", 2)
    assert len({Ref("issue", "o/r", 1), Ref("issue", "o/r", 1)}) == 1
