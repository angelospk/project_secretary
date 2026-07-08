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


def test_parse_timeline_skips_foreign_repo_sources_when_repo_given():
    # A cross-referenced event whose source issue lives in ANOTHER repo must not
    # become a same-repo edge (the endpoints are built with the target's repo).
    events = [
        {"event": "cross-referenced",
         "source": {"issue": {"number": 11,
                              "repository": {"full_name": "other/repo"}}}},
        {"event": "cross-referenced",
         "source": {"issue": {"number": 12,
                              "repository": {"full_name": "O/R"}}}},  # same, case-insensitive
        {"event": "cross-referenced", "source": {"issue": {"number": 13}}},  # no repo info: keep
    ]
    refs = parse_timeline(7, events, repo="o/r")
    assert refs == [
        CrossRef(source=12, target=7, kind="mentions"),
        CrossRef(source=13, target=7, kind="mentions"),
    ]


def test_parse_timeline_without_repo_keeps_legacy_behavior():
    events = [
        {"event": "cross-referenced",
         "source": {"issue": {"number": 11,
                              "repository": {"full_name": "other/repo"}}}},
    ]
    assert parse_timeline(7, events) == [CrossRef(source=11, target=7, kind="mentions")]


def test_ingest_crossrefs_passes_repo_to_parse_timeline(monkeypatch):
    from secretary.ingest import pipeline

    captured = {}

    def fake_parse(number, events, *, repo=None):
        captured["repo"] = repo
        return []

    monkeypatch.setattr(pipeline, "parse_timeline", fake_parse)

    class FakeClient:
        def get_timeline(self, number):
            return []

    # pr_numbers={7} lets kind_of() resolve without a DB round-trip (db=None here).
    pipeline.ingest_crossrefs(None, "o/r", FakeClient(), 7, {7})
    assert captured["repo"] == "o/r"
