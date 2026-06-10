"""Orchestrator: assemble a ReleasePlan from a milestone.

The only organizer module that wires DB + embedder + (optional) judge together. Each
stage it calls is pure and separately tested; this just sequences them and handles the
judge cache.
"""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.organizer import expand, gaps, order, priority, themes
from secretary.organizer.judge import PROMPT_VERSION, LLMJudge, rubric_hash
from secretary.organizer.models import Item, ReleasePlan

log = logging.getLogger(__name__)


def _judge_cache_key(item: Item, model: str, rhash: str) -> str:
    return f"judge:{item.number}:{int(item.updated_at_epoch)}:{model}:{PROMPT_VERSION}:{rhash}"


def _run_judge(
    db: Surreal, settings: Settings, repo: str, members: list[Item], judge: LLMJudge
) -> dict[int, tuple[float, str]]:
    rhash = rubric_hash(settings.judge_rubric)
    scores: dict[int, tuple[float, str]] = {}
    for m in members:
        key = _judge_cache_key(m, judge.model, rhash)
        cached = db_repo.kv_get(db, repo, key)
        if isinstance(cached, dict) and "score" in cached:
            scores[m.number] = (float(cached["score"]), str(cached.get("reason", "")))
            continue
        result = judge.score(m.title, m.body, settings.judge_rubric)
        if result is None:  # transient failure: abstain for this item, never cache
            continue
        score, reason = result
        scores[m.number] = (score, reason)
        db_repo.kv_set(db, repo, key, {"score": score, "reason": reason})
    return scores


def build(
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    repo: str,
    milestone: str,
    *,
    judge: LLMJudge | None = None,
) -> ReleasePlan:
    members = [Item.from_row(r) for r in db_repo.milestone_members(db, repo, milestone)]
    if not members:
        return ReleasePlan(repo, milestone, [], [], [], [], [])

    ordered = order.dependency_order(members)
    dependents = order.dependents_count(members)

    # Batch-load every member's vector once; gaps keys by number, expand by (kind,
    # number). Issue/PR numbers don't collide within a repo, so the number-keyed view
    # is unambiguous.
    member_vectors = db_repo.milestone_embeddings(db, repo, milestone)
    embeddings = {number: vec for (_kind, number), vec in member_vectors.items()}

    judge_scores = _run_judge(db, settings, repo, members, judge) if judge else None
    ranked = priority.rank_members(
        members,
        weights=settings.priority_weight_map,
        label_map=settings.priority_label_map,
        dependents=dependents,
        judge_scores=judge_scores,
    )

    plan_themes = themes.group(ordered, priority_labels=set(settings.priority_label_map))
    suggested = expand.suggested_adds(
        db, embedder, repo, members,
        settings=settings, pair_set=settings.related_repo_pair_set,
        member_vectors=member_vectors,
    )
    warnings = gaps.coherence(members, embeddings=embeddings, dependents=dependents)

    return ReleasePlan(
        repo, milestone, ordered, plan_themes, ranked, suggested, warnings,
        judged=judge is not None,
    )
