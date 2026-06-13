"""Q&A tools layer (validation, read-only shape) and an in-memory MCP round-trip."""

from __future__ import annotations

import json

import pytest

from secretary.config import Settings
from secretary.qa import tools as tools_mod
from secretary.qa.tools import BacklogTools, NotFound


class StubEmbedder:
    def encode_query(self, text):
        return [1.0, 0.0]

    def encode_passages(self, texts):
        return [[1.0, 0.0] for _ in texts]


class StubDB:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _settings():
    return Settings(github_repo="o/r")


def _tools(monkeypatch, **patches):
    # Make `surreal(settings)` a no-op context manager so tools run without a DB.
    monkeypatch.setattr(tools_mod, "surreal", lambda s: StubDB())
    for name, fn in patches.items():
        monkeypatch.setattr(tools_mod.db_repo, name, fn)
    return BacklogTools(_settings(), StubEmbedder())


# --- input validation ---------------------------------------------------------

def test_search_rejects_empty_query(monkeypatch):
    t = _tools(monkeypatch)
    with pytest.raises(ValueError):
        t.search_backlog("   ")


def test_search_rejects_out_of_range_k(monkeypatch):
    t = _tools(monkeypatch)
    with pytest.raises(ValueError):
        t.search_backlog("q", k=0)
    with pytest.raises(ValueError):
        t.search_backlog("q", k=999)


def test_get_item_rejects_bad_kind_and_number(monkeypatch):
    t = _tools(monkeypatch)
    with pytest.raises(ValueError):
        t.get_item("milestone", "o/r", 1)
    with pytest.raises(ValueError):
        t.get_item("issue", "o/r", 0)


def test_release_plan_rejects_empty_milestone(monkeypatch):
    t = _tools(monkeypatch)
    with pytest.raises(ValueError):
        t.release_plan("o/r", "  ")


# --- read-only behavior -------------------------------------------------------

def test_get_item_returns_record_comments_and_edges(monkeypatch):
    t = _tools(
        monkeypatch,
        get_meta=lambda db, k, r, n: {"number": n, "title": "t", "body": "b"},
        comments_for=lambda db, k, r, n: [{"author": "a", "body": "hi"}],
        neighbors=lambda db, k, r, n: {("pr", "o/r", 9)},
    )
    out = t.get_item("issue", "o/r", 5)
    assert out["item"]["number"] == 5
    assert out["comments"] == [{"author": "a", "body": "hi"}]
    assert out["edges"] == [{"kind": "pr", "repo": "o/r", "number": 9}]


def test_get_item_missing_raises_notfound(monkeypatch):
    t = _tools(monkeypatch, get_meta=lambda db, k, r, n: None)
    with pytest.raises(NotFound):
        t.get_item("issue", "o/r", 404)


# --- in-memory MCP round-trip -------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_search_backlog_round_trip(monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session

    from secretary.qa import mcp_server

    monkeypatch.setattr(tools_mod, "surreal", lambda s: StubDB())
    monkeypatch.setattr(
        tools_mod.db_repo, "similar",
        lambda db, kind, vec, k=5, repo=None: (
            [{"repo": "o/r", "number": 1, "title": "hit", "state": "open",
              "labels": [], "milestone": None, "dist": 0.1, "body": "body text"}]
            if kind == "issue" else []
        ),
    )
    monkeypatch.setattr(tools_mod.db_repo, "neighbors", lambda db, k, r, n: set())

    server = mcp_server.build_server(_settings(), embedder=StubEmbedder())
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        listed = await client.list_tools()
        names = {t.name for t in listed.tools}
        assert names == {"search_backlog", "get_item", "related", "release_plan"}

        result = await client.call_tool("search_backlog", {"query": "anything"})
        payload = json.loads(result.content[0].text)
        assert payload["count"] == 1
        assert payload["hits"][0]["number"] == 1


@pytest.mark.asyncio
async def test_mcp_not_found_is_structured(monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session

    from secretary.qa import mcp_server

    monkeypatch.setattr(tools_mod, "surreal", lambda s: StubDB())
    monkeypatch.setattr(tools_mod.db_repo, "get_meta", lambda db, k, r, n: None)

    server = mcp_server.build_server(_settings(), embedder=StubEmbedder())
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "get_item", {"kind": "issue", "repo": "o/r", "number": 404}
        )
        payload = json.loads(result.content[0].text)
        assert payload["error"] == "not_found"
