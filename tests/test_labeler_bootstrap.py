"""Taxonomy bootstrap: propose a starter taxonomy from a repo's existing labels.

A cold-start helper — it never writes the taxonomy file, it prints TOML the operator
edits and saves. Generic labels (bug/enhancement/…) are skipped: they are workflow
labels, not thematic categories the labeler should file into.
"""

from __future__ import annotations

import tomllib

from secretary.labeler.bootstrap import propose_taxonomy


def _issues():
    return [
        {"number": 1, "labels": ["auth", "bug"]},
        {"number": 2, "labels": ["auth"]},
        {"number": 3, "labels": ["auth", "billing"]},
        {"number": 4, "labels": ["billing"]},
        {"number": 5, "labels": ["enhancement"]},      # generic only → ignored
        {"number": 6, "labels": ["one-off"]},          # below min_count → ignored
    ]


def test_proposes_categories_for_frequent_non_generic_labels():
    toml_text = propose_taxonomy(_issues(), min_count=2)
    data = tomllib.loads(toml_text)
    assert set(data) == {"auth", "billing"}          # generic + rare labels dropped
    assert data["auth"]["label"] == "auth"
    assert "description" in data["auth"]              # left blank for the operator
    assert set(data["auth"]["examples"]) <= {1, 2, 3}


def test_examples_are_capped_and_valid_ints():
    issues = [{"number": n, "labels": ["perf"]} for n in range(1, 20)]
    data = tomllib.loads(propose_taxonomy(issues, min_count=2, examples_per=3))
    assert len(data["perf"]["examples"]) == 3
    assert all(isinstance(n, int) for n in data["perf"]["examples"])


def test_label_with_special_chars_makes_a_safe_key_and_escaped_label():
    issues = [{"number": 1, "labels": ['area: "core"']},
              {"number": 2, "labels": ['area: "core"']}]
    text = propose_taxonomy(issues, min_count=2)
    data = tomllib.loads(text)               # must parse — quotes escaped, key TOML-safe
    (key,) = data.keys()
    assert data[key]["label"] == 'area: "core"'


def test_empty_when_nothing_qualifies():
    data = tomllib.loads(propose_taxonomy([{"number": 1, "labels": ["bug"]}], min_count=2))
    assert data == {}
