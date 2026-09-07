"""Console operational queues: duplicate-candidates and gardener-findings read views.

Both reshape data the secretary already computes into sortable review lists. The
data layer is injected (related_fn / collect_fn) so these tests cover the shaping,
pair-dedup and ordering without a live DB or the embedder.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from secretary.config import Settings
from secretary.console import app as app_mod
from secretary.console import data
from secretary.gardener.signals import Finding
from secretary.semantic.reranker import (
    DUPLICATE,
    IMPLEMENTATION_OVERLAP,
    WEAK_MATCH,
    RelatedItem,
)


def _settings(**kw) -> Settings:
    base = dict(github_repos="o/r", surreal_url="ws://x", surreal_user="r",
                surreal_pass="r", surreal_ns="n", surreal_db="d")
    base.update(kw)
    return Settings(**base)


class _DB:
    """Minimal fake: open_issues comes from a scripted list."""

    def __init__(self, open_issues):
        self._open = open_issues

    def query(self, q, vars=None):  # only open_issues hits the DB here
        if "state = 'open'" in q:
            return self._open
        return []


def _ri(number, title, category, conf, *, repo="o/r", kind="issue", state="open", signals=()):
    return RelatedItem(kind=kind, number=number, title=title, state=state, dist=0.3,
                       category=category, confidence=conf, repo=repo, signals=list(signals))


# --- duplicate candidates -----------------------------------------------------

def test_duplicate_candidates_shapes_pairs_and_dedups():
    db = _DB([{"number": 1, "title": "Login bug"}, {"number": 2, "title": "Cannot log in"}])
    # 1 -> 2 and 2 -> 1 are the same pair; must appear once.
    related = {
        1: [_ri(2, "Cannot log in", DUPLICATE, 0.9, signals=["shared label: auth"])],
        2: [_ri(1, "Login bug", DUPLICATE, 0.88)],
    }
    out = data.duplicate_candidates(
        db, _settings(), "o/r", related_fn=lambda d, r, n: related.get(n, []))
    assert len(out) == 1
    pair = out[0]
    assert pair["source"]["number"] in (1, 2) and pair["match"]["number"] in (1, 2)
    assert pair["category"] == DUPLICATE
    assert pair["signals"] == ["shared label: auth"]
    assert pair["source"]["url"].startswith("https://github.com/o/r/issues/")


def test_duplicate_candidates_filters_weak_and_sorts_by_confidence():
    db = _DB([{"number": 1, "title": "A"}, {"number": 5, "title": "B"}])
    related = {
        1: [_ri(9, "weak", WEAK_MATCH, 0.99), _ri(7, "overlap", IMPLEMENTATION_OVERLAP, 0.6)],
        5: [_ri(8, "dup", DUPLICATE, 0.95)],
    }
    out = data.duplicate_candidates(
        db, _settings(), "o/r", related_fn=lambda d, r, n: related.get(n, []))
    assert [p["match"]["number"] for p in out] == [8, 7]  # weak dropped, sorted desc
    assert all(p["category"] != WEAK_MATCH for p in out)


def test_duplicate_candidates_skips_issue_without_embedding():
    db = _DB([{"number": 1, "title": "A"}])

    def boom(d, r, n):
        raise ValueError("no stored embedding")

    assert data.duplicate_candidates(db, _settings(), "o/r", related_fn=boom) == []


# --- gardener findings --------------------------------------------------------

def test_gardener_findings_shapes_and_orders_confident_first():
    findings = [
        Finding(issue=3, signal="dormant", confidence="borderline",
                summary="no activity 200 days", suggestion="ping or close",
                fingerprint="aaa", evidence=("last update 2025-12",)),
        Finding(issue=7, signal="fixed", confidence="confident",
                summary="closed by merged PR", suggestion="close as fixed",
                fingerprint="bbb", evidence=("merged PR o/r#40",)),
    ]
    out = data.gardener_findings(
        db=_DB([]), settings=_settings(), repo="o/r",
        now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        collect_fn=lambda *a, **k: findings)
    assert [f["issue"] for f in out] == [7, 3]  # confident first
    assert out[0]["url"] == "https://github.com/o/r/issues/7"
    assert out[0]["evidence"] == ["merged PR o/r#40"]
    assert out[1]["signal"] == "dormant"


# --- route rendering ----------------------------------------------------------

class _DummyCM:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


@pytest.fixture()
def render_client(monkeypatch):
    monkeypatch.setattr(app_mod, "surreal", lambda s: _DummyCM())
    return TestClient(app_mod.build_app(_settings(console_session_secret="x")))


def test_duplicates_route_renders_and_escapes(render_client, monkeypatch):
    monkeypatch.setattr(data, "duplicate_candidates", lambda db, s, repo: [{
        "source": {"repo": "o/r", "kind": "issue", "number": 1, "title": "<x>bad</x>",
                   "url": "https://github.com/o/r/issues/1"},
        "match": {"repo": "o/r", "kind": "issue", "number": 2, "title": "twin",
                  "state": "open", "url": "https://github.com/o/r/issues/2"},
        "category": "duplicate_candidate", "confidence": 0.91, "signals": ["shared label"]}])
    r = render_client.get("/duplicates")
    assert r.status_code == 200
    assert "#1" in r.text and "twin" in r.text and "0.91" in r.text
    assert "<x>bad</x>" not in r.text and "&lt;x&gt;" in r.text  # autoescaped


def test_gardener_route_renders(render_client, monkeypatch):
    monkeypatch.setattr(data, "gardener_findings", lambda db, s, repo: [{
        "issue": 7, "url": "https://github.com/o/r/issues/7", "signal": "fixed",
        "confidence": "confident", "summary": "closed by merged PR",
        "suggestion": "close as fixed", "evidence": ["merged PR o/r#40"]}])
    r = render_client.get("/gardener")
    assert r.status_code == 200
    assert "#7" in r.text and "closed by merged PR" in r.text and "merged PR o/r#40" in r.text
