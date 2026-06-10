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

_QUERY = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    projectsV2(first: 10) {
      nodes {
        number
        items(first: 100) {
          pageInfo { hasNextPage }
          nodes {
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
        }
      }
    }
  }
}
"""


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


def ingest_projects(db: Surreal, repo: str, client: GitHubClient) -> int:
    """Ingest all Projects v2 items for the repo. Returns count ingested."""
    data = client.graphql(
        _QUERY, {"owner": client.owner, "name": client.repo}
    )
    projects = ((data.get("repository") or {}).get("projectsV2") or {}).get("nodes", [])
    count = 0
    for project in projects:
        items = project.get("items") or {}
        if items.get("pageInfo", {}).get("hasNextPage"):
            log.warning(
                "project #%s has >100 items; only the first page was ingested",
                project.get("number"),
            )
        for node in items.get("nodes", []):
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
