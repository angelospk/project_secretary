"""Supersede judge: prompt grounding and YES/NO parsing with abstention."""

from __future__ import annotations

from secretary.gardener import judge as judge_mod


def test_parse_verdict_handles_yes_no_and_abstain():
    assert judge_mod.parse_verdict("YES, clearly covered") is True
    assert judge_mod.parse_verdict("no — different request") is False
    assert judge_mod.parse_verdict("hard to say") is None


def test_prompt_includes_both_items():
    prompt = judge_mod.build_supersede_prompt(
        {"number": 1, "title": "add export", "body": "we want CSV export"},
        {"number": 80, "repo": "o/r", "title": "CSV export shipped"},
    )
    assert "add export" in prompt and "o/r#80" in prompt


def test_judge_abstains_on_backend_failure():
    def boom(_prompt):
        raise RuntimeError("provider down")

    j = judge_mod.supersede_judge(boom)
    assert j({"number": 1, "title": "t"}, {"number": 80}) is None
