"""Dataclasses the organizer passes between its (mostly pure) stages.

`Item` is a normalized milestone member or candidate, built from a DB row. The rest
are the structured outputs each stage produces and `plan.py` assembles into a
`ReleasePlan` for rendering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from secretary.github.models import depends_on_refs


def _to_epoch(value: object) -> float:
    """Best-effort seconds-since-epoch from a Surreal datetime / ISO string / None."""
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


@dataclass
class Item:
    kind: str  # "issue" | "pr"
    repo: str
    number: int
    title: str
    state: str
    labels: list[str] = field(default_factory=list)
    milestone: str | None = None
    reactions: int = 0
    comments_count: int = 0
    body: str | None = None
    updated_at_epoch: float = 0.0
    depends_on: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        # depends_on is the UNION of native (typed, human-confirmed) edges already on
        # the item and the directed refs parsed from the body — native edges arrive via
        # the depends_on field, body refs are derived here. Dedup, order-stable.
        parsed = depends_on_refs(self.body) if self.body else []
        self.depends_on = list(dict.fromkeys([*self.depends_on, *parsed]))

    @classmethod
    def from_row(cls, row: dict) -> "Item":
        return cls(
            kind=row.get("kind", "issue"),
            repo=str(row.get("repo", "")),
            number=int(row["number"]),
            title=row.get("title", ""),
            state=row.get("state", ""),
            labels=list(row.get("labels") or []),
            milestone=row.get("milestone"),
            reactions=int(row.get("reactions", 0) or 0),
            comments_count=int(row.get("comments_count", 0) or 0),
            body=row.get("body"),
            updated_at_epoch=_to_epoch(row.get("updated_at")),
            depends_on=[int(n) for n in (row.get("native_depends_on") or [])],
        )


@dataclass
class PriorityScore:
    number: int
    total: float
    components: dict[str, float]  # normalized 0..1 per component
    judge_reason: str | None = None


@dataclass
class Theme:
    name: str
    items: list[Item]


@dataclass
class SuggestedAdd:
    kind: str
    repo: str
    number: int
    title: str
    dist: float
    reason: str  # the strongest signal/category that surfaced it


@dataclass
class Warning:
    kind: str  # "gap" | "done" | "duplicate" | "stale_critical"
    message: str
    numbers: list[int] = field(default_factory=list)


@dataclass
class ReleasePlan:
    repo: str
    milestone: str
    ordered: list[Item]  # members in dependency order
    themes: list[Theme]
    ranked: list[tuple[Item, PriorityScore]]  # members by priority, desc
    suggested_adds: list[SuggestedAdd]
    warnings: list[Warning]
    judged: bool = False
