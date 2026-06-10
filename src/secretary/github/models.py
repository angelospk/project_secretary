"""Pydantic models that normalize raw GitHub API JSON into internal records.

These map directly from GitHub REST payloads (via aliases) into the shapes the
ingest pipeline upserts into SurrealDB.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# "closes #12", "fixes #3", "resolves #9" (GitHub's closing keywords)
_CLOSING_KEYWORD = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE
)

# Cross-repo reference in body text, e.g. "blocked by owner/repo#42". The lookbehind
# keeps it from firing inside a URL path (.../owner/repo#42) or a longer token.
_CROSS_REPO_REF = re.compile(r"(?<![\w./-])([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)#(\d+)")

# Directed dependency in body text: "blocked by #12", "depends on #3", "needs #9",
# "requires #7". Only these phrasings mean *this item depends on N* — and only these
# drive the organizer's release ordering. A bare "#12" or "relates to #12" does not,
# and a PR's "closes #N" is a *resolves* edge, never an ordering constraint.
_DEPENDS_ON = re.compile(
    r"\b(?:blocked\s+by|depends?\s+on|requires?|needs?)\s+#(\d+)", re.IGNORECASE
)

# Reaction buckets GitHub returns; everything except "-1" and "confused" reads as a
# positive vote for prioritization.
_POSITIVE_REACTIONS = ("+1", "laugh", "hooray", "heart", "rocket", "eyes")


def _positive_reactions(raw: dict | None) -> int:
    """Sum of positive reaction counts from an issue's `reactions` object."""
    reactions = raw or {}
    return sum(int(reactions.get(key, 0) or 0) for key in _POSITIVE_REACTIONS)


def _login(user: dict | None) -> str | None:
    return user.get("login") if user else None


def _label_names(labels: list[dict] | None) -> list[str]:
    return [label["name"] for label in labels or [] if "name" in label]


def _milestone_title(milestone: dict | None) -> str | None:
    return milestone.get("title") if milestone else None


class Issue(BaseModel):
    repo: str
    number: int
    title: str
    body: str | None = None
    state: str
    author: str | None = None
    labels: list[str] = Field(default_factory=list)
    url: str | None = None
    milestone: str | None = None
    reactions: int = 0
    comments_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None

    @classmethod
    def from_api(cls, raw: dict, repo: str) -> "Issue":
        return cls(
            repo=repo,
            number=raw["number"],
            title=raw["title"],
            body=raw.get("body"),
            state=raw["state"],
            author=_login(raw.get("user")),
            labels=_label_names(raw.get("labels")),
            url=raw.get("html_url"),
            milestone=_milestone_title(raw.get("milestone")),
            reactions=_positive_reactions(raw.get("reactions")),
            comments_count=int(raw.get("comments", 0) or 0),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            closed_at=raw.get("closed_at"),
        )


class PullRequest(BaseModel):
    repo: str
    number: int
    title: str
    body: str | None = None
    state: str
    author: str | None = None
    labels: list[str] = Field(default_factory=list)
    url: str | None = None
    milestone: str | None = None
    head_ref: str | None = None
    base_ref: str | None = None
    linked_issues: list[int] = Field(default_factory=list)
    reactions: int = 0
    comments_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None

    @classmethod
    def from_api(cls, raw: dict, repo: str) -> "PullRequest":
        body = raw.get("body")
        return cls(
            repo=repo,
            number=raw["number"],
            title=raw["title"],
            body=body,
            state=raw["state"],
            author=_login(raw.get("user")),
            labels=_label_names(raw.get("labels")),
            url=raw.get("html_url"),
            milestone=_milestone_title(raw.get("milestone")),
            head_ref=(raw.get("head") or {}).get("ref"),
            base_ref=(raw.get("base") or {}).get("ref"),
            linked_issues=closing_refs(body),
            reactions=_positive_reactions(raw.get("reactions")),
            comments_count=int(raw.get("comments", 0) or 0),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            merged_at=raw.get("merged_at"),
            closed_at=raw.get("closed_at"),
        )


class Comment(BaseModel):
    repo: str
    gh_id: int
    parent_number: int
    author: str | None = None
    body: str | None = None
    url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_api(cls, raw: dict, repo: str) -> "Comment":
        return cls(
            repo=repo,
            gh_id=raw["id"],
            parent_number=parent_number_from_issue_url(raw["issue_url"]),
            author=_login(raw.get("user")),
            body=raw.get("body"),
            url=raw.get("html_url"),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
        )

    @field_validator("parent_number")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("parent_number must be positive")
        return v


def closing_refs(body: str | None) -> list[int]:
    """Issue numbers a PR body declares it closes (dedup, ordered)."""
    if not body:
        return []
    seen: dict[int, None] = {}
    for match in _CLOSING_KEYWORD.finditer(body):
        seen.setdefault(int(match.group(1)), None)
    return list(seen)


def depends_on_refs(body: str | None) -> list[int]:
    """Issue numbers this item declares it depends on (dedup, ordered).

    Matches only directed-dependency phrasing ("blocked by/depends on/needs/requires
    #N"). These are the sole edges the organizer uses to order a release; weaker links
    (bare mentions, "relates to") are deliberately excluded.
    """
    if not body:
        return []
    seen: dict[int, None] = {}
    for match in _DEPENDS_ON.finditer(body):
        seen.setdefault(int(match.group(1)), None)
    return list(seen)


def cross_repo_refs(body: str | None, current_repo: str) -> list[tuple[str, int]]:
    """`owner/repo#N` references in a body that point at a *different* repo.

    Returns normalized `(owner/repo, number)` pairs (dedup, ordered). Same-repo
    `#N` mentions are handled elsewhere; this is only the cross-repo case.
    """
    if not body:
        return []
    current = current_repo.strip().lower()
    seen: dict[tuple[str, int], None] = {}
    for match in _CROSS_REPO_REF.finditer(body):
        repo = match.group(1).lower()
        if repo != current:
            seen.setdefault((repo, int(match.group(2))), None)
    return list(seen)


def parent_number_from_issue_url(issue_url: str) -> int:
    """Extract the issue/PR number from a comment's `issue_url`.

    e.g. https://api.github.com/repos/o/r/issues/42 -> 42
    """
    tail = issue_url.rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit():
        raise ValueError(f"cannot parse issue number from URL: {issue_url!r}")
    return int(tail)
