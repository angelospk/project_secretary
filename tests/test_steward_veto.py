"""The per-field human veto: allow our own updates, freeze on human edits."""

from __future__ import annotations

from secretary.db import repo as db_repo
from secretary.steward.veto import can_write, record_write


def _patch(monkeypatch, kv: dict) -> None:
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))


def test_first_write_is_allowed_and_recorded(monkeypatch):
    kv: dict = {}
    _patch(monkeypatch, kv)
    assert can_write(None, "o/r", "IT", "Status", None) is True
    record_write(None, "o/r", "IT", "Status", "In Progress")
    assert kv["steward_set:IT:Status"] == "In Progress"


def test_update_allowed_when_current_matches_last_set(monkeypatch):
    kv = {"steward_set:IT:Status": "In Progress"}
    _patch(monkeypatch, kv)
    assert can_write(None, "o/r", "IT", "Status", "In Progress") is True


def test_human_change_vetoes_the_pair_permanently(monkeypatch):
    kv = {"steward_set:IT:Status": "In Progress"}
    _patch(monkeypatch, kv)
    # the board now shows a different value → a human moved it.
    assert can_write(None, "o/r", "IT", "Status", "Done") is False
    assert kv["steward_veto:IT:Status"] is True
    # the veto sticks even if the value later returns to ours.
    assert can_write(None, "o/r", "IT", "Status", "In Progress") is False
