"""Read-only backlog tools — the surface both the MCP server and `ask` build on.

Every method here calls only read helpers (`db.repo` selects, `find_related`,
`organizer.plan.build`); none writes. The read-only guarantee lives at this layer, not
in tool descriptions: there is simply no write path reachable from here. Inputs are
validated (kind, repo shape, number, k bounds, milestone length) so a malformed MCP
call fails cleanly instead of reaching the database.
"""

from __future__ import annotations

from secretary.config import Settings, normalize_repo
from secretary.db import repo as db_repo
from secretary.db.connection import surreal
from secretary.embeddings.embedder import Embedder
from secretary.organizer import plan as organizer_plan
from secretary.organizer.render import render as render_plan
from secretary.qa import retrieve, synth
from secretary.semantic.related import find_related

KINDS = ("issue", "pr")
_K_MAX = 50
_MILESTONE_MAX = 200


class NotFound(Exception):
    """A requested item/plan does not exist — surfaced as a structured tool error."""


def _validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind must be 'issue' or 'pr', got {kind!r}")
    return kind


def _validate_number(number: int) -> int:
    if int(number) <= 0:
        raise ValueError("number must be a positive integer")
    return int(number)


def _validate_k(k: int | None) -> int | None:
    if k is None:
        return None
    if not (1 <= int(k) <= _K_MAX):
        raise ValueError(f"k must be between 1 and {_K_MAX}")
    return int(k)


def _hit_dict(hit: retrieve.Hit) -> dict:
    return {
        "kind": hit.ref.kind,
        "repo": hit.ref.repo,
        "number": hit.ref.number,
        "title": hit.title,
        "state": hit.state,
        "score": hit.score,
        "why": hit.why,
        "snippet": hit.snippet,
    }


class BacklogTools:
    """Read-only operations over the indexed backlog.

    Holds a shared embedder (ONNX load is expensive) and opens a fresh DB connection per
    call — nothing is mutated, so there is no cross-call state to coordinate.
    """

    def __init__(self, settings: Settings, embedder: Embedder):
        self._settings = settings
        self._embedder = embedder

    def _repo(self, repo: str | None) -> str | None:
        return normalize_repo(repo) if repo else None

    def search_backlog(
        self, query: str, repo: str | None = None, k: int | None = None
    ) -> dict:
        """Vector + edge-expanded hits for a natural-language query."""
        if not query.strip():
            raise ValueError("query must not be empty")
        k = _validate_k(k)
        scope = self._repo(repo)
        with surreal(self._settings) as db:
            hits = retrieve.query(
                db, self._embedder, self._settings, query, repo=scope, k=k
            )
        return {"hits": [_hit_dict(h) for h in hits], "count": len(hits)}

    def get_item(self, kind: str, repo: str, number: int) -> dict:
        """Full record for one item: metadata, comments, and graph edges."""
        kind = _validate_kind(kind)
        number = _validate_number(number)
        scope = normalize_repo(repo)
        with surreal(self._settings) as db:
            meta = db_repo.get_meta(db, kind, scope, number)
            if meta is None:
                raise NotFound(f"no {kind} {scope}#{number}")
            comments = db_repo.comments_for(db, kind, scope, number)
            edges = [
                {"kind": k, "repo": r, "number": n}
                for k, r, n in sorted(db_repo.neighbors(db, kind, scope, number))
            ]
        return {"item": meta, "comments": comments, "edges": edges}

    def related(self, kind: str, repo: str, number: int, k: int | None = None) -> dict:
        """Classified related items for an issue/PR (semantic + reranker)."""
        _validate_kind(kind)  # accepted for symmetry; find_related re-detects kind
        number = _validate_number(number)
        k = _validate_k(k)
        scope = normalize_repo(repo)
        with surreal(self._settings) as db:
            if db_repo.get_meta(db, kind, scope, number) is None:
                raise NotFound(f"no {kind} {scope}#{number}")
            items = find_related(
                db, self._embedder, scope, number,
                k=k or 5, pair_set=self._settings.related_repo_pair_set,
            )
        return {
            "related": [
                {
                    "kind": it.kind, "repo": it.repo, "number": it.number,
                    "title": it.title, "category": it.category,
                    "confidence": round(it.confidence, 4), "dist": round(it.dist, 4),
                    "signals": list(it.signals),
                }
                for it in items
            ]
        }

    def gardener_findings(self, repo: str) -> dict:
        """Advisory backlog-hygiene findings for a repo (probably fixed/duplicate/dormant).

        The cheap deterministic path — no LLM judge — so an attached agent can pull
        "what looks closeable, and why" with evidence, never triggering a write. The
        gardener proposes; it never closes.
        """
        from datetime import datetime, timezone

        from secretary.gardener.garden import collect_findings

        scope = normalize_repo(repo)
        with surreal(self._settings) as db:
            findings = collect_findings(
                db, self._embedder, self._settings, scope,
                now=datetime.now(timezone.utc), judge=None,
            )
        return {
            "findings": [
                {
                    "issue": f.issue, "signal": f.signal, "confidence": f.confidence,
                    "summary": f.summary, "suggestion": f.suggestion,
                    "evidence": list(f.evidence),
                }
                for f in findings
            ],
            "count": len(findings),
        }

    def release_plan(self, repo: str, milestone: str) -> dict:
        """The organizer's current plan content for a milestone (rendered markdown)."""
        if not milestone.strip() or len(milestone) > _MILESTONE_MAX:
            raise ValueError("milestone must be a non-empty string under 200 chars")
        scope = normalize_repo(repo)
        with surreal(self._settings) as db:
            release = organizer_plan.build(
                db, self._embedder, self._settings, scope, milestone
            )
            if not release.ordered:
                raise NotFound(f"no items assigned to milestone {milestone!r} in {scope}")
            return {"repo": scope, "milestone": milestone, "plan": render_plan(release)}


def answer_question(
    settings: Settings,
    embedder: Embedder,
    question: str,
    *,
    repo: str | None = None,
    k: int | None = None,
    raw: bool = False,
) -> tuple[list[retrieve.Hit], str | None]:
    """Retrieve hits and, unless `raw`, synthesize a grounded answer if a model is set.

    Returns (hits, answer_or_None). Answer is None in raw mode or when synthesis is not
    configured — the caller renders the hits directly.
    """
    scope = normalize_repo(repo) if repo else None
    with surreal(settings) as db:
        hits = retrieve.query(db, embedder, settings, question, repo=scope, k=k)
    if raw or not synth.synthesis_enabled(settings):
        return hits, None
    return hits, synth.answer(settings, question, hits)
