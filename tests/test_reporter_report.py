"""Digest orchestration: section write, Discord, watermark advance, empty-window no-op."""

from __future__ import annotations

from datetime import datetime, timezone

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.reporter import digest as digest_mod
from secretary.reporter import report as report_mod
from secretary.reporter.digest import DigestData

NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)


class StubClient:
    def __init__(self):
        self.created = None
        self.body = ""
        self.updates = 0

    def create_issue(self, title, body, labels=None):
        self.created = title
        return {"number": 555, "body": body}

    def get_issue(self, number):
        return {"body": self.body}

    def update_issue_body(self, number, body):
        self.body = body
        self.updates += 1
        return {}


def _settings(**kw):
    return Settings(github_repo="o/r", **kw)


def _patch(monkeypatch, data, kv):
    monkeypatch.setattr(report_mod, "build_digest", lambda *a, **k: data)
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))
    monkeypatch.setattr(db_repo, "get_watermark", lambda db, repo, key: kv.get(f"wm:{key}"))
    monkeypatch.setattr(db_repo, "set_watermark", lambda db, repo, key, ts: kv.__setitem__(f"wm:{key}", ts))


def _data(empty: bool) -> DigestData:
    activity = {"opened": 0, "closed": 0, "merged": 0} if empty else {"opened": 2, "closed": 0, "merged": 1}
    return DigestData(repo="o/r", since=NOW, until=NOW, activity=activity)


def test_apply_writes_section_and_advances_watermark(monkeypatch):
    kv = {}
    _patch(monkeypatch, _data(empty=False), kv)
    client = StubClient()
    report_mod.run_digest(None, None, client, _settings(), "o/r", now=NOW, apply=True)
    assert client.created == "Secretary digest"
    assert "oc-secretary" in client.body
    assert kv["wm:digest"] == NOW
    assert kv["digest_issue"] == 555


def test_empty_window_writes_nothing_but_advances(monkeypatch):
    kv = {}
    _patch(monkeypatch, _data(empty=True), kv)
    client = StubClient()
    report_mod.run_digest(None, None, client, _settings(), "o/r", now=NOW, apply=True)
    assert client.created is None and client.updates == 0
    assert kv["wm:digest"] == NOW  # window still moves forward


def test_dry_run_writes_nothing(monkeypatch):
    kv = {}
    _patch(monkeypatch, _data(empty=False), kv)
    client = StubClient()
    report_mod.run_digest(None, None, client, _settings(), "o/r", now=NOW, apply=False)
    assert client.created is None and "wm:digest" not in kv


def test_discord_posted_when_configured(monkeypatch):
    kv = {}
    _patch(monkeypatch, _data(empty=False), kv)
    posted = {}
    monkeypatch.setattr(report_mod, "post_discord",
                        lambda webhook, payload: posted.update(webhook=webhook, payload=payload) or True)
    report_mod.run_digest(None, None, StubClient(),
                          _settings(digest_discord_webhook="https://discord/wh"),
                          "o/r", now=NOW, apply=True)
    assert posted["webhook"] == "https://discord/wh"
