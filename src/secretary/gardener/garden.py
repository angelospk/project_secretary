"""Gardener orchestration: collect findings, apply vetoes, render, write.

Collection (DB reads) is separated from the pure predicates in `signals.py`; writes are
the usual trust-laddered ones — a managed section on a rolling issue (report) and at most
one advisory comment per (issue, signal, evidence) ever (comment). Nothing closes.

At most one finding per issue: signals are evaluated strongest-first and the first match
wins, so a probably-fixed issue is never also nagged as dormant.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.gardener import render as render_mod
from secretary.gardener import signals as sig
from secretary.gardener.judge import SupersedeJudge
from secretary.gardener.signals import KEEP_MARKER, Finding
from secretary.github.client import GitHubClient
from secretary.responder import section
from secretary.semantic.reranker import DUPLICATE, HISTORICAL_REFERENCE, MODERATE, STRONG
from secretary.semantic.related import find_related

log = logging.getLogger(__name__)

# RelatedItem provider, injectable so the orchestration is unit-testable without a db.
RelatedFn = Callable[[Surreal, Embedder, str, int], list]


def _priority_labels(settings: Settings) -> set[str]:
    """The maintainer's declared priority vocabulary (drives the dormancy guard)."""
    return {k.lower() for k in settings.priority_label_map}


def _is_closed(state: str | None) -> bool:
    return (state or "").lower() in ("closed", "merged")


def collect_findings(
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    repo: str,
    *,
    now: datetime,
    judge: SupersedeJudge | None = None,
    related_fn: RelatedFn | None = None,
    open_issues: list[dict] | None = None,
) -> list[Finding]:
    """Evaluate every open issue against the signals, strongest match per issue."""
    related_fn = related_fn or (
        lambda db, e, r, n: find_related(
            db, e, r, n, k=8, pair_set=settings.related_repo_pair_set
        )
    )
    priority_labels = _priority_labels(settings)
    if open_issues is None:
        open_issues = db_repo.open_issues(db, repo)
    open_by_number = {int(r["number"]): r for r in open_issues}

    findings: list[Finding] = []
    seen_pairs: set[frozenset[int]] = set()
    for issue in open_issues:
        number = int(issue["number"])

        # 1. probably fixed — a merged PR links it.
        linked = db_repo.linked_prs(db, repo, number)
        found = sig.probably_fixed(issue, linked)
        if found is not None:
            findings.append(found)
            continue

        # Both duplicate and superseded read the same related-item list.
        related = related_fn(db, embedder, repo, number)

        # 2. probably duplicate — an open same-repo DUPLICATE twin.
        dup = next(
            (it for it in related
             if it.category == DUPLICATE and it.repo == repo
             and not _is_closed(it.state) and int(it.number) in open_by_number),
            None,
        )
        if dup is not None:
            pair = frozenset({number, int(dup.number)})
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                found = sig.probably_duplicate(
                    issue, open_by_number[int(dup.number)], priority_labels
                )
                if found is not None:
                    findings.append(found)
                    continue

        # 3. probably superseded — strong relatedness to a closed item.
        sup = next(
            (it for it in related
             if it.category == HISTORICAL_REFERENCE and _is_closed(it.state)
             and it.dist <= MODERATE),
            None,
        )
        if sup is not None:
            closed_ref = {"number": int(sup.number), "repo": sup.repo,
                          "dist": sup.dist, "title": sup.title}
            verdict = None
            if STRONG < sup.dist <= MODERATE and judge is not None:
                verdict = judge(issue, closed_ref)
            found = sig.probably_superseded(issue, closed_ref, verdict)
            if found is not None:
                findings.append(found)
                continue

        # 4. dormant — old and unprioritized.
        found = sig.dormant(issue, now, settings.gardener_dormant_days, priority_labels)
        if found is not None:
            findings.append(found)

    return findings


def _veto_key(issue: int, signal: str) -> str:
    return f"gardener_veto:{issue}:{signal}"


def _comment_key(f: Finding) -> str:
    # Fingerprint in the key: a comment is re-posted only if the recommendation target
    # materially changes (e.g. a duplicate survivor flips), never for identical evidence.
    return f"gardener_comment:{f.issue}:{f.signal}:{f.fingerprint}"


def apply_vetoes(
    db: Surreal, repo: str, findings: list[Finding], bodies: dict[int, str | None]
) -> list[Finding]:
    """Drop findings a maintainer has vetoed: a body `keep` marker (all signals for that
    issue) or a per-(issue, signal) kv veto."""
    kept: list[Finding] = []
    for f in findings:
        body = bodies.get(f.issue) or ""
        if KEEP_MARKER in body:
            continue
        if db_repo.kv_get(db, repo, _veto_key(f.issue, f.signal)) is True:
            continue
        kept.append(f)
    return kept


def run_gardener(
    db: Surreal,
    embedder: Embedder,
    client: GitHubClient | None,
    settings: Settings,
    repo: str,
    *,
    now: datetime,
    judge: SupersedeJudge | None = None,
    apply: bool = False,
    related_fn: RelatedFn | None = None,
) -> list[Finding]:
    """Collect findings, filter vetoes, and (when apply) write the report/comments.

    Dry-run (apply=False) computes and returns findings without any write. Writes honor
    `gardener_mode`: report maintains the managed section; comment also posts one advisory
    per finding. Off ⇒ no writes regardless of apply.
    """
    open_issues = db_repo.open_issues(db, repo)
    findings = collect_findings(
        db, embedder, settings, repo, now=now, judge=judge, related_fn=related_fn,
        open_issues=open_issues,
    )
    bodies = {int(r["number"]): r.get("body") for r in open_issues}
    findings = apply_vetoes(db, repo, findings, bodies)

    if apply and client is not None and settings.gardener_mode in ("report", "comment"):
        _write_report(client, db, settings, repo, findings)
        if settings.gardener_mode == "comment":
            _write_comments(client, db, settings, repo, findings)
    return findings


_REPORT_INTRO = (
    "Backlog gardening maintained by the secretary. Everything below the marker is "
    "regenerated each run — proposals only, nothing is closed automatically."
)


def _write_report(
    client: GitHubClient, db: Surreal, settings: Settings, repo: str,
    findings: list[Finding],
) -> None:
    key = "gardener_issue"
    title = settings.gardener_issue_title
    content = render_mod.render(findings)
    stored = db_repo.kv_get(db, repo, key)
    number = int(stored) if isinstance(stored, (int, float)) and not isinstance(stored, bool) else None

    if number is None:
        created = client.create_issue(title, _REPORT_INTRO, labels=[settings.plan_issue_label])
        number = int(created["number"])
        db_repo.kv_set(db, repo, key, number)
        body = created.get("body") or _REPORT_INTRO
    else:
        body = client.get_issue(number).get("body") or ""
        if section.was_human_edited(body):
            log.warning("gardening issue #%s edited by hand; leaving it alone", number)
            return

    new_body = section.upsert(body, number, content)
    if new_body != body:
        client.update_issue_body(number, new_body)


def _comment_body(f: Finding) -> str:
    lines = [f"**Secretary — {f.signal.replace('_', ' ')}** (a suggestion, not an action)",
             "", f.summary, ""]
    lines += [f"- {ev}" for ev in f.evidence]
    lines += ["", f.suggestion]
    return "\n".join(lines)


def _write_comments(
    client: GitHubClient, db: Surreal, settings: Settings, repo: str,
    findings: list[Finding],
) -> None:
    for f in findings:
        key = _comment_key(f)
        if db_repo.kv_get(db, repo, key):
            continue  # already advised for this exact recommendation
        client.create_comment(f.issue, _comment_body(f))
        db_repo.kv_set(db, repo, key, True)  # only after a successful write
