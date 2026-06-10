"""Writer recovery: a kv-stored plan issue that was deleted on GitHub must heal."""

from __future__ import annotations

import httpx
import pytest

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.organizer import writer
from secretary.organizer.models import ReleasePlan


def _settings() -> Settings:
    return Settings(github_repo="o/r")


def _plan() -> ReleasePlan:
    return ReleasePlan("o/r", "v1", [], [], [], [], [])


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.github.com/x")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"{status}", request=req, response=resp)


class FakeClient:
    def __init__(self, *, get_raises: Exception | None = None):
        self._get_raises = get_raises
        self.created = False

    def get_issue(self, number: int) -> dict:
        if self._get_raises is not None:
            raise self._get_raises
        return {"body": "existing"}

    def create_issue(self, title: str, body: str, labels=None) -> dict:
        self.created = True
        return {"number": 777, "body": body}

    def update_issue_body(self, number: int, body: str) -> dict:
        return {}


def _patch_kv(monkeypatch, kv: dict) -> None:
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set",
                        lambda db, repo, key, value: kv.__setitem__(key, value))


def test_missing_plan_issue_heals_and_recreates(monkeypatch):
    kv = {"plan:v1": 123}  # points at an issue that 404s
    _patch_kv(monkeypatch, kv)
    client = FakeClient(get_raises=_http_error(404))

    msg = writer.write_plan(client, None, _settings(), _plan())

    assert client.created
    assert kv["plan:v1"] == 777  # healed to the freshly created issue
    assert "777" in msg


def test_non_404_get_error_still_raises(monkeypatch):
    kv = {"plan:v1": 123}
    _patch_kv(monkeypatch, kv)
    client = FakeClient(get_raises=_http_error(500))

    with pytest.raises(httpx.HTTPStatusError):
        writer.write_plan(client, None, _settings(), _plan())

    assert not client.created  # a server error must not trigger a duplicate issue
