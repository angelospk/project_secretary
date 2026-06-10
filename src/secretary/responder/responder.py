"""#3 responder (Model B): enrich an issue with related context + DeepWiki.

`enrich` is pure read/compute — it never touches GitHub, so it is always safe to
run (dry-run). `apply_to_github` is the only write path; it is gated on a token
with issues:write and refuses to clobber a human-edited managed block.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo
from secretary.deepwiki import client as deepwiki
from secretary.embeddings.embedder import Embedder
from secretary.github.client import GitHubClient
from secretary.responder import section
from secretary.responder.compose import render_content
from secretary.responder.prompt import build_query
from secretary.semantic.related import find_related
from secretary.semantic.reranker import RelatedItem

log = logging.getLogger(__name__)


@dataclass
class EnrichResult:
    number: int
    kind: str
    content: str
    section: str
    related: list[RelatedItem] = field(default_factory=list)
    contexts: dict[str, str] = field(default_factory=dict)


_MAX_DEEPWIKI_REPOS = 3


def _repos_to_query(repo_name: str, related: list[RelatedItem]) -> list[str]:
    """The item's own repo, plus any other repos that actually surfaced as related.

    Bounded so one enrichment never fans out into many DeepWiki calls (the
    reverse-engineered ada API throttles under load — one call per repo, capped).
    """
    ordered: dict[str, None] = {repo_name: None}
    for r in related:
        if r.category != "weak_match" and r.repo:
            ordered.setdefault(r.repo, None)
    return list(ordered)[:_MAX_DEEPWIKI_REPOS]


def enrich(
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    repo_name: str,
    number: int,
    *,
    run_ref: str = "",
) -> EnrichResult:
    """Compute the enrichment for an issue in `repo_name`. Does NOT write to GitHub."""
    kind = "pr" if repo.pr_exists(db, repo_name, number) else "issue"
    target = repo.get_meta(db, kind, repo_name, number)
    if target is None:
        raise ValueError(f"{kind} {repo_name}#{number} not found in memory")

    related = find_related(
        db, embedder, repo_name, number, k=8, pair_set=settings.related_repo_pair_set
    )
    query = build_query(target, related)

    contexts: dict[str, str] = {}
    for dw_repo in _repos_to_query(repo_name, related):
        contexts[dw_repo] = deepwiki.query(
            dw_repo, query, timeout=settings.deepwiki_timeout_seconds
        )

    content = render_content(repo_name, related, contexts, run_ref=run_ref)
    return EnrichResult(
        number=number,
        kind=kind,
        content=content,
        section=section.build_section(number, content),
        related=related,
        contexts=contexts,
    )


def apply_to_github(
    client: GitHubClient,
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    repo_name: str,
    number: int,
    *,
    force: bool = False,
) -> str:
    """Write the managed section into the live issue body. Gated, human-edit-aware."""
    issue = client.get_issue(number)
    body = issue.get("body") or ""
    if section.was_human_edited(body) and not force:
        return "skipped: managed block was edited by a human (use --force to override)"

    result = enrich(db, embedder, settings, repo_name, number)
    new_body = section.upsert(body, number, result.content)
    if new_body == body:
        return "unchanged"
    client.update_issue_body(number, new_body)
    return "updated"


def apply_comment(
    client: GitHubClient,
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    repo_name: str,
    number: int,
    *,
    force: bool = False,
) -> str:
    """Post/update a sticky managed comment. Works for any contributor (no triage)."""
    result = enrich(db, embedder, settings, repo_name, number)
    existing = next(
        (c for c in client.get_issue_comments(number) if section.extract(c.get("body"))),
        None,
    )
    if existing is None:
        client.create_comment(number, result.section)
        return "created comment"
    if section.was_human_edited(existing.get("body")) and not force:
        return "skipped: managed comment was edited by a human (use --force to override)"
    client.update_comment(existing["id"], result.section)
    return "updated comment"
