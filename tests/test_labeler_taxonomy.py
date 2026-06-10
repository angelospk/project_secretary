"""Taxonomy loading: parsing, label defaults, content-sensitive hash."""

from __future__ import annotations

import textwrap

import pytest

from secretary.labeler.taxonomy import load_taxonomy

TOML = """
[notifications]
description = "Email/WhatsApp/SMS delivery"
examples = [412, 398]
label = "notifications"

[transcript]
description = "Speaker segments and word-level data"
examples = [256]
"""


def _write(tmp_path, content: str) -> str:
    p = tmp_path / "taxonomy.toml"
    p.write_text(textwrap.dedent(content))
    return str(p)


def test_load_parses_categories(tmp_path):
    tax = load_taxonomy(_write(tmp_path, TOML))
    assert {c.key for c in tax.categories} == {"notifications", "transcript"}
    notif = next(c for c in tax.categories if c.key == "notifications")
    assert notif.label == "notifications"
    assert notif.examples == (412, 398)


def test_label_defaults_to_key(tmp_path):
    tax = load_taxonomy(_write(tmp_path, TOML))
    transcript = next(c for c in tax.categories if c.key == "transcript")
    assert transcript.label == "transcript"  # no explicit label → table name
    assert transcript.examples == (256,)


def test_labels_property_lists_all(tmp_path):
    tax = load_taxonomy(_write(tmp_path, TOML))
    assert set(tax.labels) == {"notifications", "transcript"}


def test_hash_is_stable_and_content_sensitive(tmp_path):
    a = load_taxonomy(_write(tmp_path, TOML))
    b = load_taxonomy(_write(tmp_path, TOML))
    assert a.hash == b.hash
    changed = load_taxonomy(_write(tmp_path, TOML.replace("delivery", "delivery + queue")))
    assert changed.hash != a.hash


def test_bad_examples_raise(tmp_path):
    bad = '[x]\ndescription = "y"\nexamples = ["not-an-int"]\n'
    with pytest.raises(ValueError):
        load_taxonomy(_write(tmp_path, bad))
