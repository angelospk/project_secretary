"""`secretary --version` prints the installed package version and exits 0."""

from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from secretary import cli


def test_version_flag_prints_version_and_exits_zero():
    result = CliRunner().invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    printed = result.stdout.strip()
    assert printed
    assert version("project-secretary") in printed
