"""LLM judge: prompt/score parsing and graceful failure, no network."""

from __future__ import annotations

from secretary.config import Settings
from secretary.organizer.judge import LLMJudge, parse_score, rubric_hash


def _settings():
    return Settings(github_repo="o/r", judge_model="test-model")


def test_parse_score_normalizes_and_extracts_reason():
    score, why = parse_score("SCORE: 8\nWHY: big user impact")
    assert score == 0.8
    assert why == "big user impact"


def test_parse_score_clamps_and_handles_garbage():
    assert parse_score("SCORE: 12")[0] == 1.0
    assert parse_score("no score here")[0] == 0.0


def test_judge_uses_injected_completion():
    judge = LLMJudge(_settings(), complete=lambda prompt: "SCORE: 7\nWHY: solid")
    score, why = judge.score("title", "body", "rubric")
    assert score == 0.7
    assert why == "solid"


def test_judge_failure_is_neutral_not_fatal():
    def boom(_prompt):
        raise RuntimeError("api down")

    score, why = LLMJudge(_settings(), complete=boom).score("t", "b", "r")
    assert score == 0.0
    assert "unavailable" in why


def test_rubric_hash_is_stable_and_sensitive():
    assert rubric_hash("a") == rubric_hash(" a ")
    assert rubric_hash("a") != rubric_hash("b")
