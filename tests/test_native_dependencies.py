"""Native-dependency ingest: edge parsing, pagination, relate whitelist, ordering."""

from __future__ import annotations

from secretary.db import repo as db_repo
from secretary.github import native
from secretary.github.native import ingest_native, native_edges
from secretary.organizer.models import Item
from secretary.organizer.order import dependency_order, member_depends_on


def test_native_edges_splits_dependencies_and_sub_issues():
    node = {
        "number": 9,
        "parent": {"number": 2},
        "subIssues": {"nodes": [{"number": 30}, {"number": 31}]},
        "blockedBy": {"nodes": [{"number": 1}, {"number": 5}]},
    }
    edges = native_edges(node, "o/r")
    deps = {(s[2], t[2]) for s, k, t in edges if k == "depends_native"}
    subs = {(s[2], t[2]) for s, k, t in edges if k == "subissue_of"}
    assert deps == {(9, 1), (9, 5)}        # 9 blocked-by 1 and 5
    assert subs == {(9, 2), (30, 9), (31, 9)}  # 9->parent 2; children 30,31 -> 9


def test_native_edges_skips_self_and_missing():
    assert native_edges({"number": 9, "blockedBy": {"nodes": [{"number": 9}]}}, "o/r") == []
    assert native_edges({"parent": {"number": 2}}, "o/r") == []  # no number → nothing


def test_ingest_native_paginates_and_relates(monkeypatch):
    pages = [
        {"repository": {"issues": {
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "nodes": [{"number": 9, "blockedBy": {"nodes": [{"number": 1}]}}],
        }}},
        {"repository": {"issues": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"number": 10, "parent": {"number": 4}}],
        }}},
    ]

    class FakeClient:
        owner, repo = "o", "r"

        def __init__(self):
            self.cursors = []

        def graphql(self, query, variables):
            self.cursors.append(variables["cursor"])
            return pages[len(self.cursors) - 1]

    related: list = []
    monkeypatch.setattr(db_repo, "relate",
                        lambda db, s, k, t: related.append((s, k, t)))

    count = ingest_native(None, "o/r", FakeClient())
    assert count == 2
    kinds = {k for _, k, _ in related}
    assert kinds == {"depends_native", "subissue_of"}


def test_relate_whitelist_accepts_native_kinds():
    class FakeDB:
        def __init__(self):
            self.queries = []

        def query(self, q, params=None):
            self.queries.append((q, params))
            return []

    db = FakeDB()
    db_repo.relate(db, ("issue", "o/r", 9), "depends_native", ("issue", "o/r", 1))
    db_repo.relate(db, ("issue", "o/r", 30), "subissue_of", ("issue", "o/r", 9))
    assert len(db.queries) == 2  # neither raised


def _item(number, *, body=None, native=None):
    return Item(kind="issue", repo="o/r", number=number, title=f"#{number}",
                state="open", body=body, depends_on=list(native or []))


def test_native_dependency_orders_even_without_body_text():
    # #9 is blocked-by #1 only via a native edge (no body) → #1 orders before #9.
    members = [_item(9, native=[1]), _item(1)]
    order = [i.number for i in dependency_order(members)]
    assert order.index(1) < order.index(9)


def test_subissue_relationship_never_orders():
    # A sub-issue edge is stored under a different kind, so it never lands in
    # depends_on. Here #9 has only a parent (#2) and no real dependency.
    members = [_item(9), _item(2)]
    assert member_depends_on(members)[9] == set()
