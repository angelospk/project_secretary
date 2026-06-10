"""Organizer config parsing: weight/label maps, validation, defaults."""

from __future__ import annotations

import pytest

from secretary.config import Settings, parse_kv_floats


def _settings(**kw):
    return Settings(github_repo="o/r", **kw)


def test_parse_kv_floats_basic():
    assert parse_kv_floats("a=1,b=0.5") == {"a": 1.0, "b": 0.5}
    assert parse_kv_floats("  A = 2 ") == {"a": 2.0}
    assert parse_kv_floats("") == {}


def test_parse_kv_floats_rejects_malformed():
    with pytest.raises(ValueError):
        parse_kv_floats("a")
    with pytest.raises(ValueError):
        parse_kv_floats("a=notanumber")


def test_weight_map_defaults_present():
    weights = _settings().priority_weight_map
    assert set(weights) == {"react", "dep", "engage", "label", "fresh", "judge"}


def test_negative_weight_rejected():
    with pytest.raises(ValueError):
        _settings(priority_weights="react=-0.1,dep=0.5").priority_weight_map


def test_label_map_defaults():
    labels = _settings().priority_label_map
    assert labels["p0"] == 1.0


def test_judge_off_by_default():
    assert _settings().judge_enabled is False
