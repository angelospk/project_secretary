"""Dependency ordering of milestone members.

Per the Codex plan review, ordering is driven ONLY by typed directed `depends_on`
edges ("blocked by / depends on / needs / requires #N"). Weak links never order:
`mentions`/`relates_to` are annotations, and a PR's `closes #N` is a *resolves* edge,
not "N depends on the PR". A topological sort places dependencies before dependents;
ties and cycles break deterministically by (open-before-closed, dependents desc,
number asc), so the output is stable and a cycle degrades gracefully instead of
hanging.
"""

from __future__ import annotations

from secretary.organizer.models import Item

_CLOSED = ("closed", "merged")


def member_depends_on(members: list[Item]) -> dict[int, set[int]]:
    """Each member's `depends_on` targets restricted to other members in the set."""
    numbers = {m.number for m in members}
    return {
        m.number: {n for n in m.depends_on if n in numbers and n != m.number}
        for m in members
    }


def dependents_count(members: list[Item]) -> dict[int, int]:
    """How many members depend on each member (in-milestone graph centrality)."""
    counts = {m.number: 0 for m in members}
    for deps in member_depends_on(members).values():
        for target in deps:
            counts[target] = counts.get(target, 0) + 1
    return counts


def dependency_order(members: list[Item]) -> list[Item]:
    """Members in dependency order (a member's deps come before it)."""
    by_number = {m.number: m for m in members}
    deps = member_depends_on(members)
    dependents = dependents_count(members)

    def tiebreak(number: int) -> tuple[int, int, int]:
        item = by_number[number]
        closed = 1 if (item.state or "").lower() in _CLOSED else 0
        return (closed, -dependents.get(number, 0), number)

    remaining = dict(deps)  # number -> unresolved deps (mutated)
    placed: list[Item] = []
    placed_set: set[int] = set()

    while remaining:
        ready = [n for n, d in remaining.items() if d <= placed_set]
        if ready:
            chosen = min(ready, key=tiebreak)
        else:
            # Cycle: no node is fully satisfiable. Break it on the tie-break order.
            chosen = min(remaining, key=tiebreak)
        placed.append(by_number[chosen])
        placed_set.add(chosen)
        del remaining[chosen]

    return placed
