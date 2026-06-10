"""Heuristic reranker: turn semantic candidates into classified related items.

Embeddings are candidate *generation* only (they over-cluster on shared domain
vocabulary, especially in Greek). This reranker layers the structured signals we
already store — labels, milestone, explicit graph edges, open/closed state — to
assign each candidate a category and confidence, so #3 can feed DeepWiki only the
high-signal matches with a reason.

No LLM: deterministic, cheap, fits a small VM. A learned reranker can replace this
behind the same `classify` signature later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Category vocabulary (Codex review).
DUPLICATE = "duplicate_candidate"
IMPLEMENTATION_OVERLAP = "implementation_overlap"
CONCEPTUAL_CONTEXT = "conceptual_context"
HISTORICAL_REFERENCE = "historical_reference"
WEAK_MATCH = "weak_match"

# Cosine-distance bands for the MiniLM model (smaller = closer).
STRONG = 0.40
MODERATE = 0.55
WEAK = 0.70

# \w (Unicode) covers Latin + Greek incl. accented chars (e.g. ύ, ή) that fall
# outside the plain α-ω range; text is lowercased before matching.
_TOKEN = re.compile(r"\w{4,}", re.UNICODE)

# Cross-repo guard (Codex review): generic labels co-occur in every repo, so they
# must not pull unrelated repos together. Only repo-specific labels count across repos.
_GENERIC_LABELS = {
    "bug", "enhancement", "documentation", "help wanted", "good first issue",
    "question", "wontfix", "duplicate", "invalid", "feature", "chore",
}


@dataclass
class RelatedItem:
    kind: str
    number: int
    title: str
    state: str
    dist: float
    category: str
    confidence: float
    repo: str = ""
    signals: list[str] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def _title_overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _is_closed(state: str | None) -> bool:
    # Merged PRs are state "closed" in GitHub; we treat closed/merged alike here.
    return (state or "").lower() in ("closed", "merged")


def classify(
    target: dict,
    cand_kind: str,
    cand: dict,
    dist: float,
    has_edge: bool,
    *,
    same_repo: bool = True,
    pair_allowed: bool = True,
) -> RelatedItem:
    """Classify one candidate relative to the target item.

    `same_repo` / `pair_allowed` carry the cross-repo policy: a candidate in another
    repo only links freely when the repo pair is allow-listed or there is an explicit
    edge. Otherwise generic labels are ignored, it can never be a DUPLICATE, a
    moderate-or-worse distance drops to weak, and confidence takes a penalty.
    """
    t_labels = set(target.get("labels") or [])
    c_labels = set(cand.get("labels") or [])
    shared = sorted(t_labels & c_labels)
    if not same_repo:
        shared = [lbl for lbl in shared if lbl.lower() not in _GENERIC_LABELS]
    same_ms = target.get("milestone") is not None and target.get("milestone") == cand.get("milestone")
    closed = _is_closed(cand.get("state"))
    cross_soft = (not same_repo) and (not has_edge) and (not pair_allowed)

    signals: list[str] = []
    if has_edge:
        signals.append("graph-edge")
    if not same_repo:
        signals.append("cross-repo:" + str(cand.get("repo", "")))
    if shared:
        signals.append("labels:" + ",".join(shared))
    if same_ms:
        signals.append("milestone:" + str(target.get("milestone")))

    title_ov = _title_overlap(target.get("title", ""), cand.get("title", ""))

    if has_edge:
        category = (
            HISTORICAL_REFERENCE
            if closed
            else (IMPLEMENTATION_OVERLAP if cand_kind == "pr" else CONCEPTUAL_CONTEXT)
        )
    elif dist > WEAK:
        category = WEAK_MATCH
    elif cross_soft and dist > MODERATE:
        # Unrelated repos need a strong signal to surface at all.
        category = WEAK_MATCH
    elif same_repo and dist <= STRONG and shared and title_ov >= 0.30:
        category = DUPLICATE  # duplicates only make sense within one repo
    elif closed and dist <= MODERATE:
        category = HISTORICAL_REFERENCE
    elif cand_kind == "pr" and dist <= MODERATE:
        category = IMPLEMENTATION_OVERLAP
    elif dist <= MODERATE:
        category = CONCEPTUAL_CONTEXT
    else:
        category = WEAK_MATCH

    # Confidence: closeness (cosine sim ~ 1 - dist) plus structured-signal boosts.
    confidence = max(0.0, 1.0 - dist)
    if has_edge:
        confidence += 0.15
    if shared:
        confidence += 0.10
    if same_ms:
        confidence += 0.05
    if cross_soft:
        confidence -= 0.10  # cross-repo penalty (unrelated, non-allow-listed repos)
    confidence = round(min(max(confidence, 0.0), 0.99), 3)

    return RelatedItem(
        kind=cand_kind,
        number=cand["number"],
        title=cand.get("title", ""),
        state=cand.get("state", ""),
        dist=round(dist, 4),
        category=category,
        confidence=confidence,
        repo=str(cand.get("repo", "")),
        signals=signals,
    )
