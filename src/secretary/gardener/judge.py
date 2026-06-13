"""Borderline-superseded judge: does a closed item's work cover an open request?

Only the borderline distance band of `probably_superseded` reaches the judge. Same
discipline as the labeler/organizer judges: an injectable `complete` for offline tests,
abstain-on-failure (None), never fatal. Judge-off ⇒ the borderline finding is reported
as borderline rather than promoted.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from secretary.config import Settings
from secretary.llm import make_complete

log = logging.getLogger(__name__)

# (open issue dict, closed item ref dict) -> True covers | False not | None abstain.
SupersedeJudge = Callable[[dict, dict], "bool | None"]

_VERDICT_RE = re.compile(r"\b(YES|NO)\b", re.IGNORECASE)


def build_supersede_prompt(issue: dict, closed_ref: dict) -> str:
    body = (issue.get("body") or "").strip()[:1200]
    ref = f"{closed_ref.get('repo')}#{closed_ref['number']}" if closed_ref.get("repo") else f"#{closed_ref['number']}"
    return (
        "An open issue may already be handled by a closed, related item. Decide whether "
        "the closed item's work covers the open request. Answer only if confident.\n\n"
        f"Open issue: {issue.get('title', '')}\n{body or '(no body)'}\n\n"
        f"Closed item {ref}: {closed_ref.get('title', '')}\n\n"
        "Answer on one line: YES if the closed work covers the open request, NO otherwise."
    )


def parse_verdict(raw: str) -> bool | None:
    m = _VERDICT_RE.search(raw or "")
    if not m:
        return None
    return m.group(1).upper() == "YES"


def supersede_judge(complete: Callable[[str], str]) -> SupersedeJudge:
    def judge(issue: dict, closed_ref: dict) -> bool | None:
        try:
            raw = complete(build_supersede_prompt(issue, closed_ref))
        except Exception as exc:  # noqa: BLE001 - advisory, never fatal
            log.warning("supersede judge failed for #%s: %s", issue.get("number"), exc)
            return None
        return parse_verdict(raw)

    return judge


def default_supersede_judge(settings: Settings) -> SupersedeJudge:
    return supersede_judge(make_complete(settings))
