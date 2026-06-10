"""Group milestone members into readable themes.

v1 groups by an item's dominant *meaningful* label — skipping generic labels (bug,
enhancement, …) and priority labels, which describe type/urgency rather than topic.
Items with no meaningful label fall into "Other". Embedding-based clustering is a
noted follow-up. Themes are ordered by size (desc) then name; "Other" always sorts
last so the plan leads with real themes.
"""

from __future__ import annotations

from secretary.organizer.models import Item, Theme
from secretary.semantic.reranker import _GENERIC_LABELS

OTHER = "Other"


def _theme_label(item: Item, ignore: set[str]) -> str:
    for label in item.labels:
        if label.lower() not in ignore:
            return label
    return OTHER


def group(items: list[Item], *, priority_labels: set[str] | None = None) -> list[Theme]:
    ignore = set(_GENERIC_LABELS) | {label.lower() for label in (priority_labels or set())}
    buckets: dict[str, list[Item]] = {}
    for item in items:  # preserves caller order within a theme
        buckets.setdefault(_theme_label(item, ignore), []).append(item)

    def sort_key(name: str) -> tuple[int, int, str]:
        return (1 if name == OTHER else 0, -len(buckets[name]), name.lower())

    return [Theme(name, buckets[name]) for name in sorted(buckets, key=sort_key)]
