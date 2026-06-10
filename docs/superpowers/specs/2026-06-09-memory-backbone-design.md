# Memory Backbone — Design Spec

**Date:** 2026-06-09
**Status:** Approved (design), pending implementation plan
**Subsystem:** #1 of the OpenCouncil Secretary platform

## Context

The OpenCouncil project backlog (issues + PRs + Project board) is neglected. The
long-term goal is a "smart secretary": a system that remembers and connects
everything across issues/PRs, surfaces related prior work, enriches an automated
DeepWiki responder, and helps the backlog move faster (especially the many
UI-mockup issues that suit AI agents).

That platform decomposes into four subsystems:

| # | Subsystem | Depends on |
|---|-----------|-----------|
| **1. Memory backbone** | SurrealDB on a VM + GitHub sync of all issues/PRs/comments/project items | — |
| 2. Semantic layer | Embeddings + similarity + relevance/conflict classification | 1 |
| 3. DeepWiki responder (enriched) | On new issue/comment, retrieve related context, enrich prompt, call DeepWiki ×2, write managed section into the issue body | 1 (+2) |
| 4. Secretary / organizer | Keep the GitHub Project board organized, surface links, triage | 1 + 2 |

Build order: **1 → 3 → 2 → enrich 3 → 4.** Each subsystem gets its own
spec → plan → implementation cycle.

**This document covers subsystem #1 only.**

### Origin of the idea

Started from `plans/search-deepwiki.ts` in the opencouncil repo — a Bun script
that queries DeepWiki (Devin `ada` API) over POST + WebSocket for
`schemalabz/opencouncil`. The platform also queries `schemalabz/opencouncil-tasks`
(the async-task backend, a second DeepWiki repo). Those queries belong to
subsystem #3 and are out of scope here.

## Goal of this slice

Reliable **ingest + storage + freshness** of all GitHub issues, PRs, comments,
and Project v2 items for `schemalabz/opencouncil` into a SurrealDB instance.

Embeddings and semantic linking are explicitly **deferred to subsystem #2**. We
define `embedding` fields now (left `null`) and populate explicit GitHub
cross-references now; semantic links come later.

### Non-goals (this slice)

- No embedding generation, no vector search, no semantic similarity.
- No write-back to GitHub (read-only ingest).
- No responder / DeepWiki calls.
- No Project board mutation.

## Dataset sizing

Measured 2026-06-09 against `schemalabz/opencouncil`:

- ~167 issues (all-time), ~246 PRs (all-time) ≈ **~410 top-level items** + comments.

This is small. RAM/HNSW/perf are non-issues (≈1000 vectors × 384 dims ≈ 1.5 MB).
The real risk is **sync correctness and link quality**, not scale. We can be
generous in design without resource anxiety.

## Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | **SurrealDB** (graph + vectors in one) | One store for records, graph relations, and (later) vectors |
| SurrealDB deployment | **Server** (`surreal start`, `surrealkv` on disk) — not embedded | Embedded = single-process lock; multiple components (sync worker, later responder + secretary) must share one DB. Server is ~tens of MB, fits the VM |
| Language | **Python** (uv) | User preference for MVP speed |
| VM | None yet → **provider-agnostic, systemd-based**; develop **local first** | Defer infra; validate logic locally |
| Freshness | **Polling now, webhooks later** behind a `ChangeSource` interface | Polling needs no public endpoint and is robust; webhooks slot in without touching the ingest pipeline |
| GitHub auth | **Fine-grained PAT** (read: issues, PRs, contents, projects) | Sufficient for read-only ingest; GitHub App deferred |

## Architecture

### Repo layout (`~/projects/opencouncil-secretary`, private)

```
opencouncil-secretary/
├── pyproject.toml · .env.example · README.md
├── src/secretary/
│   ├── config.py                 # pydantic-settings: PAT, repo, surreal url/ns/db, interval
│   ├── github/
│   │   ├── client.py             # httpx REST + GraphQL, pagination, rate-limit handling
│   │   ├── models.py             # pydantic: Issue, PullRequest, Comment, ProjectItem
│   │   └── crossrefs.py          # parse timeline cross-reference events
│   ├── db/
│   │   ├── connection.py         # surrealdb async client
│   │   ├── schema.surql          # DEFINE TABLE/FIELD/INDEX
│   │   └── repo.py               # upsert/query functions
│   ├── ingest/
│   │   ├── pipeline.py           # normalize → upsert → relations (shared by all sources)
│   │   └── reconcile.py          # full backfill + incremental (watermark)
│   ├── sources/
│   │   ├── base.py               # ChangeSource interface
│   │   ├── polling.py            # PollingSource (timer-driven reconcile)  ← now
│   │   └── webhook.py            # WebhookSource stub                       ← later
│   └── cli.py                    # init-db · backfill · reconcile · run
├── deploy/
│   ├── surrealdb.service         # systemd unit (DB server)
│   ├── secretary-sync.service    # systemd unit (sync runner)
│   ├── secretary-sync.timer      # systemd timer (polling cadence)
│   └── README.md                 # provider-agnostic provisioning notes
└── tests/
```

### The `ChangeSource` abstraction (key to "polling now, webhooks later")

Both implementations feed the **same** `ingest(changeset)` pipeline:

- `PollingSource` — timer-driven incremental reconcile (now).
- `WebhookSource` — HTTP receiver translating GitHub webhook payloads into the
  same changeset shape (stub now, activated later without changing the pipeline).

The pipeline: `fetch full object from GitHub → normalize (pydantic) → upsert into
SurrealDB → derive cross-ref relations`.

### Data model (SurrealDB)

- `issue`: number (unique), title, body, state, author, labels[], created_at,
  updated_at, closed_at, url, `embedding` (null)
- `pr`: same as issue + merged_at, head/base refs, linked_issues[], `embedding` (null)
- `comment`: id, parent (record link → issue/pr), author, body, created_at, updated_at
- `project_item`: project field values, status, linked content (issue/pr)
- graph relations: `relates_to`, `mentions` — populated **in #1** from GitHub
  timeline cross-references (e.g. "PR #X closes #Y"). Semantic links → #2.
- `sync_state`: watermark `last_synced_at` per entity type.

### Sync mechanics

- **Backfill** once → **incremental** thereafter using `since` / `updated_at`
  watermark; idempotent upsert keyed on number/id.
- GitHub quirk: the issues REST endpoint returns PRs too (PRs are issues) —
  separate by the presence of the `pull_request` field.
- Rate limit: fine-grained PAT = 5000 req/hr; backfill ≈ a few thousand requests
  → comfortable.

### CLI commands

- `init-db` — apply `schema.surql` (and validate SurrealDB connectivity early)
- `backfill` — full ingest from scratch
- `reconcile` — incremental ingest since watermark
- `run` — loop: reconcile every N minutes (the polling runner)

## Testing strategy

- **Unit:** GitHub model parsing (fixtures of real API JSON), crossref parsing,
  idempotent upsert, watermark logic.
- **Integration:** against an ephemeral SurrealDB (`surreal start memory` or a
  test instance) — verify schema apply, upsert, record links, `RELATE`.

## Risks to validate early (spike during `init-db`)

- **SurrealDB Python SDK maturity** for record links + `RELATE` (core features,
  but confirm against the installed SDK version before building on them).
- **SurrealQL schema specifics** — validate when applying `schema.surql`.

These should be the first thing exercised in the implementation plan so we fail
fast if the SDK surprises us.

## Open items deferred to later subsystems

- Embedding model choice (cheap/free API vs local Ollama `all-minilm` /
  `nomic-embed-text`) → #2.
- Relevance vs conflict classification (file/functionality overlap) → #2.
- DeepWiki prompt enrichment with retrieved related issues/PRs → #3.
- Project board organization / triage automation → #4.
