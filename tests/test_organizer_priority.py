"""Priority scoring: normalization, weighting, degenerate ranges, judge blend."""

from __future__ import annotations

from secretary.organizer.models import Item
from secretary.organizer.priority import _active_weights, minmax, rank_members

WEIGHTS = {"react": 0.25, "dep": 0.3, "engage": 0.15, "label": 0.2, "fresh": 0.1, "judge": 0.0}
LABELS = {"p0": 1.0, "p1": 0.8, "bug": 0.4}


def _item(number, *, reactions=0, comments=0, labels=None, updated=0.0, state="open"):
    return Item(
        kind="issue", repo="o/r", number=number, title=f"#{number}", state=state,
        labels=labels or [], reactions=reactions, comments_count=comments,
        updated_at_epoch=updated,
    )


def test_minmax_degenerate_returns_neutral():
    assert minmax({1: 5.0, 2: 5.0}) == {1: 0.0, 2: 0.0}  # all equal → 0, no div-by-zero
    assert minmax({1: 7.0}) == {1: 0.0}  # single item
    assert minmax({}) == {}


def test_minmax_scales_to_unit_range():
    out = minmax({1: 0.0, 2: 5.0, 3: 10.0})
    assert out == {1: 0.0, 2: 0.5, 3: 1.0}


def test_active_weights_drop_judge_and_renormalize():
    # judge weight 0 → excluded; remaining weights renormalize to sum 1.
    active = _active_weights(WEIGHTS, ("react", "dep", "engage", "label", "fresh"))
    assert abs(sum(active.values()) - 1.0) < 1e-9
    assert "judge" not in active


def test_high_dependent_outranks_recent_when_dep_dominates():
    members = [
        _item(1, reactions=0, comments=0, updated=1.0),   # stale, but many dependents
        _item(2, reactions=1, comments=9, updated=100.0),  # recent + chatty, no dependents
    ]
    weights = {"react": 0.0, "dep": 1.0, "engage": 0.0, "label": 0.0, "fresh": 0.0}
    ranked = rank_members(members, weights=weights, label_map=LABELS,
                          dependents={1: 3, 2: 0})
    assert ranked[0][0].number == 1


def test_label_priority_feeds_score():
    members = [_item(1, labels=["p0"]), _item(2, labels=["bug"])]
    weights = {"react": 0.0, "dep": 0.0, "engage": 0.0, "label": 1.0, "fresh": 0.0}
    ranked = rank_members(members, weights=weights, label_map=LABELS, dependents={})
    assert ranked[0][0].number == 1
    top = ranked[0][1]
    assert top.components["label"] == 1.0


def test_judge_blends_in_and_carries_reason():
    members = [_item(1), _item(2)]
    weights = {"react": 0.0, "dep": 0.0, "engage": 0.0, "label": 0.0, "fresh": 0.0, "judge": 1.0}
    ranked = rank_members(
        members, weights=weights, label_map=LABELS, dependents={},
        judge_scores={1: (0.9, "high impact"), 2: (0.1, "minor")},
    )
    assert ranked[0][0].number == 1
    assert ranked[0][1].judge_reason == "high impact"
    assert ranked[0][1].total == 0.9
