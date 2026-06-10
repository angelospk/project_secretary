"""Provider-agnostic completion: CLI backend, API request/parse, dispatch, creds."""

from __future__ import annotations

import pytest

from secretary import llm
from secretary.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(github_repo="o/r", **overrides)


# --- CLI backend (real subprocess, harmless commands) ------------------------

def test_cli_complete_pipes_prompt_on_stdin():
    s = _settings(judge_provider="cli", judge_cli_command="cat")
    assert llm.cli_complete(s, "SCORE: 7\nWHY: ok") == "SCORE: 7\nWHY: ok"


def test_cli_complete_substitutes_prompt_token():
    s = _settings(judge_provider="cli", judge_cli_command="printf %s {prompt}")
    assert llm.cli_complete(s, "hello world") == "hello world"


def test_cli_complete_raises_on_nonzero_exit():
    s = _settings(judge_provider="cli", judge_cli_command="false")
    with pytest.raises(RuntimeError):
        llm.cli_complete(s, "x")


def test_cli_complete_requires_a_command():
    with pytest.raises(RuntimeError):
        llm.cli_complete(_settings(judge_provider="cli"), "x")


def test_cli_complete_missing_binary_raises():
    s = _settings(judge_provider="cli", judge_cli_command="oc-no-such-binary-xyz")
    with pytest.raises(RuntimeError):
        llm.cli_complete(s, "x")


def test_make_complete_dispatches_to_cli():
    complete = llm.make_complete(_settings(judge_provider="cli", judge_cli_command="cat"))
    assert complete("ping") == "ping"


# --- HTTP backends (mocked httpx) --------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_anthropic_complete_parses_text_blocks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"], seen["headers"] = url, headers
        return _Resp({"content": [
            {"type": "text", "text": "SCORE: 8\n"},
            {"type": "thinking", "text": "ignored"},
        ]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.anthropic_complete(_settings(judge_provider="anthropic"), "p")
    assert out == "SCORE: 8\n"
    assert "anthropic.com" in seen["url"]
    assert seen["headers"]["x-api-key"] == "k"


def test_anthropic_complete_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm.anthropic_complete(_settings(), "p")


def test_openai_complete_parses_choices(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url.endswith("/chat/completions")
        assert headers["authorization"] == "Bearer k"
        return _Resp({"choices": [{"message": {"content": "SCORE: 6"}}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    assert llm.openai_complete(_settings(judge_provider="openai"), "p") == "SCORE: 6"


def test_gemini_complete_parses_candidates(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def fake_post(url, headers=None, json=None, timeout=None):
        assert ":generateContent" in url
        assert headers["x-goog-api-key"] == "k"
        return _Resp({"candidates": [{"content": {"parts": [{"text": "YES"}]}}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    assert llm.gemini_complete(_settings(judge_provider="gemini"), "p") == "YES"


def test_gemini_reads_google_api_key_alias(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert _settings(judge_provider="gemini").gemini_api_key == "g"


# --- credentials / validation -------------------------------------------------

def test_credentials_ready_per_provider(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.credentials_ready(_settings(judge_provider="anthropic")) is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert llm.credentials_ready(_settings(judge_provider="anthropic")) is True
    assert llm.credentials_ready(
        _settings(judge_provider="cli", judge_cli_command="cat")) is True
    assert llm.credentials_ready(_settings(judge_provider="cli")) is False


def test_invalid_provider_rejected():
    with pytest.raises(ValueError):
        _settings(judge_provider="bogus")
