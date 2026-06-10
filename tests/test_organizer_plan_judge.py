"""Judge orchestration in plan._run_judge: caching, and abstention on failure."""

from __future__ import annotations

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.organizer import plan
from secretary.organizer.judge import LLMJudge
from secretary.organizer.models import Item


def _member(number: int, title: str) -> Item:
    return Item(kind="issue", repo="o/r", number=number, title=title, state="open")


def _fake_kv(monkeypatch, store: dict):
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: store.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: store.__setitem__(key, value))


def test_failed_judge_abstains_and_is_not_cached(monkeypatch):
    store: dict = {}
    _fake_kv(monkeypatch, store)

    def flaky(prompt: str) -> str:
        if "broken" in prompt:
            raise RuntimeError("api down")
        return "SCORE: 8\nWHY: solid"

    settings = Settings(github_repo="o/r")
    judge = LLMJudge(settings, complete=flaky)
    members = [_member(1, "good issue"), _member(2, "broken issue")]

    scores = plan._run_judge(None, settings, "o/r", members, judge)
    assert scores == {1: (0.8, "solid")}  # #2 absent: the judge abstained
    assert len(store) == 1  # only the successful score was cached

    # On a later run the API recovers: #2 is scored fresh, not poisoned by a cache.
    healthy = LLMJudge(settings, complete=lambda p: "SCORE: 4\nWHY: ok")
    scores = plan._run_judge(None, settings, "o/r", members, healthy)
    assert scores[1] == (0.8, "solid")  # served from cache
    assert scores[2] == (0.4, "ok")
