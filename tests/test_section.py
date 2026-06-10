from secretary.responder import section


def test_build_and_extract_roundtrip():
    s = section.build_section(42, "hello **world**")
    m = section.extract(s)
    assert m is not None
    assert m.group("num") == "42"
    assert m.group("body") == "hello **world**"
    assert not section.was_human_edited(s)


def test_human_edit_detected():
    s = section.build_section(42, "original content")
    tampered = s.replace("original content", "a human changed this")
    assert section.was_human_edited(tampered) is True


def test_upsert_inserts_into_empty_and_existing_body():
    body = "User's original description.\n\nMore details."
    once = section.upsert(body, 7, "ctx v1")
    assert body in once
    assert section.extract(once) is not None

    # Re-running replaces in place — exactly one managed block.
    twice = section.upsert(once, 7, "ctx v2")
    assert twice.count("<!-- oc-secretary:") == 1
    assert "ctx v2" in twice
    assert "ctx v1" not in twice
    assert "User's original description." in twice


def test_upsert_into_empty_body():
    out = section.upsert("", 1, "content")
    assert out.startswith("<!-- oc-secretary:")
    assert section.extract(out) is not None
