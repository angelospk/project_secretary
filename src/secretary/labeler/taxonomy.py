"""The maintainer-owned thematic taxonomy (TOML), loaded and hashed.

Each top-level table is a category. The taxonomy is owned by a human — the labeler only
files issues into it, never invents categories. The content hash invalidates derived
caches (centroids, judge verdicts) cleanly when the taxonomy is edited.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str  # the TOML table name; stable identity
    description: str  # seeds the centroid and the judge prompt
    label: str  # the GitHub label to apply (defaults to the key)
    examples: tuple[int, ...]  # optional seed issue numbers


@dataclass(frozen=True)
class Taxonomy:
    categories: tuple[Category, ...]
    hash: str  # sha1 over the normalized content; changes when the taxonomy changes

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(c.label for c in self.categories)


def _category_fingerprint(c: Category) -> str:
    # Order-independent over examples; covers everything that shifts a centroid.
    examples = ",".join(str(n) for n in sorted(c.examples))
    return f"{c.key}\x1f{c.label}\x1f{c.description.strip()}\x1f{examples}"


def load_taxonomy(path: str) -> Taxonomy:
    """Parse a taxonomy TOML file into a hashed `Taxonomy`.

    A category's `label` defaults to its table name; `examples` defaults to empty. A
    non-list `examples` or non-integer entry raises, so misconfiguration is loud.
    """
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    categories: list[Category] = []
    for key, raw in data.items():
        if not isinstance(raw, dict):
            raise ValueError(f"taxonomy category {key!r} must be a table")
        examples = raw.get("examples", [])
        if not isinstance(examples, list) or any(not isinstance(n, int) for n in examples):
            raise ValueError(f"taxonomy category {key!r}: examples must be a list of ints")
        description = raw.get("description", "")
        label = raw.get("label", key)
        if not isinstance(description, str):
            raise ValueError(f"taxonomy category {key!r}: description must be a string")
        if not isinstance(label, str):
            raise ValueError(f"taxonomy category {key!r}: label must be a string")
        categories.append(
            Category(key=key, description=description, label=label, examples=tuple(examples))
        )

    categories.sort(key=lambda c: c.key)
    fingerprint = "\x1e".join(_category_fingerprint(c) for c in categories)
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    return Taxonomy(categories=tuple(categories), hash=digest)
