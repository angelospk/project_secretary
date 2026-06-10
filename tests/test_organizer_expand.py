"""Suggested adds: pool widening past the cap and member/threshold filtering."""

from __future__ import annotations

from secretary.config import Settings
from secretary.organizer import expand
from secretary.organizer.models import Item
from secretary.semantic.reranker import RelatedItem


def _settings(**overrides) -> Settings:
    return Settings(github_repo="o/r", **overrides)


def _member(number: int) -> Item:
    return Item(kind="issue", repo="o/r", number=number, title=f"#{number}",
                state="open", milestone="v1")


def _related(number: int, dist: float = 0.2, **overrides) -> RelatedItem:
    fields = dict(kind="issue", number=number, title=f"#{number}", state="open",
                  dist=dist, category="conceptual_context", confidence=0.8, repo="o/r")
    fields.update(overrides)
    return RelatedItem(**fields)


def test_pool_widens_past_cap_so_members_cant_crowd_out(monkeypatch):
    # With expand_max=2 and 3 members, the retrieval pool must exceed the cap —
    # otherwise the top hits (mostly fellow members) starve real candidates.
    members = [_member(n) for n in (1, 2, 3)]
    seen: list[dict] = []

    def fake_find_related(db, embedder, repo, number, **kwargs):
        seen.append(kwargs)
        return [_related(2), _related(3), _related(50)]  # two members, one candidate

    monkeypatch.setattr(expand, "find_related", fake_find_related)
    settings = _settings(expand_max=2)
    adds = expand.suggested_adds(None, None, "o/r", members, settings=settings)

    assert all(kw["k"] == kw["per_kind"] == 2 + 3 for kw in seen)
    assert [a.number for a in adds] == [50]  # members filtered, candidate survives


def test_threshold_cap_and_best_distance_dedup(monkeypatch):
    members = [_member(1)]

    def fake_find_related(db, embedder, repo, number, **kwargs):
        return [
            _related(50, dist=0.9),   # beyond threshold → dropped
            _related(60, dist=0.3),
            _related(60, dist=0.2),   # same candidate, closer → kept at 0.2
            _related(70, dist=0.25),
            _related(80, dist=0.4),   # capped out by expand_max=2
        ]

    monkeypatch.setattr(expand, "find_related", fake_find_related)
    settings = _settings(expand_max=2)
    adds = expand.suggested_adds(None, None, "o/r", members, settings=settings)
    assert [(a.number, a.dist) for a in adds] == [(60, 0.2), (70, 0.25)]
