# Result

## Task
Make `Settings.related_repo_pair_set` fail loudly on malformed input instead of
silently dropping chunks without a `+` separator (which turned a comma typo like
`SECRETARY_RELATED_REPO_PAIRS=o/a,o/b` into an empty set that silently disabled
cross-repo linking).

## Changes
- `src/secretary/config.py` — `related_repo_pair_set` now raises `ValueError` for
  any non-empty chunk that lacks a `+` separator or has a blank side, matching the
  loud-misconfig style of `parse_kv_floats`/`normalize_repo`. Blank chunks are
  still skipped, so an empty string yields an empty set.
- `tests/test_config.py` — added:
  - `test_related_repo_pairs_owner_a_plus_owner_b` (correct parse of `owner/a+owner/b`)
  - `test_related_repo_pairs_empty_gives_empty_set`
  - `test_related_repo_pairs_missing_plus_raises` (the `o/a,o/b` comma-typo case)
  - `test_related_repo_pairs_blank_side_raises` (`owner/a+`)

## Verification
- `.venv/bin/python -m pytest -q` → **323 passed, 24 skipped**.
- `ruff check src/secretary` (via `uv run ruff`) → **All checks passed!**
  (ruff is not installed in `.venv`; ran through `uv`.)

## Follow-ups
None.
