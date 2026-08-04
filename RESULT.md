# Result: add `--version` flag to the secretary CLI

## What changed

- **`src/secretary/cli.py`**
  - Import `version as _pkg_version` from `importlib.metadata`.
  - Add a Typer app callback (`_main`) with a `--version` option wired to an
    eager callback (`_version_callback`). When passed, it prints
    `importlib.metadata.version("project-secretary")` and exits 0 via
    `typer.Exit()`. `is_eager=True` means it runs before any subcommand is
    resolved, so `secretary --version` works without a subcommand.

- **`tests/test_cli_version.py`** (new)
  - Uses `typer.testing.CliRunner` to invoke `cli.app` with `--version`.
  - Asserts exit code 0 and that the printed output is non-empty and contains
    `importlib.metadata.version("project-secretary")`.

## Approach

TDD: wrote the failing test first (exit code was 2 — no such option), then
added the callback until it passed. Followed the existing Typer framework and
the `from secretary import cli` import convention used in `test_organizer_cli.py`.

## Test output

`.venv/bin/python -m pytest -q`

```
312 passed, 24 skipped, 1 warning in 4.09s
```

Manual check via the console script:

```
$ .venv/bin/secretary --version
0.1.0
$ echo $?
0
```

`secretary --help` still lists all existing commands; the new `--version`
option appears under Options.

## Follow-ups

- None. The version is read from installed package metadata, so it stays in
  sync with `pyproject.toml` automatically.
