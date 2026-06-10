"""Steward orchestration: mode gating, status sync, score, priority, veto."""

from __future__ import annotations

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.steward.run import run_steward


class FakeBoard:
    def __init__(self):
        self.status: list[tuple[str, str]] = []
        self.score: list[tuple[str, str, float]] = []
        self.select: list[tuple[str, str, str]] = []

    def set_status(self, item_id, status):
        self.status.append((item_id, status))

    def set_score(self, item_id, field, score):
        self.score.append((item_id, field, score))

    def set_single_select(self, item_id, field, option):
        self.select.append((item_id, field, option))


def _item(number, gh_id, *, status=None, fields=None):
    return {"gh_id": gh_id, "status": status, "fields": fields or {},
            "kind": "issue", "number": number}


def _run(monkeypatch, items, pr_tokens, *, mode="sync", apply=True, ranked=None,
         kv=None, fill_priority=False, board=None):
    kv = {} if kv is None else kv
    monkeypatch.setattr(db_repo, "project_items", lambda db, repo: items)
    monkeypatch.setattr(db_repo, "linked_pr_status_tokens",
                        lambda db, repo, n: pr_tokens.get(n, []))
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))
    settings = Settings(github_repo="o/r", steward_mode=mode,
                        steward_fill_priority=fill_priority)
    actions = run_steward(None, board, settings, "o/r", ranked=ranked, apply=apply)
    return actions, kv


def test_sync_sets_status_from_an_open_pr(monkeypatch):
    board = FakeBoard()
    actions, kv = _run(monkeypatch, [_item(5, "IT5")], {5: ["open"]}, board=board)
    assert ("IT5", "In Progress") in board.status
    assert any(a.kind == "status" and a.value == "In Progress" for a in actions)
    assert kv["steward_set:IT5:Status"] == "In Progress"


def test_report_mode_reports_but_never_touches_the_board(monkeypatch):
    board = FakeBoard()
    actions, _ = _run(monkeypatch, [_item(5, "IT5")], {5: ["open"]},
                      mode="report", board=board)
    assert board.status == []
    assert actions[0].kind == "status"  # still surfaced in the report


def test_status_is_not_demoted_from_done(monkeypatch):
    board = FakeBoard()
    actions, _ = _run(monkeypatch, [_item(5, "IT5", status="Done")], {5: ["open"]},
                      board=board)
    assert board.status == []
    assert actions == []


def test_status_vetoed_when_a_human_changed_it(monkeypatch):
    board = FakeBoard()
    kv = {"steward_set:IT5:Status": "In Progress"}  # what we last set
    actions, kv = _run(monkeypatch, [_item(5, "IT5", status="In Review")],
                       {5: ["merged"]}, kv=kv, board=board)
    assert board.status == []  # a merged PR would move it to Done, but the human edited it
    assert any(a.kind == "vetoed" for a in actions)
    assert kv["steward_veto:IT5:Status"] is True


def test_sync_writes_the_informational_score_field(monkeypatch):
    board = FakeBoard()
    actions, _ = _run(monkeypatch, [_item(5, "IT5", status="Done")], {},
                      ranked=[(5, 0.83)], board=board)
    assert board.score == [("IT5", "Secretary score", 0.83)]
    assert any(a.kind == "score" and a.value == "0.8300" for a in actions)


def test_fill_priority_only_when_empty(monkeypatch):
    board = FakeBoard()
    items = [_item(5, "IT5", fields={}), _item(6, "IT6", fields={"Priority": "P3"})]
    actions, _ = _run(monkeypatch, items, {}, ranked=[(5, 0.9), (6, 0.1)],
                      fill_priority=True, board=board)
    assert ("IT5", "Priority", "P1") in board.select  # top of two → P1
    assert all(item != "IT6" for item, _, _ in board.select)  # human's P3 untouched
