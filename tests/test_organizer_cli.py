"""CLI judge resolution: enabling the judge without a key warns instead of failing."""

from __future__ import annotations

from secretary import cli
from secretary.config import Settings
from secretary.organizer.judge import LLMJudge


def _settings(**overrides) -> Settings:
    return Settings(github_repo="o/r", **overrides)


def test_judge_off_when_not_requested():
    judge, warning = cli._resolve_judge(_settings(), force=False)
    assert judge is None
    assert warning is None


def test_judge_requested_without_key_warns_and_disables(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    judge, warning = cli._resolve_judge(_settings(), force=True)
    assert judge is None
    assert warning is not None
    assert "ANTHROPIC_API_KEY" in warning


def test_judge_requested_with_key_builds_judge(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    judge, warning = cli._resolve_judge(_settings(), force=True)
    assert isinstance(judge, LLMJudge)
    assert warning is None
