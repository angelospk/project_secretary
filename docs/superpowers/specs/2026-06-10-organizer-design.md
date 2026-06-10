# Subsystem #4 — The Organizer

Date: 2026-06-10
Status: design approved (delegated — user asked to proceed on best judgment)

## Purpose

The first three subsystems are reactive and per-issue: someone files an issue, the
secretary enriches it. The organizer is the first proactive, backlog-wide subsystem.
It helps a maintainer **plan and assemble a release** out of a messy backlog instead
of reacting to one issue at a time.

Concretely it answers two questions:

1. *What belongs together in this release?* — assembly: suggested adds, dependency
   order, theme grouping, coherence gaps.
2. *What is most worth doing?* — prioritization: a transparent score per item,
   structural metrics by default, an optional configurable LLM judge layered on top.

## Control surface & data flow

The maintainer expresses intent through a **GitHub Milestone** — native input, no new
tooling. They assign issues to a milestone in the GitHub UI (or move cards on a board;
milestone is what we read). The organizer reads milestone membership from the DB
(every issue already carries its `milestone`), enriches it from the graph + semantic
layers already built, and writes one **living "Release plan" issue** — an idempotent
managed section, reusing `responder/section.py`.

```
maintainer (GitHub UI): assign #42, #58 → Milestone "v2.1"
        │
        ▼
organizer reads:  issues WHERE milestone = "v2.1"   (DB, no extra API calls)
        + graph edges (relates_to / mentions / linked_issues)
        + embeddings (semantic.related)
        + engagement signals (reactions, comments)
        ▼
builds ReleasePlan: themes · order · suggested adds · gaps · prioritized list
        ▼
renders markdown → managed section → "Release plan: v2.1" issue (find-or-create)
```

Everything is read-from-DB except the final optional write. No new GitHub reads are
needed for assembly; the milestone, edges, and embeddings are already ingested.

## Components — `secretary/organizer/`

Each module is small, pure where possible, and unit-testable without a live DB
(functions take already-fetched data; only `plan.py` touches the DB/embedder).

- `milestone.py` — `members(db, repo, milestone) -> list[Item]`: issues/PRs whose
  `milestone` matches. Pure DB read.
- `priority.py` — `score(item, signals, weights, judge=None) -> PriorityScore`:
  the hybrid formula below. Pure function; the LLM judge is injected (or None).
- `judge.py` — optional LLM judge. `LLMJudge.score(item, rubric) -> (float, str)`.
  Anthropic Messages API over the existing httpx client; gated by config. No new
  hard dependency. Returns 0–1 plus a one-line justification. Absent ⇒ metrics only.
- `expand.py` — `suggested_adds(db, embedder, repo, members, ...) -> list[Add]`:
  related/blocking issues NOT already in the milestone, via `semantic.related` +
  graph neighbours of members. Dedup against members; threshold + cap. Per Codex
  review #8, anti-recommend: closed/merged items (unless `expand_include_closed`),
  plan issues themselves (the `release-plan` label), and items already in another
  milestone. Cross-repo candidates are excluded unless `expand_cross_repo` is set.
- `order.py` — `dependency_order(members, depends_on) -> list[Item]`: topological
  order driven **only** by typed directed `depends_on` edges. Per Codex review, weak
  edges must not drive ordering: `mentions`/`relates_to` are annotations only, and a
  PR's `closes #N` means "PR resolves N" (a *resolves* edge), NOT "N depends on PR" —
  it must never reverse into an ordering constraint. `depends_on` is derived from
  body keywords ("blocked by #N", "depends on #N", "needs #N", "requires #N") by
  `models.depends_on_refs`, restricted to in-milestone targets in `order.py`. Cycles
  and ties break deterministically by (open-before-closed,
  dependents desc, number). Items with no `depends_on` edges keep stable number order.
- `themes.py` — `group(members) -> list[Theme]`: cluster members for readability.
  v1 groups by dominant shared label, falling back to "Other". Embedding-based
  clustering is a noted follow-up, not v1.
- `gaps.py` — `coherence(members, depends_on, scores) -> list[Warning]`: members whose
  `depends_on` target is not in the milestone (gap), members already closed/merged
  (done), likely duplicates among members (high cosine + shared labels), and — per
  Codex review #7 — "stale but highly depended-on" members surfaced as a warning so
  low freshness can't bury a load-bearing issue.
- `plan.py` — orchestrator. Assembles a `ReleasePlan` dataclass and renders markdown.
  The only module that wires DB + embedder + judge together.
- `writer.py` — find-or-create the plan issue and upsert its managed section. Reuses
  `section.upsert`; refuses to overwrite a human-edited block (same guard as #3). Per
  Codex review #9, the plan issue is keyed in `sync_state` by `(repo, milestone)` →
  issue number, so a renamed milestone or a title collision across repos can't spawn a
  duplicate; the stored number is the source of truth, title match is only a fallback
  on first run.

## Priority scoring (the hybrid)

```
score(item) = w_react   * norm(reactions)        # 👍 / total positive reactions
            + w_dep      * norm(dependents)       # how many items depend on it (graph)
            + w_engage   * norm(comments+people)  # discussion volume
            + w_label    * label_priority(item)   # P0=1.0, P1=0.8 … (configurable map)
            + w_fresh    * freshness(item)        # recent activity, decayed
            [+ w_judge   * llm_judge(item, rubric)]   # opt-in, 0–1
```

- All weights live in config (`SECRETARY_PRIORITY_WEIGHTS`, a `key=value,…` string)
  with sensible defaults. Unknown keys ignored; missing keys take defaults. Per Codex
  review, weights are validated `>= 0` and **normalized to sum to 1** over the active
  components, so the final score is always in `[0, 1]` and interpretable.
- `label_priority` is a configurable label→rank map (`SECRETARY_PRIORITY_LABELS`),
  default `{p0:1.0, p1:0.8, p2:0.5, p3:0.2, critical:1.0, bug:0.4}`.
- Normalization is per-batch min-max, computed over **milestone members only** (Codex
  review #3): the member ranking must be stable regardless of which candidates the
  expander happens to surface this run. Suggested-add candidates are scored against the
  same member-derived min/max but ranked in their own list. Degenerate ranges (all
  equal, or a single item) return a neutral `0.0` for that component — never a
  divide-by-zero or a misleading `1.0`. Scores are presented as a *relative* ranking
  for this release, not an absolute value.
- The judge is **off by default**. When `SECRETARY_JUDGE_ENABLED=true` and an API key
  is present, each item is scored against `SECRETARY_JUDGE_RUBRIC` (free text, e.g.
  "Rate user impact, alignment with the release theme, and effort/risk"). The judge
  result is cached per full key `(item_id, updated_at, model, prompt_version,
  rubric_hash)` (Codex review #10) so re-runs don't re-pay and a model/rubric/prompt
  change correctly invalidates. Cache lives in `sync_state`-style records.
- Every score carries its component breakdown, so the plan can show *why* (e.g.
  `#42 0.81 [deps 0.9, react 0.7, judge 0.8]`). Transparency over magic.

## New ingested fields

Add to `Issue` (and PR where cheap), `from_api`, and the schema — both come free in
the REST issues payload, no extra calls:

- `reactions: int` — positive reaction count (`reactions.+1 + reactions.heart + …`,
  or `total_count` minus negatives). Default 0.
- `comments_count: int` — the `comments` field. Default 0.

Backfill/reconcile already re-upsert every issue, so these populate on the next sync.
Older DBs without them read as 0 (option/default), degrading gracefully.

## Config additions (all defaulted, generic)

```
SECRETARY_PLAN_ISSUE_LABEL=release-plan        # label applied to plan issues
SECRETARY_PRIORITY_WEIGHTS=react=0.25,dep=0.3,engage=0.15,label=0.2,fresh=0.1,judge=0.0
SECRETARY_PRIORITY_LABELS=p0=1.0,p1=0.8,p2=0.5,p3=0.2,critical=1.0,bug=0.4
SECRETARY_EXPAND_THRESHOLD=0.45                # max cosine dist for a suggested add
SECRETARY_EXPAND_MAX=10                        # cap on suggested adds
SECRETARY_JUDGE_ENABLED=false
SECRETARY_JUDGE_MODEL=claude-haiku-4-5-20251001
SECRETARY_JUDGE_RUBRIC=Rate user impact, alignment with the release theme, and effort/risk.
# ANTHROPIC_API_KEY read from env when the judge is enabled
```

## CLI

```
secretary plan <milestone> [--repo owner/name] [--judge] [--write]
```

- Default: print the rendered plan (dry run), no GitHub writes, judge per config.
- `--judge` forces the judge on for this run regardless of config default.
- `--write`: find-or-create the `Release plan: <milestone>` issue and upsert its
  managed section. Refuses if the block was human-edited (same guard as enrich).
- `--repo` disambiguates in multi-repo setups (same `_resolve_repo` helper).

A new `create_issue(title, body, labels)` is added to the GitHub client for the
find-or-create path; everything else reuses existing client methods.

## Testing strategy (TDD)

Unit (no DB):
- `priority`: weighting math, normalization, label map, missing-signal defaults,
  breakdown correctness; judge injected as a stub.
- `order`: directed deps respected; cycle/undirected fall back deterministically.
- `themes`: dominant-label grouping; "Other" bucket.
- `gaps`: detects out-of-milestone refs, done members, dup pairs.
- `expand`: dedups against members, respects threshold + cap (stub embedder).
- `config`: weight/label parsing, defaults, bad input.
- `models`: reactions/comments_count parsed from a sample payload.

Integration (live Surreal, skip if absent), in `test_integration_organizer.py` — the
Codex-recommended eval. Seed:
- `#1` P1, stale, many dependents.
- `#2` recent, many comments, no dependents.
- `#3` body "blocked by #1".
- PR `#4` body "closes #3".
- `#5` outside milestone, semantically related but closed.
- `#6` outside milestone, open, semantically related.
- `#7` body mentions `#1` only, no dependency language.
- a duplicate-like pair inside the milestone (high cosine + shared label).

Assert: order places `#1` before `#3`; `#4 closes #3` does NOT make `#3` depend on
`#4`; the plain mention `#7 → #1` does not affect order; `#6` is suggested, `#5` is
not; the duplicate warning appears; and with `dep` weight dominant, the high-dependent
stale `#1` ranks above the recent zero-structure `#2`.

## opencouncil wiring

opencouncil-secretary stays a thin config repo: it picks up the organizer via the
`project-secretary` git dependency bump, then sets in `.env`:

```
SECRETARY_JUDGE_ENABLED=true
SECRETARY_JUDGE_RUBRIC=Rate user impact for Greek municipalities, alignment with the
  current release theme, and implementation effort/risk. Favor accessibility and
  data-correctness issues.
```

so the same generic engine produces an opencouncil-tuned ranking. No code in the
deployment repo.

## Out of scope (follow-ups)

- Embedding-based theme clustering (v1 uses labels).
- Auto-writing milestone membership back to GitHub (organizer advises; the human owns
  membership — same trust stance as the responder).
- Drift maintenance loop (re-suggest on each poll cycle) — natural next step once the
  one-shot plan is solid.
