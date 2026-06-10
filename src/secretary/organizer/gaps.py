"""Coherence checks over a milestone — the warnings section of the plan.

Surfaces four kinds of problem, all from data already in hand:
- gap: a member declares it depends on an issue that is NOT in the milestone.
- done: a member is already closed/merged but still assigned.
- duplicate: two members are near-identical (high cosine) and share a real label.
- stale_critical: a member many others depend on has gone quiet — flagged so low
  freshness can't bury a load-bearing issue (Codex review #7). Staleness is absolute
  (no update in `stale_days`), not relative: relative min-max freshness would flag
  the merely-oldest member of every milestone, however recently it was touched.
"""

from __future__ import annotations

import math
import time

from secretary.organizer.models import Item, Warning
from secretary.semantic.reranker import _GENERIC_LABELS, _title_overlap


def _cosine_dist(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


def _meaningful_labels(item: Item) -> set[str]:
    return {label.lower() for label in item.labels if label.lower() not in _GENERIC_LABELS}


def coherence(
    members: list[Item],
    *,
    embeddings: dict[int, list[float]] | None = None,
    dependents: dict[int, int] | None = None,
    dup_dist: float = 0.25,
    dup_title_overlap: float = 0.3,
    stale_days: float = 30.0,
    stale_dependents: int = 2,
    now_epoch: float | None = None,
) -> list[Warning]:
    embeddings = embeddings or {}
    dependents = dependents or {}
    member_numbers = {m.number for m in members}
    warnings: list[Warning] = []

    # gap: dependency on a non-member.
    for m in members:
        missing = [n for n in m.depends_on if n not in member_numbers]
        for target in missing:
            warnings.append(
                Warning(
                    "gap",
                    f"#{m.number} depends on #{target}, which is not in this milestone",
                    [m.number, target],
                )
            )

    # done: already closed/merged.
    for m in members:
        if (m.state or "").lower() in ("closed", "merged"):
            warnings.append(
                Warning("done", f"#{m.number} is already {m.state} but still assigned", [m.number])
            )

    # duplicate: high cosine + shared meaningful label.
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            va, vb = embeddings.get(a.number), embeddings.get(b.number)
            if not va or not vb:
                continue
            shared_label = bool(_meaningful_labels(a) & _meaningful_labels(b))
            # Near in vector space, sharing a real label, AND with overlapping titles —
            # the same triple the reranker requires, so merely-related items don't flag.
            if (
                shared_label
                and _cosine_dist(va, vb) <= dup_dist
                and _title_overlap(a.title, b.title) >= dup_title_overlap
            ):
                warnings.append(
                    Warning(
                        "duplicate",
                        f"#{a.number} and #{b.number} look like duplicates",
                        [a.number, b.number],
                    )
                )

    # stale_critical: load-bearing but quiet. Absolute age; unknown timestamps skip.
    now = time.time() if now_epoch is None else now_epoch
    for m in members:
        if (
            dependents.get(m.number, 0) >= stale_dependents
            and m.updated_at_epoch > 0
            and now - m.updated_at_epoch >= stale_days * 86400
        ):
            days = int((now - m.updated_at_epoch) // 86400)
            warnings.append(
                Warning(
                    "stale_critical",
                    f"#{m.number} is depended on by {dependents[m.number]} items "
                    f"but hasn't been updated in {days} days",
                    [m.number],
                )
            )

    return warnings
