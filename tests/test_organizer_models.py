"""Model parsing for the organizer: dependency refs and engagement signals."""

from __future__ import annotations

from secretary.github.models import Issue, depends_on_refs
from secretary.organizer.models import Item


def test_depends_on_matches_directed_phrasing():
    assert depends_on_refs("blocked by #1") == [1]
    assert depends_on_refs("depends on #2 and needs #3") == [2, 3]
    assert depends_on_refs("requires #4") == [4]


def test_depends_on_ignores_weak_and_closing_refs():
    assert depends_on_refs("see #1, relates to #2") == []
    assert depends_on_refs("closes #5") == []
    assert depends_on_refs(None) == []


def test_depends_on_dedups_ordered():
    assert depends_on_refs("needs #7, blocked by #7, depends on #3") == [7, 3]


def test_issue_parses_reactions_and_comment_count():
    raw = {
        "number": 1, "title": "t", "state": "open",
        "comments": 4,
        "reactions": {"+1": 3, "heart": 2, "-1": 1, "confused": 5},
    }
    issue = Issue.from_api(raw, "o/r")
    assert issue.comments_count == 4
    assert issue.reactions == 5  # +1(3) + heart(2); -1 and confused excluded


def test_issue_defaults_when_signals_absent():
    issue = Issue.from_api({"number": 2, "title": "t", "state": "open"}, "o/r")
    assert issue.reactions == 0
    assert issue.comments_count == 0


def _row(number, *, body=None, native=None):
    return {"kind": "issue", "repo": "o/r", "number": number, "title": f"#{number}",
            "state": "open", "body": body, "native_depends_on": native or []}


def test_item_unions_native_and_body_depends(monkeypatch):
    # native edge to #1, body declares #2 — both must drive ordering, deduped.
    item = Item.from_row(_row(9, body="depends on #2", native=[1]))
    assert item.depends_on == [1, 2]


def test_item_dedups_native_and_body_overlap():
    item = Item.from_row(_row(9, body="blocked by #1", native=[1]))
    assert item.depends_on == [1]  # same dep from both sources, once


def test_item_native_only_when_no_body():
    item = Item.from_row(_row(9, native=[3, 4]))
    assert item.depends_on == [3, 4]
