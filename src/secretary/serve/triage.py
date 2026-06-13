"""The per-item triage path a worker runs for one webhook event.

Every step is one of the existing, idempotent entry points the poll loop uses, so
running it early (webhook) or late (reconcile), once or twice, converges to the same
state — that is what makes the webhook safe as a pure latency optimization.

For a full-triage `issues` opened/reopened event:
  1. ingest_issue_or_pr  — upsert the item into memory.
  2. embed_pending       — embed the one new unembedded row (idempotent).
  3. responder.apply_comment — the sticky enrichment comment (idempotent).
  4. run_labeler(numbers={n}) — classify just this issue, honoring labeler_mode.

For ingest-only events, only step 1 (or ingest_comment) runs.
"""

from __future__ import annotations

import logging

from surrealdb import Surreal

from secretary import llm
from secretary.config import Settings
from secretary.embeddings.embedder import Embedder
from secretary.embeddings.service import embed_pending
from secretary.github.client import GitHubClient
from secretary.ingest import pipeline
from secretary.labeler import apply as labeler_apply
from secretary.labeler.judge import default_membership_judge
from secretary.responder import responder
from secretary.serve.routing import TriageTask

log = logging.getLogger(__name__)


def run_task(
    task: TriageTask,
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    client: GitHubClient,
) -> None:
    """Ingest the event's item and, for full-triage events, enrich + label it."""
    # 1. Ingest (always).
    if task.raw_kind == "comment":
        pipeline.ingest_comment(db, task.repo, task.raw, set())
    else:
        pipeline.ingest_issue_or_pr(db, task.repo, client, task.raw)

    if task.action != "triage" or not settings.serve_triage:
        return

    # 2. Embed the one new row (idempotent — picks up only the unembedded item).
    embed_pending(db, embedder)

    # 3. Sticky enrichment comment (idempotent: updates in place, never duplicates).
    msg = responder.apply_comment(client, db, embedder, settings, task.repo, task.number)
    log.info("triage %s#%s enrich: %s", task.repo, task.number, msg)

    # 4. Labels — scoped to this one issue, reusing cached centroids + trust rules.
    if not settings.taxonomy_path:
        return
    judge_fn = None
    if settings.judge_enabled and llm.credentials_ready(settings):
        judge_fn = default_membership_judge(settings)
    results = labeler_apply.run_labeler(
        db, embedder, client, settings, task.repo,
        apply=True, judge=judge_fn, numbers={task.number},
    )
    for r in results:
        log.info("triage %s#%s label %s: %s", task.repo, task.number, r.label, r.action)
