"""GitHub Projects v2 ingestion (GraphQL).

Best-effort: Projects v2 lives only on the GraphQL API and may require extra token
scopes. Failures here must not abort the rest of the sync.
"""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary.db import repo as db_repo
from secretary.github.client import GitHubClient

log = logging.getLogger(__name__)

# Shared item-node selection, reused by the initial query and the items-only follow-up.
_ITEM_FIELDS = """
fragment ItemFields on ProjectV2Item {
  id
  content {
    __typename
    ... on Issue { number }
    ... on PullRequest { number }
  }
  fieldValues(first: 20) {
    nodes {
      __typename
      ... on ProjectV2ItemFieldSingleSelectValue {
        name
        field { ... on ProjectV2FieldCommon { name } }
      }
      ... on ProjectV2ItemFieldTextValue {
        text
        field { ... on ProjectV2FieldCommon { name } }
      }
    }
  }
}
"""

# Page through projects; each project carries its first page of items.
_QUERY = """
query($owner:String!, $name:String!, $projectsCursor:String) {
  repository(owner:$owner, name:$name) {
    projectsV2(first: 10, after: $projectsCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        id
        items(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { ...ItemFields }
        }
      }
    }
  }
}
""" + _ITEM_FIELDS

# Follow-up: the remaining item pages for one project, addressed by its node id.
_ITEMS_QUERY = """
query($id:ID!, $cursor:String) {
  node(id: $id) {
    ... on ProjectV2 {
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { ...ItemFields }
      }
    }
  }
}
""" + _ITEM_FIELDS


def _field_map(field_values: dict) -> dict[str, str]:
    fields: dict[str, str] = {}
    for node in (field_values or {}).get("nodes", []):
        field = node.get("field") or {}
        name = field.get("name")
        value = node.get("name") or node.get("text")
        if name and value is not None:
            fields[name] = value
    return fields


def _content_ref(content: dict | None) -> tuple[str, int] | None:
    if not content:
        return None
    typename = content.get("__typename")
    number = content.get("number")
    if number is None:
        return None
    if typename == "Issue":
        return ("issue", number)
    if typename == "PullRequest":
        return ("pr", number)
    return None


def _ingest_nodes(db: Surreal, repo: str, nodes: list[dict]) -> int:
    count = 0
    for node in nodes:
        item_id = node.get("id")
        if not item_id:
            log.warning("project item without id; skipping")
            continue
        fields = _field_map(node.get("fieldValues") or {})
        db_repo.upsert_project_item(
            db,
            repo,
            gh_id=item_id,
            status=fields.get("Status"),
            fields=fields,
            content=_content_ref(node.get("content")),
        )
        count += 1
    return count


def _ingest_project(db: Surreal, repo: str, client: GitHubClient, project: dict) -> int:
    """Ingest one project's items, paging past the first 100 via its node id."""
    items = project.get("items") or {}
    count = _ingest_nodes(db, repo, items.get("nodes", []))
    page = items.get("pageInfo") or {}
    project_id = project.get("id")
    while page.get("hasNextPage") and project_id:
        cursor = page.get("endCursor")
        if not cursor:  # defensive: no cursor means we can't advance — stop, don't loop
            break
        data = client.graphql(_ITEMS_QUERY, {"id": project_id, "cursor": cursor})
        items = ((data.get("node") or {}).get("items")) or {}
        count += _ingest_nodes(db, repo, items.get("nodes", []))
        page = items.get("pageInfo") or {}
    return count


def ingest_projects(db: Surreal, repo: str, client: GitHubClient) -> int:
    """Ingest every Projects v2 item for the repo, paging through projects and items."""
    count = 0
    projects_cursor: str | None = None
    while True:
        data = client.graphql(
            _QUERY,
            {"owner": client.owner, "name": client.repo, "projectsCursor": projects_cursor},
        )
        conn = ((data.get("repository") or {}).get("projectsV2") or {})
        for project in conn.get("nodes", []):
            count += _ingest_project(db, repo, client, project)
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        projects_cursor = page.get("endCursor")
        if not projects_cursor:  # defensive: can't advance without a cursor
            break
    return count
