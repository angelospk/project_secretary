"""Theme grouping: dominant meaningful label, generic/priority labels ignored."""

from __future__ import annotations

from secretary.organizer.models import Item
from secretary.organizer.themes import OTHER, group


def _item(number, labels):
    return Item(kind="issue", repo="o/r", number=number, title=f"#{number}",
                state="open", labels=labels)


def test_groups_by_meaningful_label():
    items = [_item(1, ["search"]), _item(2, ["search"]), _item(3, ["onboarding"])]
    themes = {t.name: [i.number for i in t.items] for t in group(items)}
    assert themes["search"] == [1, 2]
    assert themes["onboarding"] == [3]


def test_generic_and_priority_labels_are_skipped():
    items = [_item(1, ["bug", "search"]), _item(2, ["p0", "infra"])]
    themes = {t.name: [i.number for i in t.items] for t in group(items, priority_labels={"p0"})}
    assert "search" in themes and "infra" in themes
    assert "bug" not in themes and "p0" not in themes


def test_unlabeled_falls_into_other_which_sorts_last():
    items = [_item(1, []), _item(2, ["search"]), _item(3, ["search"])]
    names = [t.name for t in group(items)]
    assert names[-1] == OTHER
    assert names[0] == "search"  # bigger theme first
