"""Pure classification: an issue's nearest category and the decision band.

No I/O — operates over pre-fetched vectors so it's trivially testable. The three bands
mirror the reranker's distance-band style: confident (accept), borderline (review →
ask the judge), and too-far (silence, never force a fit).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from secretary.labeler.centroids import Centroid

ACCEPT = "accept"
REVIEW = "review"
SILENCE = "silence"


@dataclass(frozen=True)
class Classification:
    number: int
    category: str | None  # nearest category key, or None when silenced
    label: str | None
    dist: float
    band: str
    # Evidence for "why this category": the runner-up and how clear the call was.
    runner_up: str | None = None      # label of the 2nd-nearest category, if any
    margin: float | None = None       # 2nd-nearest dist − nearest dist (bigger = clearer)


def _cosine_dist(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


def classify_issue(
    number: int,
    vector: list[float],
    centroids: list[Centroid],
    *,
    accept: float,
    review: float,
) -> Classification:
    """Classify one issue against the centroids by nearest cosine distance."""
    if not centroids:
        return Classification(number, None, None, 1.0, SILENCE)

    ranked = sorted(centroids, key=lambda c: _cosine_dist(vector, c.vector))
    nearest = ranked[0]
    dist = _cosine_dist(vector, nearest.vector)

    if dist <= accept:
        band = ACCEPT
    elif dist <= review:
        band = REVIEW
    else:
        # Too far for any category — leave it alone, carry no category.
        return Classification(number, None, None, round(dist, 4), SILENCE)

    runner_up: str | None = None
    margin: float | None = None
    if len(ranked) > 1:
        second = ranked[1]
        runner_up = second.label
        margin = round(_cosine_dist(vector, second.vector) - dist, 4)

    return Classification(number, nearest.key, nearest.label, round(dist, 4), band,
                          runner_up=runner_up, margin=margin)
