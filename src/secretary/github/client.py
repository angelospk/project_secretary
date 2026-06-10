"""Thin synchronous GitHub REST + GraphQL client.

Handles auth, pagination (Link header), and basic rate-limit backoff. Returns raw
JSON dicts; normalization into models happens in the ingest pipeline.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

from secretary.config import Settings

_TIMELINE_ACCEPT = "application/vnd.github.mockingbird-preview+json"


class GitHubClient:
    def __init__(
        self, settings: Settings, *, repo: str | None = None, client: httpx.Client | None = None
    ):
        self.settings = settings
        # `repo` ("owner/name") selects which repo this client targets; defaults to
        # the settings' first/default repo for single-repo callers.
        target = repo or settings.repo_list[0]
        self.repo_full = target
        self.owner, _, self.repo = target.partition("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        self._client = client or httpx.Client(
            base_url=settings.github_api_url, headers=headers, timeout=30.0
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low level ----------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """HTTP request with shared secondary-rate-limit backoff."""
        for _ in range(5):
            resp = self._client.request(method, url, **kwargs)
            if resp.status_code == 403 and self._is_rate_limited(resp):
                self._sleep_until_reset(resp)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def _get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        return self._request("GET", url, params=params, headers=headers)

    @staticmethod
    def _is_rate_limited(resp: httpx.Response) -> bool:
        return resp.headers.get("X-RateLimit-Remaining") == "0" or (
            "secondary rate limit" in resp.text.lower()
        )

    @staticmethod
    def _sleep_until_reset(resp: httpx.Response) -> None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            time.sleep(int(retry_after) + 1)
            return
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            time.sleep(max(1, int(reset) - int(time.time()) + 1))
            return
        time.sleep(60)

    def _paginate(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> Iterator[dict]:
        next_url: str | None = url
        next_params = dict(params or {})
        next_params.setdefault("per_page", 100)
        while next_url:
            resp = self._get(next_url, params=next_params, headers=headers)
            for item in resp.json():
                yield item
            link = resp.links.get("next")
            next_url = link["url"] if link else None
            next_params = None  # the next link already carries query params

    # -- REST endpoints -----------------------------------------------------

    def list_issues(self, *, since: str | None = None) -> Iterator[dict]:
        """All issues AND PR-stubs (state=all). Caller splits on `pull_request`."""
        params: dict[str, Any] = {"state": "all", "sort": "updated", "direction": "asc"}
        if since:
            params["since"] = since
        yield from self._paginate(
            f"/repos/{self.owner}/{self.repo}/issues", params=params
        )

    def get_pull(self, number: int) -> dict:
        return self._get(f"/repos/{self.owner}/{self.repo}/pulls/{number}").json()

    def list_issue_comments(self, *, since: str | None = None) -> Iterator[dict]:
        params: dict[str, Any] = {"sort": "updated", "direction": "asc"}
        if since:
            params["since"] = since
        yield from self._paginate(
            f"/repos/{self.owner}/{self.repo}/issues/comments", params=params
        )

    def get_timeline(self, number: int) -> list[dict]:
        return list(
            self._paginate(
                f"/repos/{self.owner}/{self.repo}/issues/{number}/timeline",
                headers={"Accept": _TIMELINE_ACCEPT},
            )
        )

    def get_issue(self, number: int) -> dict:
        return self._get(f"/repos/{self.owner}/{self.repo}/issues/{number}").json()

    def update_issue_body(self, number: int, body: str) -> dict:
        """PATCH an issue's body. Requires a token with issues:write scope."""
        resp = self._request(
            "PATCH", f"/repos/{self.owner}/{self.repo}/issues/{number}", json={"body": body}
        )
        return resp.json()

    def get_issue_comments(self, number: int) -> list[dict]:
        return list(
            self._paginate(f"/repos/{self.owner}/{self.repo}/issues/{number}/comments")
        )

    def create_comment(self, number: int, body: str) -> dict:
        resp = self._request(
            "POST", f"/repos/{self.owner}/{self.repo}/issues/{number}/comments",
            json={"body": body},
        )
        return resp.json()

    def update_comment(self, comment_id: int, body: str) -> dict:
        resp = self._request(
            "PATCH", f"/repos/{self.owner}/{self.repo}/issues/comments/{comment_id}",
            json={"body": body},
        )
        return resp.json()

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        endpoint = self.settings.github_api_url.rstrip("/") + "/graphql"
        resp = self._client.post(
            endpoint,
            json={"query": query, "variables": variables or {}},
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL errors: {payload['errors']}")
        return payload["data"]
