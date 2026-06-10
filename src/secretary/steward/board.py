"""Board I/O: the write surface the orchestrator talks to, and a GraphQL adapter.

The orchestrator depends only on the `BoardClient` protocol, so it is fully testable
with a fake. `GraphQLBoard` is the real Projects v2 adapter; its mutations are the
dangerous part the staged rollout exists for, so validate it live in `place` before
ever enabling `sync`. Every write is best-effort: a failure logs and the run continues.
"""

from __future__ import annotations

import logging
from typing import Protocol

from secretary.config import Settings
from secretary.github.client import GitHubClient

log = logging.getLogger(__name__)


class BoardClient(Protocol):
    def set_status(self, item_id: str, status: str) -> None: ...
    def set_score(self, item_id: str, field: str, score: float) -> None: ...
    def set_single_select(self, item_id: str, field: str, option: str) -> None: ...


_PROJECT_QUERY = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    projectV2(number:$number) {
      id
      fields(first: 50) {
        nodes {
          __typename
          ... on ProjectV2FieldCommon { id name dataType }
          ... on ProjectV2SingleSelectField {
            id name options { id name }
          }
        }
      }
    }
  }
}
"""

_SET_OPTION = """
mutation($project:ID!, $item:ID!, $field:ID!, $option:String!) {
  updateProjectV2ItemFieldValue(input:{
    projectId:$project, itemId:$item, fieldId:$field,
    value:{ singleSelectOptionId:$option }
  }) { projectV2Item { id } }
}
"""

_SET_NUMBER = """
mutation($project:ID!, $item:ID!, $field:ID!, $number:Float!) {
  updateProjectV2ItemFieldValue(input:{
    projectId:$project, itemId:$item, fieldId:$field,
    value:{ number:$number }
  }) { projectV2Item { id } }
}
"""


class GraphQLBoard:
    """Projects v2 write adapter. Resolves the project + field/option ids once."""

    def __init__(self, client: GitHubClient, settings: Settings):
        self._client = client
        self._settings = settings
        self._project_id: str | None = None
        self._fields: dict[str, dict] = {}  # field name -> {id, dataType, options{name:id}}

    def _resolve(self) -> None:
        if self._project_id is not None:
            return
        data = self._client.graphql(
            _PROJECT_QUERY,
            {"owner": self._client.owner, "name": self._client.repo,
             "number": self._settings.project_number},
        )
        project = ((data.get("repository") or {}).get("projectV2")) or {}
        self._project_id = project.get("id")
        if not self._project_id:
            raise RuntimeError(
                f"project #{self._settings.project_number} not found on "
                f"{self._client.owner}/{self._client.repo}"
            )
        for node in (project.get("fields") or {}).get("nodes", []):
            name = node.get("name")
            if not name:
                continue
            options = {o["name"]: o["id"] for o in node.get("options", []) or []}
            self._fields[name] = {"id": node.get("id"), "options": options}

    def _field(self, name: str) -> dict:
        self._resolve()
        field = self._fields.get(name)
        if field is None:
            raise RuntimeError(f"board field {name!r} not found")
        return field

    def set_single_select(self, item_id: str, field: str, option: str) -> None:
        f = self._field(field)
        option_id = f["options"].get(option)
        if option_id is None:
            raise RuntimeError(f"field {field!r} has no option {option!r}")
        self._client.graphql(_SET_OPTION, {
            "project": self._project_id, "item": item_id,
            "field": f["id"], "option": option_id,
        })

    def set_status(self, item_id: str, status: str) -> None:
        self.set_single_select(item_id, self._settings.status_field, status)

    def set_score(self, item_id: str, field: str, score: float) -> None:
        f = self._field(field)
        self._client.graphql(_SET_NUMBER, {
            "project": self._project_id, "item": item_id,
            "field": f["id"], "number": float(score),
        })
