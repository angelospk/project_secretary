"""Gardener signals as pure predicates: evidence, survivor logic, bands, guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from secretary.gardener import signals as sig
from secretary.gardener.signals import (
    BORDERLINE,
    CONFIDENT,
    DORMANT,
    DUPLICATE_SIG,
    FIXED,
    SUPERSEDED,
)

NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)
PRIORITY = {"p0", "p1", "p2", "p3", "critical", "bug"}


def _issue(number, **kw):
    base = {"number": number, "title": f"#{number}", "body": "", "labels": [],
            "milestone": None, "reactions": 0, "comments_count": 0,
            "created_at": NOW - timedelta(days=400), "updated_at": NOW}
    base.update(kw)
    return base


# --- probably_fixed -----------------------------------------------------------

def test_fixed_confident_for_merged_closing_pr():
    f = sig.probably_fixed(_issue(1), [
        {"repo": "o/r", "number": 50, "via": "relates_to", "merged_at": NOW, "state": "closed"}])
    assert f.signal == FIXED and f.confidence == CONFIDENT
    assert "o/r#50" in f.summary


def test_fixed_none_when_pr_not_merged():
    f = sig.probably_fixed(_issue(1), [
        {"repo": "o/r", "number": 50, "via": "relates_to", "merged_at": None, "state": "open"},
        {"repo": "o/r", "number": 51, "via": "relates_to", "merged_at": None, "state": "closed"}])
    assert f is None  # closed-unmerged is not a fix


def test_fixed_borderline_for_merged_mention_only():
    f = sig.probably_fixed(_issue(1), [
        {"repo": "o/r", "number": 9, "via": "mentions", "merged_at": NOW, "state": "closed"}])
    assert f.confidence == BORDERLINE  # a mention is not a closing ref


# --- probably_duplicate -------------------------------------------------------

def test_duplicate_survivor_is_more_engaged():
    a = _issue(1, reactions=1, comments_count=1)       # engagement 2
    b = _issue(2, reactions=5, comments_count=5)       # engagement 10 → survivor
    f = sig.probably_duplicate(a, b, PRIORITY)
    assert f.issue == 1 and "duplicate of #2" in f.summary


def test_duplicate_tiebreak_prefers_older_then_lower_number():
    older = _issue(2, created_at=NOW - timedelta(days=100))
    newer = _issue(5, created_at=NOW - timedelta(days=10))
    f = sig.probably_duplicate(newer, older, PRIORITY)
    assert f.issue == 5  # older #2 survives, newer #5 is the loser


def test_duplicate_guard_protects_prioritized_issue():
    prioritized = _issue(1, milestone="v1", reactions=0)   # low engagement but milestoned
    plain = _issue(2, reactions=10)                          # high engagement, no priority
    # engagement would close #1, but the guard refuses to close the milestoned one.
    assert sig.probably_duplicate(prioritized, plain, PRIORITY) is None


# --- probably_superseded ------------------------------------------------------

def _closed(number, dist, repo="o/r"):
    return {"number": number, "repo": repo, "dist": dist, "title": "done"}


def test_superseded_confident_when_close():
    f = sig.probably_superseded(_issue(1), _closed(80, 0.30))
    assert f.confidence == CONFIDENT and f.signal == SUPERSEDED


def test_superseded_borderline_without_judge():
    f = sig.probably_superseded(_issue(1), _closed(80, 0.50), judge_verdict=None)
    assert f.confidence == BORDERLINE


def test_superseded_judge_yes_promotes_no_suppresses():
    assert sig.probably_superseded(_issue(1), _closed(80, 0.50), judge_verdict=True).confidence == CONFIDENT
    assert sig.probably_superseded(_issue(1), _closed(80, 0.50), judge_verdict=False) is None


def test_superseded_none_when_too_far():
    assert sig.probably_superseded(_issue(1), _closed(80, 0.90)) is None


# --- dormant ------------------------------------------------------------------

def test_dormant_fires_for_old_unprioritized_issue():
    f = sig.dormant(_issue(1, updated_at=NOW - timedelta(days=300)), NOW, 180, PRIORITY)
    assert f.signal == DORMANT and f.confidence == BORDERLINE


def test_dormant_skips_recent():
    assert sig.dormant(_issue(1, updated_at=NOW - timedelta(days=10)), NOW, 180, PRIORITY) is None


def test_dormant_skips_milestoned_or_priority_labeled():
    old = NOW - timedelta(days=300)
    assert sig.dormant(_issue(1, updated_at=old, milestone="v1"), NOW, 180, PRIORITY) is None
    assert sig.dormant(_issue(1, updated_at=old, labels=["p1"]), NOW, 180, PRIORITY) is None


# --- determinism --------------------------------------------------------------

def test_fingerprint_is_stable_for_same_target():
    a = sig.probably_duplicate(_issue(1), _issue(2, reactions=9), PRIORITY)
    b = sig.probably_duplicate(_issue(1), _issue(2, reactions=9), PRIORITY)
    assert a.fingerprint == b.fingerprint
