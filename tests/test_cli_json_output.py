"""--json output for the read-only query commands (related, ask).

The formatters are pure, so they are driven directly here — no DB, no LLM. The
CliRunner cases monkeypatch find_related/answer_question for the same reason.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from secretary import cli
from secretary.config import Settings
from secretary.qa.retrieve import Hit, Ref
from secretary.semantic.reranker import RelatedItem


def _item(**overrides) -> RelatedItem:
    kwargs = dict(
        kind="issue", number=7, title="Flaky sync", state="open", dist=0.123,
        category="duplicate", confidence=0.87, repo="o/other", signals=["title", "labels"],
    )
    kwargs.update(overrides)
    return RelatedItem(**kwargs)


def _hit(**overrides) -> Hit:
    kwargs = dict(
        ref=Ref(kind="issue", repo="o/r", number=12), title="Board sync drops items",
        state="open", score=0.42, why="vector", snippet="…",
    )
    kwargs.update(overrides)
    return Hit(**kwargs)


# --- pure formatters -------------------------------------------------------

def test_related_json_has_expected_fields():
    payload = json.loads(cli._related_json("o/r", 3, "Target title", [_item()]))
    assert payload["repo"] == "o/r"
    assert payload["number"] == 3
    assert payload["title"] == "Target title"
    (row,) = payload["related"]
    assert row["number"] == 7
    assert row["repo"] == "o/other"
    assert row["category"] == "duplicate"
    assert row["confidence"] == 0.87
    assert row["dist"] == 0.123
    assert row["signals"] == ["title", "labels"]


def test_related_json_keys_are_sorted():
    text = cli._related_json("o/r", 3, "t", [_item()])
    payload = json.loads(text)
    assert list(payload) == sorted(payload)
    assert list(payload["related"][0]) == sorted(payload["related"][0])


def test_related_json_empty_results():
    payload = json.loads(cli._related_json("o/r", 3, "t", []))
    assert payload["related"] == []


def test_related_lines_render_human_text():
    lines = cli._related_lines("o/r", 3, "Target title", [_item()])
    assert lines[0] == "Related to o/r#3 ('Target title'):"
    assert "o/other#7" in lines[1]
    assert "conf=0.87" in lines[1]
    assert "dist=0.123" in lines[1]
    assert "[title, labels]" in lines[1]


def test_related_lines_omit_repo_for_same_repo():
    lines = cli._related_lines("o/r", 3, "t", [_item(repo="o/r")])
    assert "#7" in lines[1]
    assert "o/r#7" not in lines[1]


def test_ask_json_has_expected_fields():
    payload = json.loads(cli._ask_json([_hit()], "an answer"))
    assert payload["answer"] == "an answer"
    (row,) = payload["hits"]
    assert row["ref"] == {"kind": "issue", "number": 12, "repo": "o/r"}
    assert row["why"] == "vector"
    assert row["score"] == 0.42
    assert row["title"] == "Board sync drops items"


def test_ask_json_keys_are_sorted():
    payload = json.loads(cli._ask_json([_hit()], None))
    assert list(payload) == sorted(payload)
    assert list(payload["hits"][0]) == sorted(payload["hits"][0])
    assert list(payload["hits"][0]["ref"]) == sorted(payload["hits"][0]["ref"])


def test_ask_json_null_score_for_edge_hits():
    payload = json.loads(cli._ask_json([_hit(score=None, why="edge")], None))
    assert payload["hits"][0]["score"] is None
    assert payload["hits"][0]["why"] == "edge"


def test_ask_json_no_hits():
    payload = json.loads(cli._ask_json([], None))
    assert payload == {"answer": None, "hits": []}


def test_ask_lines_render_human_text():
    lines = cli._ask_lines([_hit()], "an answer")
    assert lines[0] == "an answer"
    assert any("grounded on 1 indexed items" in line for line in lines)
    assert any("o/r#12" in line for line in lines)


def test_ask_lines_no_hits():
    lines = cli._ask_lines([], None)
    assert lines == ["no indexed items match (memory is only as fresh as the last sync)"]


# --- CLI wiring ------------------------------------------------------------

def _patch_common(monkeypatch):
    monkeypatch.setattr(cli, "_setup_logging", lambda: None)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(github_repo="o/r"))
    monkeypatch.setattr(cli, "LocalEmbedder", lambda *a, **kw: object())


def test_related_command_json(monkeypatch):
    _patch_common(monkeypatch)

    class _FakeDB:
        pass

    class _Ctx:
        def __enter__(self):
            return _FakeDB()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "surreal", lambda settings: _Ctx())
    monkeypatch.setattr(cli.db_repo, "pr_exists", lambda *a, **kw: False)
    monkeypatch.setattr(cli.db_repo, "get_meta", lambda *a, **kw: {"title": "Target"})
    monkeypatch.setattr(cli, "find_related", lambda *a, **kw: [_item()])

    result = CliRunner().invoke(cli.app, ["related", "3", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["related"][0]["category"] == "duplicate"


def test_ask_command_json(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        cli.qa_tools, "answer_question", lambda *a, **kw: ([_hit()], "synthesized")
    )

    result = CliRunner().invoke(cli.app, ["ask", "why is sync flaky?", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["answer"] == "synthesized"
    assert payload["hits"][0]["ref"]["number"] == 12


def test_ask_command_human_output_unchanged(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        cli.qa_tools, "answer_question", lambda *a, **kw: ([_hit()], "synthesized")
    )

    result = CliRunner().invoke(cli.app, ["ask", "why is sync flaky?"])
    assert result.exit_code == 0, result.output
    assert "synthesized" in result.output
    assert "grounded on 1 indexed items" in result.output
