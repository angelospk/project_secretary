"""Retrieval for backlog Q&A: vector search + one-hop edge expansion (read-only).

Two-stage, mirroring how a maintainer answers from memory: first the items whose text
is closest to the question (exact cosine over the same embeddings everything else uses),
then one hop out along explicit graph edges — the question often matches the *symptom*
while the answer hangs off a neighbour (the PR that fixed it, the issue it duplicates).

`k` (`qa_top_k`) caps the primary vector hits. Edge hits are appended *after* them as
bonus context under their own caps (`qa_edge_per_hit`, `qa_max_edge_hits`) and never
displace a vector hit; they carry `score=None` because they were not ranked against the
question. Everything here calls only read helpers in `db.repo`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder

KINDS = ("issue", "pr")
_WS = re.compile(r"\s+")
_SNIPPET_CHARS = 200


@dataclass(frozen=True, order=True)
class Ref:
    """Stable identity of an indexed item; repo is part of it (numbers collide)."""

    kind: str
    repo: str
    number: int


@dataclass(frozen=True)
class Hit:
    ref: Ref
    title: str
    state: str
    score: float | None  # cosine similarity for vector hits; None for edge hits.
    why: Literal["vector", "edge"]
    snippet: str


def _snippet(row: dict) -> str:
    """A short, whitespace-collapsed snippet: body if present, else the title."""
    text = (row.get("body") or "").strip() or (row.get("title") or "").strip()
    return _WS.sub(" ", text)[:_SNIPPET_CHARS]


def query(
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    question: str,
    *,
    repo: str | None = None,
    k: int | None = None,
) -> list[Hit]:
    """Structured, ranked hits for a natural-language question.

    `repo=None` searches every indexed repo (cross-repo memory); pass a repo to scope.
    Vector hits come first (closest by cosine), then one-hop edge neighbours.
    """
    top_k = k if k is not None else settings.qa_top_k
    if top_k <= 0:
        raise ValueError("k must be positive")
    vector = embedder.encode_query(question)

    # --- vector stage: nearest items across both kinds, merged and capped to top_k ---
    candidates: list[tuple[float, str, dict]] = []  # (dist, kind, row)
    for kind in KINDS:
        for row in db_repo.similar(db, kind, vector, k=top_k, repo=repo):
            candidates.append((row["dist"], kind, row))
    candidates.sort(key=lambda c: c[0])

    seen: set[Ref] = set()
    vector_hits: list[Hit] = []
    for dist, kind, row in candidates:
        ref = Ref(kind, row.get("repo", ""), int(row["number"]))
        if ref in seen:
            continue
        seen.add(ref)
        vector_hits.append(
            Hit(
                ref=ref,
                title=row.get("title") or "",
                state=row.get("state") or "",
                score=round(1.0 - dist, 4),
                why="vector",
                snippet=_snippet(row),
            )
        )
        if len(vector_hits) >= top_k:
            break

    # --- edge stage: one hop out from each vector hit, in rank order, capped ---
    edge_hits: list[Hit] = []
    for src in vector_hits:
        if len(edge_hits) >= settings.qa_max_edge_hits:
            break
        per_hit = 0
        for nb_kind, nb_repo, nb_number in sorted(
            db_repo.neighbors(db, src.ref.kind, src.ref.repo, src.ref.number)
        ):
            if per_hit >= settings.qa_edge_per_hit:
                break
            if len(edge_hits) >= settings.qa_max_edge_hits:
                break
            ref = Ref(nb_kind, nb_repo, nb_number)
            if ref in seen:
                continue
            meta = db_repo.get_meta(db, nb_kind, nb_repo, nb_number)
            if meta is None:  # edge points at an item we haven't ingested yet
                continue
            seen.add(ref)
            per_hit += 1
            edge_hits.append(
                Hit(
                    ref=ref,
                    title=meta.get("title") or "",
                    state=meta.get("state") or "",
                    score=None,
                    why="edge",
                    snippet=_snippet(meta),
                )
            )

    return vector_hits + edge_hits
