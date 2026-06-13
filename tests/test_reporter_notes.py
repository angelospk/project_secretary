"""Release notes: theme grouping, mechanical dev style, title rewording in user style."""

from __future__ import annotations

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.reporter import notes as notes_mod
from secretary.reporter.notes import build_notes


def _settings():
    return Settings(github_repo="o/r")


def _done(monkeypatch, rows):
    monkeypatch.setattr(db_repo, "milestone_done_items", lambda db, repo, m: rows)


def _row(number, title, kind="issue", labels=None):
    return {"number": number, "title": title, "body": "", "kind": kind,
            "labels": labels or [], "milestone": "v1", "reactions": 0, "comments_count": 0}


def test_empty_milestone_message(monkeypatch):
    _done(monkeypatch, [])
    out = build_notes(None, _settings(), "o/r", "v1")
    assert "No closed issues or merged PRs" in out


def test_dev_style_is_mechanical(monkeypatch):
    _done(monkeypatch, [_row(1, "Fix login"), _row(2, "Add export", kind="pr")])
    out = build_notes(None, _settings(), "o/r", "v1", style="dev")
    assert "# v1" in out
    assert "Fix login (#1)" in out and "Add export (#2)" in out


def test_user_style_rewords_titles_via_complete(monkeypatch):
    _done(monkeypatch, [_row(1, "Fix login")])
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return "Logging in is now reliable"

    out = build_notes(None, _settings(), "o/r", "v1", style="user", complete=complete)
    assert "Logging in is now reliable (#1)" in out
    assert calls and "Fix login" in calls[0]


def test_user_style_falls_back_to_raw_title_on_failure(monkeypatch):
    _done(monkeypatch, [_row(1, "Fix login")])

    def boom(prompt):
        raise RuntimeError("provider down")

    out = build_notes(None, _settings(), "o/r", "v1", style="user", complete=boom)
    assert "Fix login (#1)" in out
