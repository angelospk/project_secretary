"""Build and render the weekly maintainer digest.

Collection (DB reads) builds a plain `DigestData`; rendering is pure over it, so the
markdown and the Discord payload share one source. Every section degrades gracefully:
no new issues ⇒ empty notables, gardener off ⇒ no hygiene section, etc. The digest is
text a human skims in 90 seconds — counts and headlines, not dashboards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from surrealdb import Surreal

from secretary import llm
from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.embeddings.embedder import Embedder
from secretary.gardener import garden as gardener_garden
from secretary.gardener.signals import SIGNAL_ORDER
from secretary.organizer import priority
from secretary.organizer.drift import fingerprint_key
from secretary.organizer.models import Item
from secretary.semantic.reranker import DUPLICATE
from secretary.semantic.related import find_related


@dataclass
class DigestData:
    repo: str
    since: datetime
    until: datetime
    activity: dict[str, int]
    notable_priority: list[tuple[int, str, float]] = field(default_factory=list)
    duplicate_pairs: list[tuple[int, int]] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    gardener_counts: dict[str, int] | None = None
    health: dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """Nothing happened in the window — no activity and nothing notable."""
        return (
            sum(self.activity.values()) == 0
            and not self.notable_priority
            and not self.duplicate_pairs
            and not self.drift
            and not (self.gardener_counts or {})
        )


def _top_decile_priority(settings: Settings, rows: list[dict]) -> list[tuple[int, str, float]]:
    """Score new issues with the organizer's priority weights; keep the top decile."""
    if not rows:
        return []
    items = [Item.from_row({**r, "kind": "issue"}) for r in rows]
    ranked = priority.rank_members(
        items,
        weights=settings.priority_weight_map,
        label_map=settings.priority_label_map,
        dependents={},  # new-issue cross-deps don't matter for a weekly highlight
    )
    cut = max(1, len(ranked) // 10)
    return [(item.number, item.title, score.total) for item, score in ranked[:cut]]


def _new_duplicate_pairs(
    db: Surreal, embedder: Embedder, settings: Settings, repo: str, rows: list[dict]
) -> list[tuple[int, int]]:
    """Duplicate pairs among issues opened this window (reranker DUPLICATE, same repo)."""
    pairs: set[tuple[int, int]] = set()
    for row in rows:
        number = int(row["number"])
        related = find_related(
            db, embedder, repo, number, k=5, pair_set=settings.related_repo_pair_set
        )
        for it in related:
            if it.category == DUPLICATE and it.repo == repo:
                pairs.add(tuple(sorted((number, int(it.number)))))
    return sorted(pairs)


def _drift_since_last(db: Surreal, settings: Settings, repo: str) -> list[str]:
    """Milestones whose living plan fingerprint changed since the last digest snapshot."""
    snapshot = db_repo.kv_get(db, repo, "digest_plan_fingerprints") or {}
    drifted: list[str] = []
    for milestone in settings.plan_milestone_list:
        current = db_repo.kv_get(db, repo, fingerprint_key(milestone))
        if current is not None and snapshot.get(milestone) not in (None, current):
            drifted.append(milestone)
    return drifted


def _gardener_counts(
    db: Surreal, embedder: Embedder, settings: Settings, repo: str, now: datetime
) -> dict[str, int] | None:
    """Counts of current gardener findings by signal — omitted when the gardener is off."""
    if settings.gardener_mode == "off":
        return None
    findings = gardener_garden.collect_findings(db, embedder, settings, repo, now=now)
    counts = {sig: 0 for sig in SIGNAL_ORDER}
    for f in findings:
        counts[f.signal] = counts.get(f.signal, 0) + 1
    return {sig: n for sig, n in counts.items() if n}


def _health(db: Surreal, settings: Settings, repo: str) -> dict[str, str]:
    from secretary.ingest.reconcile import WATERMARK_KEY

    last = db_repo.get_watermark(db, repo, WATERMARK_KEY)
    return {
        "last_reconcile": last.isoformat() if last else "never",
        "judge": "ready" if llm.credentials_ready(settings) else f"off ({settings.judge_provider}, no creds)",
        "deepwiki": "configured" if settings.deepwiki_timeout_seconds > 0 else "disabled",
    }


def build_digest(
    db: Surreal,
    embedder: Embedder,
    settings: Settings,
    repo: str,
    *,
    since: datetime,
    now: datetime,
) -> DigestData:
    """Assemble the digest for `repo` over (since, now]."""
    new_rows = db_repo.issues_created_since(db, repo, since)
    return DigestData(
        repo=repo,
        since=since,
        until=now,
        activity=db_repo.activity_counts(db, repo, since),
        notable_priority=_top_decile_priority(settings, new_rows),
        duplicate_pairs=_new_duplicate_pairs(db, embedder, settings, repo, new_rows),
        drift=_drift_since_last(db, settings, repo),
        gardener_counts=_gardener_counts(db, embedder, settings, repo, now),
        health=_health(db, settings, repo),
    )


_GARDEN_HEADINGS = {
    "probably_fixed": "probably fixed",
    "probably_duplicate": "probably duplicate",
    "probably_superseded": "probably superseded",
    "dormant": "dormant",
}


def render(data: DigestData) -> str:
    """Markdown body for the managed digest section (without the section markers)."""
    a = data.activity
    lines = [
        f"Digest for **{data.repo}**, {data.since:%Y-%m-%d} → {data.until:%Y-%m-%d}.",
        "",
        "### Activity",
        f"- {a.get('opened', 0)} opened · {a.get('closed', 0)} closed · "
        f"{a.get('merged', 0)} PRs merged",
    ]

    lines.append("\n### Notables")
    if data.notable_priority:
        lines.append("- Top new issues by priority:")
        for number, title, score in data.notable_priority:
            lines.append(f"  - #{number} ({score:.2f}) {title}")
    if data.duplicate_pairs:
        joined = ", ".join(f"#{a}↔#{b}" for a, b in data.duplicate_pairs)
        lines.append(f"- New likely-duplicate pairs: {joined}")
    if data.drift:
        lines.append(f"- Release plans rewritten: {', '.join(data.drift)}")
    if not (data.notable_priority or data.duplicate_pairs or data.drift):
        lines.append("- _nothing notable_")

    if data.gardener_counts:
        summary = ", ".join(
            f"{n} {_GARDEN_HEADINGS.get(sig, sig)}" for sig, n in data.gardener_counts.items()
        )
        lines.append(f"\n### Hygiene\n- {summary} (see the gardening issue)")

    h = data.health
    lines.append(
        f"\n### Secretary health\n- last reconcile: {h.get('last_reconcile')} · "
        f"judge: {h.get('judge')} · deepwiki: {h.get('deepwiki')}"
    )
    return "\n".join(lines)
