from secretary.responder.prompt import build_query
from secretary.responder.compose import render_content
from secretary.semantic.reranker import RelatedItem


def _item(number, category="conceptual_context"):
    return RelatedItem(
        kind="issue", number=number, title=f"Title {number}", state="open",
        dist=0.4, category=category, confidence=0.6, signals=[],
    )


def test_build_query_includes_title_hints_and_questions():
    target = {"number": 5, "title": "Fix notifications", "body": "Long body here"}
    related = [_item(10), _item(11, "weak_match")]
    q = build_query(target, related)
    assert "issue #5: Fix notifications" in q
    assert "Long body here" in q
    assert "issue #10" in q
    assert "issue #11" not in q  # weak_match excluded from hints
    assert "Complexity assessment" in q
    assert "Open questions" in q


def test_render_content_handles_missing_context():
    content = render_content(
        "schemalabz/opencouncil",
        [_item(10)],
        {"schemalabz/opencouncil": "", "schemalabz/opencouncil-tasks": ""},
        run_ref="run #1",
    )
    assert "Possibly related history" in content
    assert "issue #10" in content
    assert "context unavailable" in content
    assert "run #1" in content
