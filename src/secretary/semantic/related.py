"""Find-related orchestration: semantic search across issues+PRs, then rerank.

Search spans every indexed repo (cross-repo memory). The reranker then applies the
cross-repo policy so an unrelated repo can't surface on generic-label coincidence.
This is the retrieval surface #3 consumes: given an item, return classified, ranked
related issues/PRs with a reason, ready to filter before DeepWiki.
"""

from __future__ import annotations

from surrealdb import Surreal

from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.embeddings.service import _doc_text
from secretary.semantic.reranker import WEAK_MATCH, RelatedItem, classify

KINDS = ("issue", "pr")


def find_related(
    db: Surreal,
    embedder: Embedder,
    repo: str,
    number: int,
    *,
    k: int = 5,
    per_kind: int = 10,
    include_weak: bool = False,
    pair_set: set[frozenset[str]] | None = None,
    vector: list[float] | None = None,
) -> list[RelatedItem]:
    """Classified related items for `repo#number`, searched across all repos.

    Pass `vector` to skip the stored-embedding fetch when the caller already has it
    (e.g. the organizer batch-loads every member's vector once).
    """
    kind = "pr" if db_repo.pr_exists(db, repo, number) else "issue"
    target = db_repo.get_meta(db, kind, repo, number)
    if target is None:
        raise ValueError(f"{kind} {repo}#{number} not found")

    # Reuse a provided vector, else the stored embedding, else encode the text.
    if vector is None:
        vector = db_repo.get_embedding(db, kind, repo, number)
    if vector is None:
        vector = embedder.encode_query(_doc_text(target))

    edges = db_repo.neighbors(db, kind, repo, number)  # {(kind, repo, number)}

    items: list[RelatedItem] = []
    for cand_kind in KINDS:
        for hit in db_repo.similar(db, cand_kind, vector, k=per_kind):  # repo=None: all repos
            cand_repo = hit.get("repo", "")
            if cand_kind == kind and hit["number"] == number and cand_repo == repo:
                continue  # self
            has_edge = (cand_kind, cand_repo, hit["number"]) in edges
            same_repo = cand_repo == repo
            pair_allowed = same_repo or (
                pair_set is not None and frozenset({repo, cand_repo}) in pair_set
            )
            items.append(
                classify(
                    target,
                    cand_kind,
                    hit,
                    hit["dist"],
                    has_edge,
                    same_repo=same_repo,
                    pair_allowed=pair_allowed,
                )
            )

    if not include_weak:
        items = [i for i in items if i.category != WEAK_MATCH]

    # Rank: explicit edges and confidence first, then proximity.
    items.sort(key=lambda i: (-i.confidence, i.dist))
    return items[:k]
