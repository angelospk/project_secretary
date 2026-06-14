"""Projects v2 ingestion must page through every item (and every project), not stop
at the first 100. Driven by a fake GraphQL client returning multiple pages."""

from __future__ import annotations

import pytest

from secretary.github import projects


@pytest.fixture()
def captured(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        projects.db_repo, "upsert_project_item",
        lambda db, repo, *, gh_id, status, fields, content: seen.append(gh_id),
    )
    return seen


def _item(node_id: str) -> dict:
    return {"id": node_id, "content": None, "fieldValues": {"nodes": []}}


def test_paginates_items_within_a_project(captured):
    class FakeClient:
        owner, repo = "o", "r"

        def graphql(self, query, variables):
            if "projectsV2(" in query:  # first projects page, project P1 first items page
                return {"repository": {"projectsV2": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"number": 1, "id": "P1", "items": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        "nodes": [_item("i1")],
                    }}],
                }}}
            # _ITEMS_QUERY: next items page for the project node
            assert variables["id"] == "P1" and variables["cursor"] == "c1"
            return {"node": {"items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [_item("i2")],
            }}}

    n = projects.ingest_projects(db=None, repo="o/r", client=FakeClient())
    assert n == 2
    assert captured == ["i1", "i2"]


def test_paginates_across_projects(captured):
    class FakeClient:
        owner, repo = "o", "r"

        def graphql(self, query, variables):
            cursor = variables.get("projectsCursor")
            if cursor is None:
                return {"repository": {"projectsV2": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "p1"},
                    "nodes": [{"number": 1, "id": "P1", "items": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [_item("a")],
                    }}],
                }}}
            assert cursor == "p1"
            return {"repository": {"projectsV2": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"number": 2, "id": "P2", "items": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [_item("b")],
                }}],
            }}}

    n = projects.ingest_projects(db=None, repo="o/r", client=FakeClient())
    assert n == 2
    assert captured == ["a", "b"]
