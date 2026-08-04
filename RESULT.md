# Result: global `--quiet` flag for the secretary CLI

## What changed

- **`src/secretary/cli.py`**
  - Added a Typer app callback `_global_options(--quiet/-q)` that records the flag
    in a module-level `_QUIET`. A callback is the standard Typer place for options
    shared by every subcommand, so `--quiet` works in front of any command
    (e.g. `secretary --quiet run`).
  - `_setup_logging()` now sets the root logger level explicitly to
    `WARNING` when `--quiet` is set, else `INFO`. This is done with an explicit
    `setLevel` because `logging.basicConfig` is a no-op once the root logger is
    already configured, so it reliably suppresses INFO regardless of prior config.

- **`tests/test_cli_quiet.py`** (new)
  - `test_quiet_flag_accepted` — `runner.invoke(app, ["--quiet", "console-hash"], ...)`
    exits 0 (the global flag is accepted; `console-hash` was chosen as it does no I/O).
  - `test_quiet_suppresses_info` — with the flag set, `secretary.cli` logger is not
    enabled for INFO but is for WARNING.
  - `test_info_enabled_without_quiet` — without the flag, INFO stays enabled.

## Test output

Command: `.venv/bin/python -m pytest -q`

```
322 passed, 24 skipped, 1 warning in 4.71s
```

(The 24 skips and the Starlette httpx deprecation warning are pre-existing and
unrelated to this change.)

## Notes / follow-ups

- `-q` is offered as a short alias alongside `--quiet`; no existing subcommand uses
  `-q`, so there is no collision.
- Scope was limited to `src/secretary/cli.py` and the new test file.
