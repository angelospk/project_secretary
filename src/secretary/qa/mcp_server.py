"""`secretary mcp` — a read-only MCP server over the backlog memory (stdio).

Exposes four tools that map onto `BacklogTools`. Read-only by construction: no tool
reaches a write path. Tool inputs are validated in `BacklogTools`; here we catch the
two failure shapes (`ValueError` → bad input, `NotFound` → missing item) and return a
structured `{error, message}` instead of leaking a traceback, a DB query, or a key.
Stdio transport means no port, no auth story — the server inherits the caller's env.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from secretary.config import Settings
from secretary.embeddings.embedder import Embedder, LocalEmbedder
from secretary.qa.tools import BacklogTools, NotFound

log = logging.getLogger(__name__)


def _guard(fn):
    """Run a tool call, converting known failures into structured error dicts."""
    try:
        return fn()
    except NotFound as exc:
        return {"error": "not_found", "message": str(exc)}
    except ValueError as exc:
        return {"error": "invalid_input", "message": str(exc)}
    except Exception:  # noqa: BLE001 - never leak internals over the wire
        log.exception("backlog tool failed")
        return {"error": "internal_error", "message": "the tool failed; see server logs"}


def build_server(settings: Settings, embedder: Embedder | None = None) -> FastMCP:
    """Build (but do not run) the FastMCP server, for `run` and for in-memory tests."""
    tools = BacklogTools(settings, embedder or LocalEmbedder())
    mcp = FastMCP("secretary-backlog")

    @mcp.tool(description="Search the backlog by natural-language query (vector + edges).")
    def search_backlog(query: str, repo: str | None = None, k: int | None = None) -> dict:
        return _guard(lambda: tools.search_backlog(query, repo, k))

    @mcp.tool(description="Full record for one issue/PR: metadata, comments, edges.")
    def get_item(kind: str, repo: str, number: int) -> dict:
        return _guard(lambda: tools.get_item(kind, repo, number))

    @mcp.tool(description="Classified related issues/PRs for an item (semantic + reranker).")
    def related(kind: str, repo: str, number: int, k: int | None = None) -> dict:
        return _guard(lambda: tools.related(kind, repo, number, k))

    @mcp.tool(description="The organizer's current release plan for a milestone.")
    def release_plan(repo: str, milestone: str) -> dict:
        return _guard(lambda: tools.release_plan(repo, milestone))

    @mcp.tool(description="Advisory backlog-hygiene findings (probably fixed/duplicate/"
                          "dormant) with evidence. Read-only — the gardener never closes.")
    def gardener_findings(repo: str) -> dict:
        return _guard(lambda: tools.gardener_findings(repo))

    return mcp


def serve_mcp(settings: Settings) -> None:
    """Run the stdio MCP server until the client disconnects."""
    build_server(settings).run()
