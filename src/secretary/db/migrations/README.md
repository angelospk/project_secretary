# Schema migrations

Forward-only deltas the idempotent base schema (`../schema.surql`) cannot express —
field retypes, index rebuilds, data backfills. The runner lives in `../migrate.py`.

## Adding one

1. Create `NNNN_short_description.surql` (zero-padded, monotonic — `0001_…`, `0002_…`).
   Files apply in lexical order of the filename stem.
2. Write SQL that is safe to run once against an existing populated database. Prefer
   `IF NOT EXISTS` / `IF EXISTS` guards so a half-applied run can be re-run.
3. The runner records each applied file in the `schema_migration` ledger, so it never
   runs twice. Do **not** edit or renumber a migration after it has shipped in a tag —
   downstream databases have already recorded it; change it and they diverge silently.

## Versioning contract

- Additive change (new table/field/index) → put it in `schema.surql`, patch/minor bump.
- Destructive change (retype, drop, reindex, re-embed, backfill) → a migration file
  here, minor/major bump, and a release note. A DB that has applied a migration newer
  than the running build is refused (fail-fast), not silently downgraded.

This directory ships in the wheel even when empty; this file keeps it present.
