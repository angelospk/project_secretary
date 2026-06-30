"""Propose a starter taxonomy from a repo's existing GitHub labels (cold-start helper).

The labeler files issues into a human-owned taxonomy but never invents one. This turns
the labels a repo already uses into a first draft the maintainer edits: each frequent,
non-generic label becomes a category with a few example issues, rendered as TOML. It is
advisory — the caller prints it; it never overwrites `taxonomy_path`.
"""

from __future__ import annotations

import re

from secretary.semantic.reranker import _GENERIC_LABELS

_KEY_STRIP = re.compile(r"[^a-z0-9]+")


def _key_for(label: str, taken: set[str]) -> str:
    """A TOML-table-safe, unique key derived from a label."""
    base = _KEY_STRIP.sub("_", label.lower()).strip("_") or "category"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key


def _toml_str(value: str) -> str:
    r"""A double-quoted TOML basic string with the spec's escapes (\\, \", control chars)."""
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{out}"'


def propose_taxonomy(
    issues: list[dict], *, min_count: int = 3, examples_per: int = 3,
    generic: set[str] | None = None,
) -> str:
    """Render a starter taxonomy TOML from `issues` ([{number, labels}]).

    A label becomes a category when it is non-generic and used by at least `min_count`
    issues. Up to `examples_per` issue numbers seed each category; the description is
    left blank for the operator to fill.
    """
    generic = _GENERIC_LABELS if generic is None else generic
    counts: dict[str, int] = {}
    examples: dict[str, list[int]] = {}
    for issue in issues:
        try:
            number = int(issue["number"])
        except (KeyError, TypeError, ValueError):
            continue
        for raw in issue.get("labels") or []:
            label = str(raw).strip()
            if not label or label.lower() in generic:
                continue
            counts[label] = counts.get(label, 0) + 1
            examples.setdefault(label, [])
            if len(examples[label]) < examples_per:
                examples[label].append(number)

    qualifying = sorted(
        (lbl for lbl, c in counts.items() if c >= min_count),
        key=lambda lbl: (-counts[lbl], lbl),
    )
    if not qualifying:
        return ""

    taken: set[str] = set()
    blocks: list[str] = [
        "# Starter taxonomy proposed from existing labels — EDIT before use.",
        "# Fill each description (it seeds the centroid and the judge prompt), then",
        "# save to your SECRETARY_TAXONOMY_PATH. Categories are thematic, not workflow.",
        "",
    ]
    for label in qualifying:
        key = _key_for(label, taken)
        nums = ", ".join(str(n) for n in examples[label])
        blocks += [
            f"[{key}]",
            f"label = {_toml_str(label)}",
            'description = ""',
            f"examples = [{nums}]",
            f"# {counts[label]} issue(s) carry this label",
            "",
        ]
    return "\n".join(blocks)
