# project-secretary

A "smart secretary" for a GitHub backlog. It remembers every issue, PR, comment, and Project item across one or more repos, connects related work (even across repo boundaries), and can enrich a new issue with relevant history plus grounded codebase context.

It came out of a real problem: a backlog where the same idea gets filed twice, where the issue describing a feature lives in one repo and the code that implements it lives in another, and where nobody has time to wire those together by hand.

## What it does

- **Memory backbone** — syncs issues, PRs, comments, cross-references, and Project v2 items from your repos into a [SurrealDB](https://surrealdb.com) store (graph edges, records, and vectors).
- **Semantic layer** — local embeddings (via fastembed/ONNX, no torch) plus a heuristic reranker. Embeddings find candidates; the reranker decides which are actually a duplicate, an overlap, prior context, or just noise, using structure you already have: shared labels, graph edges, milestone, open/closed state.
- **Cross-repo memory** — index several repos in one database. The same issue number in two repos never collides (composite ids), and a frontend issue can surface the backend PR that implements it.
- **DeepWiki responder** — for a given issue, retrieve related history and ask [DeepWiki](https://deepwiki.com) for file-level context about your codebase, then write a managed, idempotent section: a sticky comment, or into the issue body, Greptile-style.

## How it works

Everything is parameterized by config, so it is not tied to any one project. Point it at your repo(s), give it a SurrealDB and a GitHub token, and run the pipeline:

```bash
uv sync
cp .env.example .env   # then fill in your token + repos

# start SurrealDB (on-disk, single binary)
surreal start --user root --pass root surrealkv://./.data/surreal.db &

uv run secretary init-db        # apply schema
uv run secretary backfill       # full ingest of every configured repo
uv run secretary embed          # compute embeddings for issues/PRs
uv run secretary related 42     # show classified related items for issue #42
uv run secretary enrich 42      # dry-run: build the enrichment (no write)
uv run secretary enrich 42 --write --target comment   # post it live
```

For several repos, set `SECRETARY_GITHUB_REPOS=owner/api,owner/worker` and they land in one shared database. `secretary related` and `secretary enrich` take `--repo` to disambiguate; `backfill`, `reconcile`, and `run` loop over all of them.

## Why these choices

- **SurrealDB** gives graph edges and vector search in one embedded-or-server store, so the memory and the semantic layer share a backend.
- **Exact cosine search** rather than the HNSW operator. At backlog scale (hundreds to low thousands of items) exact is correct and trivially cheap, and it composes cleanly with a per-repo filter. The HNSW index stays defined for when a corpus actually needs it.
- **Local embeddings** keep it cheap and private. The embedder sits behind a small protocol, so you can swap in a hosted model.

## Status

Early, and honest about it. The memory backbone, semantic layer, reranker, and the DeepWiki responder all work and are tested, including against a live SurrealDB. The DeepWiki client talks to a reverse-engineered public endpoint with no SLA, so treat that path as best-effort: every call is bounded by a timeout and degrades gracefully. Don't hammer it.

This repo is the generic core. The original implementation it was extracted from (`opencouncil-secretary`) is a refined fork tuned for one project.

## License

MIT. See [LICENSE](LICENSE).
