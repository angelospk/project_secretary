"""Borderline-case judge: does an issue actually belong to a category?

Only the review band reaches the judge. It mirrors the priority judge's discipline: an
injectable `complete` so it's testable offline, and abstain-on-failure — a failed or
ambiguous call returns None, which the caller downgrades to a suggestion rather than a
wrong label. It never blocks a run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from secretary.config import Settings
from secretary.labeler.apply import JudgeFn
from secretary.labeler.taxonomy import Category
from secretary.organizer.judge import anthropic_complete

log = logging.getLogger(__name__)

_VERDICT_RE = re.compile(r"\b(YES|NO)\b", re.IGNORECASE)


def build_membership_prompt(title: str, body: str | None, category: Category) -> str:
    excerpt = (body or "").strip()[:1500]
    return (
        "Decide whether this GitHub issue belongs to the given category. Answer only "
        "if you are confident.\n"
        f"Category: {category.key}\n"
        f"Category means: {category.description.strip() or '(no description)'}\n\n"
        f"Issue title: {title}\n"
        f"Issue body:\n{excerpt or '(no body)'}\n\n"
        "Answer on one line: YES if it clearly belongs, NO otherwise."
    )


def parse_membership(raw: str) -> bool | None:
    """First YES/NO token → bool; nothing recognizable → None (abstain)."""
    m = _VERDICT_RE.search(raw or "")
    if not m:
        return None
    return m.group(1).upper() == "YES"


def membership_judge(complete: Callable[[str], str]) -> JudgeFn:
    """Wrap a `complete(prompt) -> str` backend into a JudgeFn for the labeler."""

    def judge(title: str, body: str | None, category: Category) -> bool | None:
        try:
            raw = complete(build_membership_prompt(title, body, category))
        except Exception as exc:  # noqa: BLE001 - advisory, never fatal
            log.warning("membership judge failed for %r: %s", title, exc)
            return None
        return parse_membership(raw)

    return judge


def anthropic_membership_judge(settings: Settings) -> JudgeFn:
    return membership_judge(lambda prompt: anthropic_complete(settings, prompt))
