"""Pure status/priority decisions for the steward. No I/O.

Status is derived only from linked PRs (the one progress signal reliable enough to
automate) and only ever moves forward. Priority bucketing maps an organizer rank to a
P1–P4 single-select. Every function here is total and side-effect free.
"""

from __future__ import annotations

TODO = "Todo"
IN_PROGRESS = "In Progress"
DONE = "Done"

# Forward-only rank. Anything unrecognized (incl. None / "Todo" / a custom backlog
# column) ranks 0, so the steward can advance out of it but never demotes into it.
_STATUS_RANK = {IN_PROGRESS: 1, DONE: 2}


def _rank(status: str | None) -> int:
    return _STATUS_RANK.get(status or "", 0)


def desired_status_from_prs(pr_tokens: list[str]) -> str | None:
    """Status implied by an issue's linked PRs, or None when there is no signal.

    `pr_tokens` are each "merged" | "open" | "closed". A merged PR means the work
    landed (Done); an open PR means it is underway (In Progress); only closed-unmerged
    PRs (or none) carry no signal — we never invent a status from nothing.
    """
    if "merged" in pr_tokens:
        return DONE
    if "open" in pr_tokens:
        return IN_PROGRESS
    return None


def next_status(current: str | None, desired: str | None) -> str | None:
    """The status to write, or None for no change.

    Forward-only: never demote (a human dragging a card back is a decision), and never
    re-write the same value. A re-opened PR does not un-finish work marked Done.
    """
    if desired is None or _rank(desired) <= _rank(current):
        return None
    return desired


def priority_bucket(rank_index: int, total: int) -> str:
    """Map a 0-based organizer rank (0 = highest) to a P1–P4 quartile bucket."""
    if total <= 0:
        return "P4"
    quantile = rank_index / total
    if quantile < 0.25:
        return "P1"
    if quantile < 0.5:
        return "P2"
    if quantile < 0.75:
        return "P3"
    return "P4"
