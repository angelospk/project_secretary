"""Embed pending issues/PRs and store their vectors in SurrealDB."""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary.db import repo
from secretary.embeddings.embedder import Embedder

log = logging.getLogger(__name__)

BATCH = 32


def _doc_text(row: dict) -> str:
    title = row.get("title") or ""
    body = row.get("body") or ""
    return f"{title}\n\n{body}".strip()


def embed_pending(
    db: Surreal, embedder: Embedder, kinds: tuple[str, ...] = ("issue", "pr")
) -> dict[str, int]:
    """Compute and store embeddings for rows lacking one. Resumable + idempotent."""
    counts: dict[str, int] = {}
    for kind in kinds:
        rows = repo.fetch_unembedded(db, kind)
        done = 0
        for start in range(0, len(rows), BATCH):
            batch = rows[start : start + BATCH]
            try:
                vectors = embedder.encode_passages([_doc_text(r) for r in batch])
            except Exception:  # noqa: BLE001 - one bad batch shouldn't abort the run
                log.exception("embedding batch failed (%s %s..); skipping", kind, batch[0]["number"])
                continue
            if len(vectors) != len(batch):
                log.error(
                    "embedder returned %s vectors for %s inputs; skipping batch",
                    len(vectors),
                    len(batch),
                )
                continue
            for row, vec in zip(batch, vectors):
                repo.set_embedding(db, kind, row["repo"], row["number"], vec)
                done += 1
            log.info("embedded %s/%s %ss", done, len(rows), kind)
        counts[kind] = done
    return counts
