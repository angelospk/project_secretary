"""Read-view aggregation for the console — pure-ish functions over db.repo.

Each builds a plain dict/list the templates render. GitHub URLs are *synthesized* from
(repo, kind, number) rather than trusted from the DB, so a stored value can't smuggle a
foreign link into a page. Everything degrades on empty data rather than crashing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from surrealdb import Surreal

from secretary import llm
from secretary.config import Settings
from secretary.console import state as console_state
from secretary.db import repo as db_repo


def github_url(repo: str, kind: str, number: int) -> str:
    """Synthesize the canonical GitHub URL — never render a DB-supplied link."""
    if kind not in ("issue", "pr"):
        raise ValueError(f"invalid kind: {kind!r}, expected 'issue' or 'pr'")
    segment = "pull" if kind == "pr" else "issues"
    return f"https://github.com/{repo}/{segment}/{int(number)}"


def status(db: Surreal, settings: Settings, repo: str) -> dict:
    """Service status: last reconcile, counts, judge/deepwiki availability."""
    from secretary.ingest.reconcile import WATERMARK_KEY

    last = db_repo.get_watermark(db, repo, WATERMARK_KEY)
    return {
        "repo": repo,
        "last_reconcile": last.isoformat() if last else None,
        "counts": db_repo.counts(db, repo),
        "judge_ready": llm.credentials_ready(settings),
        "judge_provider": settings.judge_provider,
        "deepwiki": (settings.deepwiki_timeout_seconds or 0) > 0,
    }


def _week_start(ts: datetime) -> str:
    ts = ts.astimezone(timezone.utc)
    monday = ts - timedelta(days=ts.weekday())
    return monday.strftime("%Y-%m-%d")


def weekly_activity(db: Surreal, repo: str, *, weeks: int = 12, now: datetime | None = None) -> list[dict]:
    """Opened/closed/merged counts bucketed by ISO week (Monday), oldest first."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(weeks=weeks)
    dates = db_repo.event_dates(db, repo, since)
    buckets: dict[str, dict[str, int]] = {}
    # Seed every week in the window so gaps render as zero rather than vanishing.
    for w in range(weeks):
        buckets[_week_start(now - timedelta(weeks=w))] = {"opened": 0, "closed": 0, "merged": 0}
    for kind, stamps in dates.items():
        for ts in stamps:
            wk = _week_start(ts)
            buckets.setdefault(wk, {"opened": 0, "closed": 0, "merged": 0})[kind] += 1
    return [{"week": wk, **counts} for wk, counts in sorted(buckets.items())]


def clusters(db: Surreal, settings: Settings, repo: str) -> list[dict]:
    """Label-based clusters with open-issue counts and console display-name overrides."""
    overrides = console_state.get_label_overrides(db, repo)
    counts = db_repo.label_counts(db, repo)
    out = []
    for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append({"key": label, "name": overrides.get(label, label),
                    "renamed": label in overrides, "count": n})
    return out


def duplicate_candidates(db: Surreal, settings: Settings, repo: str, *,
                         limit: int = 50, related_fn=None) -> list[dict]:
    """Open issues that look like duplicates/overlaps of another item, deduped by pair.

    Read-only: relies on stored embeddings (no embedder loaded here). Issues without a
    stored vector are skipped rather than encoded on the fly. `related_fn(db, repo,
    number) -> [RelatedItem]` is injectable for testing.
    """
    from secretary.semantic.related import find_related
    from secretary.semantic.reranker import DUPLICATE, IMPLEMENTATION_OVERLAP

    if related_fn is None:
        def related_fn(d, r, n):
            return find_related(d, None, r, n, k=8, pair_set=settings.related_repo_pair_set)

    open_issues = db_repo.open_issues(db, repo)
    titles = {int(i["number"]): i.get("title", "") for i in open_issues}
    seen: set[frozenset] = set()
    out: list[dict] = []
    for issue in open_issues:
        number = int(issue["number"])
        try:
            related = related_fn(db, repo, number)
        except ValueError:
            continue  # no stored embedding yet — nothing to compare
        for it in related:
            if it.category not in (DUPLICATE, IMPLEMENTATION_OVERLAP):
                continue
            key = frozenset({(repo, "issue", number), (it.repo, it.kind, int(it.number))})
            if len(key) == 1 or key in seen:
                continue  # self-pair or already recorded from the other side
            seen.add(key)
            out.append({
                "source": {"repo": repo, "kind": "issue", "number": number,
                           "title": titles.get(number, ""),
                           "url": github_url(repo, "issue", number)},
                "match": {"repo": it.repo, "kind": it.kind, "number": int(it.number),
                          "title": it.title, "state": it.state,
                          "url": github_url(it.repo, it.kind, int(it.number))},
                "category": it.category,
                "confidence": round(float(it.confidence), 2),
                "signals": list(it.signals),
            })
    out.sort(key=lambda r: -r["confidence"])
    return out[:limit]


def gardener_findings(db: Surreal, settings: Settings, repo: str, *,
                      now: datetime | None = None, collect_fn=None) -> list[dict]:
    """The gardener's advisory findings (probably-fixed/duplicate/dormant), as a queue.

    Uses the cheap deterministic path: no LLM judge, stored embeddings only. Confident
    findings sort first. `collect_fn` is injectable for testing.
    """
    from secretary.gardener.garden import collect_findings
    from secretary.semantic.related import find_related

    now = now or datetime.now(timezone.utc)
    if collect_fn is None:
        collect_fn = collect_findings

    def _safe_related(d, _embedder, r, n):
        try:
            return find_related(d, None, r, n, k=8, pair_set=settings.related_repo_pair_set)
        except ValueError:
            return []

    findings = collect_fn(db, None, settings, repo, now=now, judge=None, related_fn=_safe_related)
    out = [{
        "issue": f.issue,
        "url": github_url(repo, "issue", f.issue),
        "signal": f.signal,
        "confidence": f.confidence,
        "summary": f.summary,
        "suggestion": f.suggestion,
        "evidence": list(f.evidence),
    } for f in findings]
    out.sort(key=lambda r: (r["confidence"] != "confident", r["issue"]))
    return out


def release_progress(db: Surreal, repo: str, milestone: str) -> dict:
    """How far a milestone has moved: done (closed issues + merged PRs) vs total members."""
    members = db_repo.milestone_members(db, repo, milestone)
    done = db_repo.milestone_done_items(db, repo, milestone)
    done_keys = {(d["kind"], int(d["number"])) for d in done}
    total = len(members)
    items = []
    for m in members:
        key = (m["kind"], int(m["number"]))
        items.append({
            "kind": m["kind"], "number": int(m["number"]), "title": m.get("title", ""),
            "done": key in done_keys, "url": github_url(repo, m["kind"], int(m["number"])),
        })
    items.sort(key=lambda it: (it["done"], it["number"]))  # remaining work first
    return {
        "milestone": milestone, "total": total, "done": len(done_keys),
        "percent": round(100 * len(done_keys) / total) if total else 0, "items": items,
    }
