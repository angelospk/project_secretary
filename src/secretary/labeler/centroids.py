"""Category centroids: the vector each issue is classified against.

A category's centroid is the normalized mean of its description embedding and the
stored embeddings of its example issues. Centroids are cached in `organizer_kv` keyed
on the taxonomy hash, so editing the taxonomy invalidates them cleanly — the same
cache discipline the judge uses (organizer invariant #4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from surrealdb import Surreal

from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.labeler.taxonomy import Taxonomy


@dataclass(frozen=True)
class Centroid:
    key: str
    label: str
    vector: list[float]


def _mean_normalized(vectors: list[list[float]]) -> list[float]:
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    mean = [x / len(vectors) for x in acc]
    norm = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / norm for x in mean]


def _cache_key(taxonomy: Taxonomy) -> str:
    return f"centroids:{taxonomy.hash}"


def build_centroids(
    db: Surreal, embedder: Embedder, repo: str, taxonomy: Taxonomy
) -> list[Centroid]:
    """Build (or load from cache) one centroid per category.

    Cache hit requires the same taxonomy hash; any edit recomputes. A category with no
    usable seed (blank description and no embedded examples) is skipped — it could only
    produce a meaningless zero centroid.
    """
    cached = db_repo.kv_get(db, repo, _cache_key(taxonomy))
    if isinstance(cached, dict) and cached.get("vectors"):
        by_key = {c.key: c for c in taxonomy.categories}
        out: list[Centroid] = []
        for key, vec in cached["vectors"].items():
            cat = by_key.get(key)
            if cat is not None:
                out.append(Centroid(key=key, label=cat.label, vector=list(vec)))
        return out

    centroids: list[Centroid] = []
    for cat in taxonomy.categories:
        seeds: list[list[float]] = []
        if cat.description.strip():
            seeds.append(embedder.encode_passages([cat.description])[0])
        for number in cat.examples:
            vec = db_repo.get_embedding(db, "issue", repo, number)
            if vec:
                seeds.append(vec)
        if not seeds:
            continue
        centroids.append(Centroid(cat.key, cat.label, _mean_normalized(seeds)))

    db_repo.kv_set(
        db, repo, _cache_key(taxonomy),
        {"vectors": {c.key: c.vector for c in centroids}},
    )
    return centroids
