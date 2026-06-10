# Semantic Layer — Design Spec

**Date:** 2026-06-10
**Status:** In progress
**Subsystem:** #2 of the OpenCouncil Secretary platform (depends on #1 Memory backbone)

## Goal of this slice

Give every issue and PR a vector embedding and provide **semantic similarity
search** ("find related issues/PRs") over the SurrealDB store built in #1.

## Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Embedding model | **`paraphrase-multilingual-MiniLM-L12-v2`** via **fastembed** (ONNX, no PyTorch), 384-dim | Content is Greek + English; this multilingual model handles Greek well (validated). ~0.22GB, no torch → runs comfortably on a 1GB VM. Fully local, free, no external key. (Originally prototyped on `multilingual-e5-small`/sentence-transformers, but torch made a 1GB VM impractical — fastembed/ONNX drops the heavy stack.) |
| Vector store | SurrealDB **HNSW** index, `DIMENSION 384 DIST COSINE` | Already our store; KNN via `embedding <|k, EF|> $q` (validated on v3.1) |
| Embedding text | `title` + `body`; vectors L2-normalized | Symmetric model — no e5-style passage/query prefixes; cosine on normalized vectors |

### Validated in spike (2026-06-10)

- fastembed `paraphrase-multilingual-MiniLM-L12-v2` embeds Greek correctly; a Greek
  query ranked the Greek notification docs (~0.85/0.79 cosine) far above an
  unrelated English doc (~0.13) — no torch.
- SurrealDB HNSW: `DEFINE INDEX … HNSW DIMENSION 384 DIST COSINE`, query with
  `embedding <|k, EF|> $q` + `vector::distance::knn()`. (Old `<|k|>` removed in v3.)

## Scope

**In:** embedding generation + storage, HNSW index, similarity query (find-related),
CLI `embed` and `related <number>`.

**Deferred to #2b / #4:** relevance vs conflict classification (conceptual-related
vs file/functionality overlap) — needs heuristics or an LLM and more design.

## Architecture (additions to the existing repo)

```
src/secretary/embeddings/
  embedder.py     # Embedder protocol + E5SmallEmbedder (lazy model load, prefixes, normalize)
  service.py      # embed_pending(db, embedder): embed issues/PRs lacking an embedding
db/schema.surql   # + HNSW indexes on issue.embedding / pr.embedding; + milestone field
db/repo.py        # + set_embedding(), + similar()
cli.py            # + embed, + related
```

### Embedder

`Embedder` protocol: `encode_passages(list[str]) -> list[list[float]]` and
`encode_query(str) -> list[float]`. `LocalEmbedder` (fastembed/ONNX) lazy-loads the
model on first use and returns L2-normalized 384-vectors. Swappable (an API-backed
embedder, e.g. Gemini, can implement the same protocol later).

### Embedding service

`embed_pending(db, embedder, kinds=("issue","pr"))`: select rows where
`embedding IS NONE`, build text = `title` + `\n\n` + `body`, encode as passages in
batches, `set_embedding` each. Idempotent and resumable (only fills missing).

### Similarity

`repo.similar(db, kind, vector, k, ef=64)` →
`SELECT number, title, vector::distance::knn() AS dist FROM <kind>
 WHERE embedding <|k, ef|> $q ORDER BY dist`. `related <number>` embeds that item's
stored vector (or re-encodes its text as a query) and returns the nearest others.

## Incidental #1 addition

Add a `milestone` field (title) to `issue`/`pr` — it rides along in the existing
REST payload at no extra request cost and directly feeds the future #4 goal of
"prepare the next milestone/release". Git history, branches, and releases are a
separate, heavier data source — deferred past MVP.

## Testing

- Unit: embedder output shape (384) and normalization; `passage:`/`query:` prefixing.
- Integration (live DB): store embeddings, HNSW search returns the nearest item.

## Risks

- HNSW index over a partially-populated (`option`) field — verify it tolerates
  rows whose embedding is still NONE (skip vs error) during integration.
