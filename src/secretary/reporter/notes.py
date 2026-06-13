"""Release-notes drafts: what shipped in a milestone, grouped by organizer theme.

`dev` style is purely mechanical (works with zero LLM config). `user` style passes the
mechanical draft's *titles* through the judge plumbing to reword them into user-facing
language — rewording only. The LLM never decides what is in or out of the list (that is
the milestone's closed issues + merged PRs), so the draft cannot hallucinate a feature.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.organizer import themes
from secretary.organizer.models import Item

log = logging.getLogger(__name__)

_REWORD_INSTRUCTION = (
    "Rewrite this GitHub issue/PR title as a single user-facing release-note line. "
    "Keep it factual and short; do not invent scope. Title:\n"
)


def _reword(title: str, complete: Callable[[str], str]) -> str:
    try:
        out = complete(_REWORD_INSTRUCTION + title).strip().splitlines()
        return (out[0].strip() if out else title) or title
    except Exception as exc:  # noqa: BLE001 - rewording is best-effort, fall back to raw
        log.warning("title reword failed for %r: %s", title, exc)
        return title


def build_notes(
    db: Surreal,
    settings: Settings,
    repo: str,
    milestone: str,
    *,
    style: str = "dev",
    complete: Callable[[str], str] | None = None,
) -> str:
    """Render release notes for `milestone`. `user` style rewords titles via `complete`."""
    rows = db_repo.milestone_done_items(db, repo, milestone)
    if not rows:
        return f"No closed issues or merged PRs in milestone {milestone!r}."

    items = [Item.from_row(r) for r in rows]
    grouped = themes.group(items, priority_labels=set(settings.priority_label_map))

    lines = [f"# {milestone}", ""]
    for theme in grouped:
        lines.append(f"## {theme.name}")
        for item in sorted(theme.items, key=lambda it: it.number):
            title = _reword(item.title, complete) if (style == "user" and complete) else item.title
            lines.append(f"- {title} (#{item.number})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
