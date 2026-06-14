"""Q&A synthesis: grounding, provider/model override wiring, raw-mode gating."""

from __future__ import annotations

from secretary.config import Settings
from secretary.qa import synth
from secretary.qa.retrieve import Hit, Ref


def _hit(number, kind="issue", title="t", state="open", snippet="snip"):
    return Hit(ref=Ref(kind, "o/r", number), title=title, state=state,
               score=0.9, why="vector", snippet=snippet)


def test_prompt_cites_only_retrieved_ids_and_states_count():
    hits = [_hit(42, title="login bug"), _hit(7, kind="pr", title="fix login")]
    prompt = synth.build_prompt("why is login broken?", hits)
    assert "o/r#42" in prompt and "o/r#7" in prompt
    assert "o/r#999" not in prompt
    assert "based on 2 indexed items" in prompt
    assert "Cite the item numbers" in prompt


def test_answer_with_no_hits_skips_the_llm():
    called = []
    out = synth.answer(Settings(github_repo="o/r"), "q", [],
                       complete=lambda p: called.append(p) or "x")
    assert called == []
    assert "No indexed items" in out


def test_answer_uses_injected_complete_over_the_prompt():
    seen = {}

    def fake_complete(prompt):
        seen["p"] = prompt
        return "  the answer  "

    out = synth.answer(Settings(github_repo="o/r"), "q", [_hit(1)], complete=fake_complete)
    assert out == "the answer"
    assert "o/r#1" in seen["p"]


def test_synthesis_disabled_without_a_model():
    assert synth.synthesis_enabled(Settings(github_repo="o/r", qa_model="")) is False


def test_synthesis_enabled_requires_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(github_repo="o/r", qa_model="some-model", qa_provider="anthropic")
    assert synth.synthesis_enabled(s) is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    s2 = Settings(github_repo="o/r", qa_model="some-model", qa_provider="anthropic")
    assert synth.synthesis_enabled(s2) is True


def test_qa_provider_falls_back_to_judge_provider():
    s = Settings(github_repo="o/r", judge_provider="gemini", qa_provider="")
    assert s.qa_provider_resolved == "gemini"
    s2 = Settings(github_repo="o/r", judge_provider="gemini", qa_provider="openai")
    assert s2.qa_provider_resolved == "openai"
