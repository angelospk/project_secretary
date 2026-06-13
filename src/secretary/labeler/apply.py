"""Act on classifications: decide, respect human vetoes, suggest or apply.

The trust rules live here (organizer/labeler invariants):
1. Labels are additive only — the labeler never removes a label.
2. A human removing a secretary-applied label is a permanent veto for that pair.
3. Below-threshold is silence (handled upstream in `classify`); no best-guess labels.
4. The judge only confirms borderline cases; a failure/None downgrades to a suggestion.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.github.client import GitHubClient
from secretary.labeler.classify import ACCEPT, REVIEW, SILENCE, classify_issue
from secretary.labeler.centroids import build_centroids
from secretary.labeler.taxonomy import Category, load_taxonomy
from secretary.responder import section

log = logging.getLogger(__name__)

# (title, body, category) -> True (belongs) | False (does not) | None (judge abstained).
JudgeFn = Callable[[str, str | None, Category], bool | None]

APPLIED = "applied"
SUGGESTED = "suggested"
VETOED = "vetoed"


@dataclass(frozen=True)
class LabelResult:
    number: int
    label: str
    dist: float
    action: str  # applied | suggested | vetoed


def decide_action(band: str, verdict: bool | None, mode: str) -> str:
    """Intended action for one classification, before vetoes and the dry-run gate.

    Returns "apply" (apply the GitHub label), "suggest" (list it), or "skip".
    """
    if band == SILENCE:
        return "skip"
    confident = band == ACCEPT or (band == REVIEW and verdict is True)
    if confident and mode == "auto":
        return "apply"
    return "suggest"


def _applied_key(number: int, label: str) -> str:
    return f"label_applied:{number}:{label.lower()}"


def _veto_key(number: int, label: str) -> str:
    return f"label_veto:{number}:{label.lower()}"


def is_blacklisted(
    db: Surreal, repo: str, number: int, label: str, current_labels: list[str]
) -> bool:
    """Whether `label` must never be (re)applied to issue `number`.

    A standing veto, or a label the secretary applied that a human has since removed —
    that removal is a permanent veto, recorded on first detection so it survives even
    after the applied-record is gone.
    """
    if db_repo.kv_get(db, repo, _veto_key(number, label)) is True:
        return True
    applied = db_repo.kv_get(db, repo, _applied_key(number, label))
    present = {existing.lower() for existing in current_labels}
    if applied is not None and label.lower() not in present:
        db_repo.kv_set(db, repo, _veto_key(number, label), True)
        log.info("issue #%s: human removed %r — vetoing it permanently", number, label)
        return True
    return False


def run_labeler(
    db: Surreal,
    embedder: Embedder,
    client: GitHubClient | None,
    settings: Settings,
    repo: str,
    *,
    include_labeled: bool = False,
    apply: bool = False,
    judge: JudgeFn | None = None,
    numbers: set[int] | None = None,
) -> list[LabelResult]:
    """Classify the repo's issues and (when apply) act per mode and the trust rules.

    `numbers`, when given, scopes the run to just those issue numbers (the single-issue
    webhook path) — reusing the same cached centroids and trust rules. A scoped run never
    rewrites the shared "Label suggestions" report issue; suggestions are returned in the
    results only, leaving the shared report to the full-repo run.

    Dry-run (apply=False) computes the full report but performs no writes. In auto mode
    `apply` applies confident labels via REST; in suggest mode it posts a suggestions
    report issue. Vetoed pairs are reported but never applied.
    """
    taxonomy = load_taxonomy(settings.taxonomy_path)
    centroids = build_centroids(db, embedder, repo, taxonomy)
    by_key = {c.key: c for c in taxonomy.categories}
    taxonomy_labels = {label.lower() for label in taxonomy.labels}

    results: list[LabelResult] = []
    for row in db_repo.issues_for_labeling(db, repo):
        if numbers is not None and int(row["number"]) not in numbers:
            continue
        labels = [str(x) for x in (row.get("labels") or [])]
        if not include_labeled and taxonomy_labels & {label.lower() for label in labels}:
            continue  # already carries a taxonomy label
        vector = row.get("embedding")
        if not vector:
            continue

        c = classify_issue(
            int(row["number"]), vector, centroids,
            accept=settings.labeler_accept, review=settings.labeler_review,
        )
        if c.band == SILENCE or c.label is None:
            continue

        verdict: bool | None = None
        if c.band == REVIEW and judge is not None:
            verdict = judge(row.get("title", ""), row.get("body"), by_key[c.category])

        action = decide_action(c.band, verdict, settings.labeler_mode)
        if action == "skip":
            continue
        if action == "apply":
            if is_blacklisted(db, repo, c.number, c.label, labels):
                results.append(LabelResult(c.number, c.label, c.dist, VETOED))
                continue
            if apply and client is not None:
                client.add_labels(c.number, [c.label])
                db_repo.kv_set(db, repo, _applied_key(c.number, c.label), {"dist": c.dist})
            results.append(LabelResult(c.number, c.label, c.dist, APPLIED))
        else:
            results.append(LabelResult(c.number, c.label, c.dist, SUGGESTED))

    if numbers is None and apply and client is not None and settings.labeler_mode == "suggest":
        suggested = [r for r in results if r.action == SUGGESTED]
        if suggested:
            _write_suggestions(client, db, settings, repo, suggested)
    return results


_SUGGEST_INTRO = (
    "Label suggestions maintained by the secretary. Everything below the marker is "
    "regenerated; apply or ignore the suggestions on each issue as you see fit."
)


def _render_suggestions(results: list[LabelResult]) -> str:
    lines = ["| Issue | Suggested label | Distance |", "|---|---|---|"]
    for r in sorted(results, key=lambda x: x.dist):
        lines.append(f"| #{r.number} | `{r.label}` | {r.dist:.3f} |")
    return "\n".join(lines)


def _write_suggestions(
    client: GitHubClient, db: Surreal, settings: Settings, repo: str,
    results: list[LabelResult],
) -> None:
    key = "label_suggestions_issue"
    title = "Label suggestions"
    content = _render_suggestions(results)
    stored = db_repo.kv_get(db, repo, key)
    number = int(stored) if isinstance(stored, (int, float)) and not isinstance(stored, bool) else None

    if number is None:
        created = client.create_issue(title, _SUGGEST_INTRO, labels=[settings.plan_issue_label])
        number = int(created["number"])
        db_repo.kv_set(db, repo, key, number)
        body = created.get("body") or _SUGGEST_INTRO
    else:
        body = client.get_issue(number).get("body") or ""
        if section.was_human_edited(body):
            log.warning("suggestions issue #%s edited by hand; leaving it alone", number)
            return

    new_body = section.upsert(body, number, content)
    if new_body != body:
        client.update_issue_body(number, new_body)
