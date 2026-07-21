"""Global --quiet flag: accepted, and it raises the log level to WARNING."""

from __future__ import annotations

import logging

from typer.testing import CliRunner

from secretary import cli

runner = CliRunner()


def test_quiet_flag_accepted():
    # A global --quiet in front of a command that does no I/O must be accepted.
    result = runner.invoke(cli.app, ["--quiet", "console-hash"], input="pw\npw\n")
    assert result.exit_code == 0


def test_quiet_suppresses_info(monkeypatch):
    # With the flag set, _setup_logging must leave INFO below the threshold.
    cli._global_options(quiet=True)
    try:
        cli._setup_logging()
        assert not logging.getLogger("secretary.cli").isEnabledFor(logging.INFO)
        assert logging.getLogger("secretary.cli").isEnabledFor(logging.WARNING)
    finally:
        cli._global_options(quiet=False)


def test_info_enabled_without_quiet():
    cli._global_options(quiet=False)
    cli._setup_logging()
    assert logging.getLogger("secretary.cli").isEnabledFor(logging.INFO)
