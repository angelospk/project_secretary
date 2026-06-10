from secretary.github.crossrefs import CrossRef, parse_timeline


def test_parse_timeline_extracts_cross_references():
    events = [
        {"event": "labeled"},
        {"event": "cross-referenced", "source": {"issue": {"number": 11}}},
        {"event": "cross-referenced", "source": {"issue": {"number": 11}}},  # dup
        {"event": "cross-referenced", "source": {"issue": {"number": 7}}},  # self
        {"event": "cross-referenced", "source": {}},  # malformed
    ]
    refs = parse_timeline(7, events)
    assert refs == [CrossRef(source=11, target=7, kind="mentions")]


def test_parse_timeline_empty():
    assert parse_timeline(1, []) == []
