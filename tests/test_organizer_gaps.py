"""Coherence checks: gaps, done, duplicates, stale-but-critical."""

from __future__ import annotations

from secretary.organizer.gaps import coherence
from secretary.organizer.models import Item


def _item(number, *, body=None, state="open", labels=None, title=None):
    return Item(kind="issue", repo="o/r", number=number, title=title or f"#{number}",
                state=state, body=body, labels=labels or [])


def _kinds(warnings):
    return {w.kind for w in warnings}


def test_gap_on_dependency_outside_milestone():
    members = [_item(1, body="depends on #99")]
    warnings = coherence(members)
    assert "gap" in _kinds(warnings)
    assert any(99 in w.numbers for w in warnings if w.kind == "gap")


def test_done_member_flagged():
    members = [_item(1, state="closed")]
    assert "done" in _kinds(coherence(members))


def test_duplicate_needs_cosine_label_and_title_overlap():
    members = [_item(1, labels=["export"], title="Export CSV files"),
               _item(2, labels=["export"], title="CSV files export")]
    near = {1: [1.0, 0.0], 2: [0.99, 0.01]}  # cosine ~ identical
    assert "duplicate" in _kinds(coherence(members, embeddings=near))

    far = {1: [1.0, 0.0], 2: [0.0, 1.0]}  # orthogonal → not duplicate
    assert "duplicate" not in _kinds(coherence(members, embeddings=far))


def test_no_duplicate_without_shared_label():
    members = [_item(1, labels=["search"], title="Export CSV files"),
               _item(2, labels=["infra"], title="CSV files export")]
    near = {1: [1.0, 0.0], 2: [0.99, 0.01]}
    assert "duplicate" not in _kinds(coherence(members, embeddings=near))


def test_no_duplicate_when_titles_dont_overlap():
    # Same label + near vectors but unrelated titles → not a duplicate (Codex precision).
    members = [_item(1, labels=["search"], title="Index rewrite"),
               _item(2, labels=["search"], title="Ranking tweaks")]
    near = {1: [1.0, 0.0], 2: [0.99, 0.01]}
    assert "duplicate" not in _kinds(coherence(members, embeddings=near))


def test_stale_critical_when_depended_on_and_quiet():
    members = [_item(1)]
    warnings = coherence(members, dependents={1: 3}, fresh_norm={1: 0.0})
    assert "stale_critical" in _kinds(warnings)
