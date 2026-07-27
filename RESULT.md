# RESULT — `--json` for the read-only query commands

## What changed

`src/secretary/cli.py`

- Extracted the output rendering of `related` and `ask` into four pure functions that
  take already-fetched results and return text — no DB, no embedder, no LLM:
  - `_related_lines(repo_name, number, title, items) -> list[str]`
  - `_related_json(repo_name, number, title, items) -> str`
  - `_ask_lines(hits, synthesized) -> list[str]`
  - `_ask_json(hits, synthesized) -> str`
- Added `--json` to both commands. The command bodies now just pick a formatter; the
  human output is byte-identical to before.
- Added `import json` at the top.

JSON shapes (`json.dumps(..., sort_keys=True)`, so key order is stable):

```json
// related --json
{"number": 3, "repo": "o/r", "title": "<target title>",
 "related": [{"category": "duplicate", "confidence": 0.87, "dist": 0.123,
              "kind": "issue", "number": 7, "repo": "o/other", "signals": ["title"],
              "state": "open", "title": "..."}]}

// ask --json
{"answer": "<synthesized or null>",
 "hits": [{"ref": {"kind": "issue", "number": 12, "repo": "o/r"},
           "score": 0.42, "state": "open", "title": "...", "why": "vector"}]}
```

The spec's required fields (`number`, `repo`, `category`, `confidence`, `dist` for
related; `ref`, `why`, `score` for ask) are all present; `kind`/`title`/`state`/`signals`
are carried along because they were already in the human output and consumers would
otherwise have to re-query for them.

`tests/test_cli_json_output.py` (new, 14 tests)

- Drives the four formatters directly with hand-built `RelatedItem` / `Hit` values —
  covers required fields, sorted key order, empty results, `score: null` for edge hits,
  and the same-repo `#N` shortening in the human renderer.
- Three `CliRunner` cases with `find_related` / `qa_tools.answer_question` /
  `surreal` / `get_settings` / `LocalEmbedder` monkeypatched, asserting the commands emit
  parseable JSON and that the default (non-`--json`) `ask` output is unchanged.

## Test output

```
$ .venv/bin/python -m pytest -q
333 passed, 24 skipped, 1 warning in 4.69s

$ .venv/bin/python -m ruff check src/secretary
All checks passed!
```

(Before implementing: 13 of the 14 new tests failed — TDD order held.)

## Follow-ups (not done — out of scope)

- `related --json` on a missing item still prints the plain-text
  `o/r#3 not found` to stdout and exits 1, rather than a JSON error object. A
  `--json` consumer has to treat the non-zero exit as the signal. Worth deciding
  on a convention if more commands get `--json`.
- Other read-only commands (`report`, `plan`, `board`) still print text only; the
  same formatter-extraction pattern would apply if they need piping too.
