"""Digest building and rendering: window data, top-decile cut, drift, emptiness."""

from __future__ import annotations

from datetime import datetime, timezone

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.reporter import digest as digest_mod
from secretary.reporter.digest import DigestData, build_digest, render
from secretary.reporter.discord import discord_payload

NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)
SINCE = datetime(2026, 6, 6, tzinfo=timezone.utc)


def _settings(**kw):
    return Settings(github_repo="o/r", **kw)


def _issue_row(number, **kw):
    base = {"number": number, "title": f"#{number}", "body": "", "state": "open",
            "labels": [], "milestone": None, "reactions": 0, "comments_count": 0,
            "created_at": SINCE, "updated_at": SINCE}
    base.update(kw)
    return base


def test_top_decile_keeps_the_highest(monkeypatch):
    rows = [_issue_row(n, reactions=n) for n in range(1, 21)]  # 20 issues
    top = digest_mod._top_decile_priority(_settings(), rows)
    assert len(top) == 2  # 20 // 10
    assert top[0][0] == 20  # most reactions ranks first


def test_top_decile_empty_for_no_new_issues():
    assert digest_mod._top_decile_priority(_settings(), []) == []


def _patch_build(monkeypatch, *, activity, new_rows, kv=None, related=None, gardener=None):
    kv = kv or {}
    monkeypatch.setattr(db_repo, "activity_counts", lambda db, repo, since: activity)
    monkeypatch.setattr(db_repo, "issues_created_since", lambda db, repo, since: new_rows)
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "get_watermark", lambda db, repo, key: None)
    monkeypatch.setattr(digest_mod, "find_related", related or (lambda *a, **k: []))
    if gardener is not None:
        monkeypatch.setattr(digest_mod.gardener_garden, "collect_findings", gardener)


def test_build_digest_assembles_sections(monkeypatch):
    _patch_build(monkeypatch, activity={"opened": 3, "closed": 1, "merged": 2},
                 new_rows=[_issue_row(1, reactions=5)])
    data = build_digest(None, None, _settings(), "o/r", since=SINCE, now=NOW)
    assert data.activity["opened"] == 3
    assert data.notable_priority and data.notable_priority[0][0] == 1
    assert data.gardener_counts is None  # gardener off by default
    assert not data.is_empty


def test_drift_detected_when_fingerprint_changed(monkeypatch):
    kv = {"digest_plan_fingerprints": {"v1": "oldfp"}, "plan_fingerprint:v1": "newfp"}
    _patch_build(monkeypatch, activity={"opened": 0, "closed": 0, "merged": 0},
                 new_rows=[], kv=kv)
    data = build_digest(None, None, _settings(plan_milestones="v1"), "o/r",
                        since=SINCE, now=NOW)
    assert data.drift == ["v1"]


def test_is_empty_when_nothing_happened():
    data = DigestData(repo="o/r", since=SINCE, until=NOW,
                      activity={"opened": 0, "closed": 0, "merged": 0})
    assert data.is_empty


def test_render_includes_activity_and_health():
    data = DigestData(repo="o/r", since=SINCE, until=NOW,
                      activity={"opened": 3, "closed": 1, "merged": 2},
                      notable_priority=[(1, "login bug", 0.9)],
                      duplicate_pairs=[(1, 2)],
                      health={"last_reconcile": "2026-06-13", "judge": "ready", "deepwiki": "configured"})
    out = render(data)
    assert "3 opened" in out and "#1↔#2" in out
    assert "login bug" in out and "Secretary health" in out


def test_discord_payload_truncates_to_limit():
    payload = discord_payload("Digest", "x" * 5000)
    assert len(payload["content"]) <= 2000
    assert payload["content"].endswith("…")
