"""Transparent priority scoring for milestone members.

A score blends structural signals GitHub already gives — positive reactions, how many
members depend on an item (graph centrality), discussion volume, priority labels, and
recency — into a value in [0, 1], optionally with an LLM judge layered on top. Every
score carries its component breakdown so the plan can show *why*.

Design decisions, per the Codex plan review:
- Min-max normalization is computed over **members only**, so the ranking is stable
  regardless of which suggested-add candidates surface this run (#3).
- A degenerate range (all equal, or one item) yields a neutral 0.0 — never a
  divide-by-zero or a misleading 1.0 (#4).
- Weights are validated >= 0 (in config) and **renormalized to sum to 1** over the
  active components, so the total is interpretable and the judge weight cleanly
  drops out when the judge is off (#5).
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from secretary.organizer.models import Item, PriorityScore

# The structural components, in display order. "judge" is appended only when active.
STRUCTURAL = ("react", "dep", "engage", "label", "fresh")


def minmax(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi == lo:  # all equal / single item → no differentiating signal
        return {k: 0.0 for k in values}
    span = hi - lo
    return {k: (v - lo) / span for k, v in values.items()}


def _label_rank(labels: Iterable[str], label_map: dict[str, float]) -> float:
    ranks = [label_map.get(label.lower(), 0.0) for label in labels]
    return min(max(max(ranks, default=0.0), 0.0), 1.0)


def _active_weights(weights: dict[str, float], components: Iterable[str]) -> dict[str, float]:
    """Keep positive weights for present components, renormalized to sum to 1."""
    active = {c: weights.get(c, 0.0) for c in components if weights.get(c, 0.0) > 0}
    total = sum(active.values())
    if total <= 0:
        return {}
    return {c: w / total for c, w in active.items()}


def rank_members(
    members: list[Item],
    *,
    weights: dict[str, float],
    label_map: dict[str, float],
    dependents: dict[int, int],
    judge_scores: dict[int, tuple[float, str]] | None = None,
) -> list[tuple[Item, PriorityScore]]:
    """Score and rank members by priority (desc). Pure: all inputs pre-fetched.

    `dependents` maps a member number to how many members depend on it. `judge_scores`
    (number -> (score, reason)) is present only when the LLM judge ran; when None the
    judge component is omitted and its weight renormalized away. An item missing from
    a present `judge_scores` means the judge abstained (transient failure) — that item
    is blended over the structural weights only, neither penalized nor boosted.
    """
    # Engagement counts are heavy-tailed: one viral issue would otherwise min-max every
    # other member to ~0. log1p compresses the tail before normalization — order is
    # preserved, only the spacing changes. Deps/fresh/label are not heavy-tailed.
    react = minmax({m.number: math.log1p(m.reactions) for m in members})
    engage = minmax({m.number: math.log1p(m.comments_count) for m in members})
    dep = minmax({m.number: float(dependents.get(m.number, 0)) for m in members})
    fresh = minmax({m.number: m.updated_at_epoch for m in members})

    components = list(STRUCTURAL) + (["judge"] if judge_scores is not None else [])
    active = _active_weights(weights, components)
    structural_active = _active_weights(weights, STRUCTURAL)

    scored: list[tuple[Item, PriorityScore]] = []
    for m in members:
        comps = {
            "react": react.get(m.number, 0.0),
            "dep": dep.get(m.number, 0.0),
            "engage": engage.get(m.number, 0.0),
            "label": _label_rank(m.labels, label_map),
            "fresh": fresh.get(m.number, 0.0),
        }
        reason = None
        item_active = active
        if judge_scores is not None:
            j = judge_scores.get(m.number)
            if j is None:
                item_active = structural_active  # judge abstained for this item
            else:
                comps["judge"] = j[0]
                reason = j[1]
        total = sum(item_active.get(c, 0.0) * comps[c] for c in comps)
        scored.append(
            (m, PriorityScore(m.number, round(total, 4), comps, judge_reason=reason))
        )

    scored.sort(key=lambda pair: (-pair[1].total, pair[0].number))
    return scored
