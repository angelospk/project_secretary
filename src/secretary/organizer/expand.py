"""Suggested adds: issues/PRs that probably belong in the milestone but aren't yet.

For each member we run the existing find-related retrieval, then filter to fresh,
in-scope candidates. Per the Codex review (#8) we anti-recommend: items already in the
milestone, closed/merged items (unless configured), plan issues, cross-repo
candidates (unless configured), and items already assigned to a different milestone.
Each surviving candidate is kept at its strongest (closest) match and capped.
"""

from __future__ import annotations

from surrealdb import Surreal

from secretary.config import Settings
from secretary.embeddings.embedder import Embedder
from secretary.organizer.models import Item, SuggestedAdd
from secretary.semantic.related import find_related

_CLOSED = ("closed", "merged")


def suggested_adds(
    db: Surreal,
    embedder: Embedder,
    repo: str,
    members: list[Item],
    *,
    settings: Settings,
    pair_set: set[frozenset[str]] | None = None,
    member_vectors: dict[tuple[str, int], list[float]] | None = None,
) -> list[SuggestedAdd]:
    member_keys = {(m.kind, m.repo, m.number) for m in members}
    milestone = members[0].milestone if members else None
    plan_label = settings.plan_issue_label.lower()
    best: dict[tuple[str, int], SuggestedAdd] = {}

    # Widen the retrieval pool past the cap: a member's nearest neighbours are largely
    # other members, which the filters below discard — fetching only expand_max would
    # let them crowd real candidates out entirely.
    pool = settings.expand_max + len(members)

    for m in members:
        vector = member_vectors.get((m.kind, m.number)) if member_vectors else None
        for ri in find_related(
            db, embedder, m.repo, m.number,
            k=pool, per_kind=pool, include_weak=False, pair_set=pair_set,
            vector=vector,
        ):
            if (ri.kind, ri.repo, ri.number) in member_keys:
                continue
            if ri.repo != repo and not settings.expand_cross_repo:
                continue
            if ri.dist > settings.expand_threshold:
                continue
            if not settings.expand_include_closed and (ri.state or "").lower() in _CLOSED:
                continue
            if plan_label in {label.lower() for label in ri.labels}:
                continue
            if ri.milestone and ri.milestone != milestone:
                continue  # already planned into a different release
            key = (ri.repo, ri.number)
            signals = f" ({', '.join(ri.signals)})" if ri.signals else ""
            add = SuggestedAdd(
                ri.kind, ri.repo, ri.number, ri.title, round(ri.dist, 4),
                f"{ri.category}{signals}",
            )
            if key not in best or add.dist < best[key].dist:
                best[key] = add

    return sorted(best.values(), key=lambda a: a.dist)[: settings.expand_max]
