"""Dependency ordering: typed deps drive order; weak links and closes do not."""

from __future__ import annotations

from secretary.organizer.models import Item
from secretary.organizer.order import dependency_order, dependents_count, member_depends_on


def _item(number, *, body=None, state="open"):
    return Item(kind="issue", repo="o/r", number=number, title=f"#{number}",
                state=state, body=body)


def test_depends_on_places_dependency_first():
    members = [_item(3, body="blocked by #1"), _item(1)]
    order = [i.number for i in dependency_order(members)]
    assert order.index(1) < order.index(3)


def test_closes_keyword_is_not_a_dependency():
    # PR #4 "closes #3" must NOT make #3 depend on #4 (it's a resolves edge).
    members = [_item(3), Item(kind="pr", repo="o/r", number=4, title="#4",
                              state="open", body="closes #3")]
    deps = member_depends_on(members)
    assert deps[3] == set()
    assert deps[4] == set()  # closes is not depends_on


def test_bare_mention_does_not_order():
    members = [_item(7, body="see #1 for context"), _item(1)]
    assert member_depends_on(members)[7] == set()


def test_out_of_milestone_dep_is_dropped_from_ordering():
    members = [_item(3, body="depends on #99")]  # #99 not a member
    assert member_depends_on(members)[3] == set()


def test_dependents_count_counts_in_milestone_only():
    members = [_item(1), _item(2, body="depends on #1"), _item(3, body="needs #1")]
    assert dependents_count(members)[1] == 2


def test_cycle_degrades_gracefully():
    members = [_item(1, body="depends on #2"), _item(2, body="depends on #1")]
    order = dependency_order(members)
    assert {i.number for i in order} == {1, 2}  # both placed, no hang


def test_cycle_break_does_not_jump_a_downstream_node_ahead_of_its_dep():
    # #1 <-> #2 form a cycle; #3 depends on #1 but is NOT itself in any cycle.
    # Breaking the cycle must pick a node inside the cycle, never #3 — otherwise #3
    # lands before its own dependency #1.
    members = [
        _item(1, body="depends on #2", state="closed"),
        _item(2, body="depends on #1", state="closed"),
        _item(3, body="depends on #1"),
    ]
    order = [i.number for i in dependency_order(members)]
    assert order.index(1) < order.index(3)
