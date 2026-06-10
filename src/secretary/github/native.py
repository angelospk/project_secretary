"""Ingest GitHub-native issue dependencies and sub-issues (GraphQL).

Two kinds of edge, both typed and human-confirmed in GitHub's UI:
- `depends_native`: issue A blocked-by B → A -depends_native-> B. Same ordering
  semantics as the body-parsed `depends_on`, so the organizer unions them.
- `subissue_of`: child -subissue_of-> parent. An annotation that NEVER drives order.

Best-effort and behind a flag: the edge-parsing is pure (and tested); the live query is
caught by the caller so a missing scope or an unsupported field degrades to "no native
edges", never an error. NOTE: GitHub's issue-dependency GraphQL surface is young —
validate `blockedBy` against the live schema before relying on `depends_native`.
"""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary.db import repo as db_repo
from secretary.github.client import GitHubClient

log = logging.getLogger(__name__)

Edge = tuple[tuple[str, str, int], str, tuple[str, str, int]]

_QUERY = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    issues(first: 50, after: $cursor, states: OPEN) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        parent { number }
        subIssues(first: 50) { nodes { number } }
        blockedBy: blockedByIssues(first: 50) { nodes { number } }
      }
    }
  }
}
"""


def _numbers(connection: dict | None) -> list[int]:
    nodes = (connection or {}).get("nodes") or []
    return [n["number"] for n in nodes if isinstance(n.get("number"), int)]


def native_edges(issue_node: dict, repo: str) -> list[Edge]:
    """Pure: the depends_native / subissue_of edges implied by one issue's node."""
    number = issue_node.get("number")
    if not isinstance(number, int):
        return []
    edges: list[Edge] = []
    src = ("issue", repo, number)
    # A blocked-by B → A depends on B.
    for dep in _numbers(issue_node.get("blockedBy")):
        if dep != number:
            edges.append((src, "depends_native", ("issue", repo, dep)))
    # child -subissue_of-> parent (from this node's parent, and from each sub-issue).
    parent = (issue_node.get("parent") or {}).get("number")
    if isinstance(parent, int) and parent != number:
        edges.append((src, "subissue_of", ("issue", repo, parent)))
    for child in _numbers(issue_node.get("subIssues")):
        if child != number:
            edges.append((("issue", repo, child), "subissue_of", src))
    return edges


def ingest_native(db: Surreal, repo: str, client: GitHubClient) -> int:
    """Ingest native dependency/sub-issue edges for all open issues. Returns edge count.

    Best-effort: paginates the GraphQL query and writes edges via `relate`
    (DELETE-then-RELATE idempotent). The caller wraps this so a failure is non-fatal.
    """
    count = 0
    cursor: str | None = None
    while True:
        data = client.graphql(
            _QUERY, {"owner": client.owner, "name": client.repo, "cursor": cursor}
        )
        issues = ((data.get("repository") or {}).get("issues")) or {}
        for node in issues.get("nodes", []):
            for source, kind, target in native_edges(node, repo):
                db_repo.relate(db, source, kind, target)
                count += 1
        page = issues.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return count
        cursor = page.get("endCursor")
