"""Derive explicit cross-reference relations from GitHub timeline events.

A `cross-referenced` timeline event on item N means another item M mentioned N.
We record that as `M -mentions-> N`. PR closing keywords (`closes #N`) are handled
separately via `models.closing_refs` and recorded as `pr -relates_to-> issue`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrossRef:
    source: int  # the item doing the referencing
    target: int  # the item being referenced
    kind: str  # "mentions"


def parse_timeline(
    target_number: int, events: list[dict], *, repo: str | None = None
) -> list[CrossRef]:
    """Extract `mentions` cross-refs pointing at `target_number`.

    Pass `repo` ("owner/name") to drop events whose source issue lives in a
    *different* repo: the caller records these refs with the target's repo in both
    endpoints, so a foreign source would become a false same-repo edge. An event
    without repository info is kept (assumed same-repo). Cross-repo body references
    are handled separately by `pipeline.link_cross_repo_mentions`, which resolves the
    target's table instead of guessing.
    """
    refs: list[CrossRef] = []
    seen: set[int] = set()
    for event in events:
        if event.get("event") != "cross-referenced":
            continue
        source = event.get("source") or {}
        issue = source.get("issue") or {}
        source_number = issue.get("number")
        if not isinstance(source_number, int):
            continue
        if repo is not None:
            source_repo = (issue.get("repository") or {}).get("full_name")
            if source_repo and source_repo.strip().lower() != repo.strip().lower():
                continue
        if source_number == target_number or source_number in seen:
            continue
        seen.add(source_number)
        refs.append(CrossRef(source=source_number, target=target_number, kind="mentions"))
    return refs
