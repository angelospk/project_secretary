"""The gardener's signals as pure predicates over already-fetched records.

Each predicate takes plain data (an issue dict, its linked PRs, its related items) and
returns a `Finding` or None — no DB, no GitHub, no clock except an injected `now`. That
keeps every signal unit-testable in isolation and keeps the side effects (reads, writes,
veto bookkeeping) in `garden.py`.

Signals are ordered strongest-first; the orchestrator emits at most the strongest match
per issue, so a probably-fixed issue is never also nagged as dormant.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from secretary.semantic.reranker import MODERATE, STRONG

# Signal keys, strongest first (drives ordering and one-per-issue selection).
FIXED = "probably_fixed"
DUPLICATE_SIG = "probably_duplicate"
SUPERSEDED = "probably_superseded"
DORMANT = "dormant"

SIGNAL_ORDER = (FIXED, DUPLICATE_SIG, SUPERSEDED, DORMANT)

CONFIDENT = "confident"
BORDERLINE = "borderline"

# Veto marker a maintainer can drop in an issue body to silence the gardener for it.
KEEP_MARKER = "<!-- gardener: keep -->"


@dataclass(frozen=True)
class Finding:
    issue: int
    signal: str
    confidence: str  # CONFIDENT | BORDERLINE
    summary: str  # one-line headline
    suggestion: str  # the proposed (human) action
    fingerprint: str  # stable id of the recommendation target (for comment idempotence)
    evidence: tuple[str, ...] = field(default_factory=tuple)


def _fp(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def _engagement(issue: dict) -> int:
    return int(issue.get("reactions") or 0) + int(issue.get("comments_count") or 0)


def _has_priority(issue: dict, priority_labels: set[str]) -> bool:
    labels = {str(label).lower() for label in (issue.get("labels") or [])}
    return bool(issue.get("milestone")) or bool(labels & priority_labels)


# --- signal 1: probably fixed -------------------------------------------------

def probably_fixed(issue: dict, linked_prs: list[dict]) -> Finding | None:
    """A merged PR links the issue but it stayed open.

    A closing-ref edge (`closes #N`) to a merged PR is confident — the `closes` typo /
    cross-repo case GitHub can't auto-close. A merged PR that merely *mentions* the issue
    is borderline: a mention is not a fix.
    """
    merged = [p for p in linked_prs if p.get("merged_at")]
    if not merged:
        return None
    closing = [p for p in merged if p.get("via") == "relates_to"]
    use = closing or merged
    confidence = CONFIDENT if closing else BORDERLINE
    refs = sorted(f"{p['repo']}#{p['number']}" for p in use)
    verb = "closed by" if closing else "referenced by (verify it fixes this)"
    return Finding(
        issue=int(issue["number"]),
        signal=FIXED,
        confidence=confidence,
        summary=f"open, but {verb} merged {', '.join(refs)}",
        suggestion=f"Close as fixed by {', '.join(refs)} if the work covers it.",
        fingerprint=_fp(FIXED, *refs),
        evidence=tuple(f"merged PR {r}" for r in refs),
    )


# --- signal 2: probably duplicate ---------------------------------------------

def _survivor(a: dict, b: dict) -> tuple[dict, dict]:
    """Return (survivor, loser): higher engagement, then older, then lower number."""
    def key(x: dict):
        created = x.get("created_at") or datetime.max
        return (_engagement(x), -created.timestamp(), -int(x["number"]))

    return (a, b) if key(a) >= key(b) else (b, a)


def probably_duplicate(
    issue: dict, candidate: dict, priority_labels: set[str]
) -> Finding | None:
    """`issue` and an open same-repo DUPLICATE `candidate` are two reports of one thing.

    Propose closing the lower-engagement one and linking the survivor. Protective guard:
    never propose closing the issue that carries a priority signal (milestone / p-label)
    in favour of one that does not.
    """
    survivor, loser = _survivor(issue, candidate)
    if _has_priority(loser, priority_labels) and not _has_priority(survivor, priority_labels):
        return None  # don't close the prioritized one for an unprioritized twin
    s_num, l_num = int(survivor["number"]), int(loser["number"])
    return Finding(
        issue=l_num,
        signal=DUPLICATE_SIG,
        confidence=CONFIDENT,
        summary=f"likely duplicate of #{s_num} (more engagement)",
        suggestion=f"Close as duplicate of #{s_num}, or relabel if genuinely distinct.",
        fingerprint=_fp(DUPLICATE_SIG, s_num),
        evidence=(
            f"survivor #{s_num}: {_engagement(survivor)} reactions+comments",
            f"this #{l_num}: {_engagement(loser)} reactions+comments",
        ),
    )


# --- signal 3: probably superseded --------------------------------------------

def probably_superseded(
    issue: dict, closed_ref: dict, judge_verdict: bool | None = None
) -> Finding | None:
    """Strong relatedness to a CLOSED item whose closure plausibly covers this.

    `closed_ref` = {number, repo, dist, title}. dist <= STRONG is confident; a borderline
    distance (STRONG < dist <= MODERATE) defers to the judge: yes promotes to confident,
    no suppresses, abstain (None) reports as borderline. Judge-off keeps borderline.
    """
    dist = float(closed_ref["dist"])
    if dist > MODERATE:
        return None
    ref = f"{closed_ref.get('repo')}#{closed_ref['number']}" if closed_ref.get("repo") else f"#{closed_ref['number']}"
    if dist <= STRONG:
        confidence = CONFIDENT
    else:  # borderline band
        if judge_verdict is False:
            return None
        confidence = CONFIDENT if judge_verdict is True else BORDERLINE
    return Finding(
        issue=int(issue["number"]),
        signal=SUPERSEDED,
        confidence=confidence,
        summary=f"strongly related to closed {ref} — possibly already handled",
        suggestion=f"Check whether {ref} covers this; close as superseded if so.",
        fingerprint=_fp(SUPERSEDED, ref),
        evidence=(f"closed {ref} (distance {dist:.3f}): {closed_ref.get('title', '')}",),
    )


# --- signal 4: dormant --------------------------------------------------------

def dormant(
    issue: dict, now: datetime, dormant_days: int, priority_labels: set[str]
) -> Finding | None:
    """No activity for a long time and no priority signal — ask if it's still relevant.

    The weakest signal: proposes a ping, not a close. An issue with a milestone or a
    priority label is exempt (someone has already said it matters).
    """
    updated = issue.get("updated_at")
    if updated is None or _has_priority(issue, priority_labels):
        return None
    if now - updated < timedelta(days=dormant_days):
        return None
    days = (now - updated).days
    return Finding(
        issue=int(issue["number"]),
        signal=DORMANT,
        confidence=BORDERLINE,
        summary=f"no activity in {days} days, no milestone or priority label",
        suggestion="Ping the author: still relevant? Close if abandoned.",
        fingerprint=_fp(DORMANT),
        evidence=(f"last updated {updated:%Y-%m-%d} ({days} days ago)",),
    )
