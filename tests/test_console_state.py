"""Console-owned state: label overrides, next-release validation, digest merge."""

from __future__ import annotations

import pytest

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.console import state


@pytest.fixture()
def kv(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: store.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: store.__setitem__(key, value))
    return store


def test_label_override_round_trips(kv):
    state.set_label(None, "o/r", "notif", "Notifications")
    assert state.get_label_overrides(None, "o/r") == {"notif": "Notifications"}


def test_label_requires_key_and_name(kv):
    with pytest.raises(ValueError):
        state.set_label(None, "o/r", "", "x")
    with pytest.raises(ValueError):
        state.set_label(None, "o/r", "k", "  ")


def test_next_release_validates_items(kv):
    state.set_next_release(None, "o/r", ["bugs"], [{"kind": "issue", "number": 12}])
    got = state.get_next_release(None, "o/r")
    assert got["categories"] == ["bugs"]
    assert got["items"] == [{"kind": "issue", "number": 12}]


def test_next_release_rejects_bad_item(kv):
    with pytest.raises(ValueError):
        state.set_next_release(None, "o/r", [], [{"kind": "milestone", "number": 1}])
    with pytest.raises(ValueError):
        state.set_next_release(None, "o/r", [], [{"kind": "issue", "number": 0}])


def test_digest_override_validates_interval(kv):
    with pytest.raises(ValueError):
        state.set_digest(None, "o/r", True, 0)
    state.set_digest(None, "o/r", True, 14)
    assert state.get_digest_overrides(None, "o/r") == {"enabled": True, "interval_days": 14}


def test_effective_digest_merges_over_settings(kv):
    s = Settings(github_repo="o/r", digest_enabled=False, digest_interval_days=7,
                 digest_discord_webhook="https://wh")
    # no overrides → settings defaults; webhook reported as presence only
    eff = state.effective_digest(s, {})
    assert eff == {"enabled": False, "interval_days": 7, "discord_webhook_set": True}
    eff2 = state.effective_digest(s, {"enabled": True, "interval_days": 3})
    assert eff2["enabled"] is True and eff2["interval_days"] == 3
