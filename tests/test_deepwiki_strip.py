from secretary.deepwiki.client import strip_preamble


def test_strips_leading_reasoning_paragraphs():
    raw = (
        "Let me explore the codebase to understand the existing structure.\n\n"
        "The wiki already reveals an existing internal/watch package.\n\n"
        "I now have a comprehensive view of the codebase.\n\n"
        "## 1. Relevant Code Context\n\nThe `internal/watch` package already exists."
    )
    out = strip_preamble(raw)
    assert out.startswith("## 1. Relevant Code Context")
    assert "Let me explore" not in out
    assert "The wiki already reveals" not in out


def test_drops_stray_separators_between_reasoning_and_answer():
    raw = "Let me read all the relevant files.\n\n---\n\nThe real answer is here."
    assert strip_preamble(raw) == "The real answer is here."


def test_keeps_answer_with_no_preamble():
    raw = "## Summary\n\nEverything is already implemented."
    assert strip_preamble(raw) == raw


def test_all_reasoning_returns_original_rather_than_empty():
    raw = "Let me explore the codebase.\n\nI need to read the files."
    # Nothing looks like a real answer -> don't nuke it to "".
    assert strip_preamble(raw) == raw.strip()


def test_empty_input():
    assert strip_preamble("") == ""
