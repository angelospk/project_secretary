"""Watermark lookback: each incremental reconcile re-reads a small overlap window.

The watermark is written from our own wall clock, while GitHub's `since` filter and
`updated_at` run on GitHub's clock. Clock skew — or eventual consistency on the
`since`-filtered issues listing (an item's `updated_at` advances while its labels
projection lags) — can let a change slip past the watermark permanently, fixable
only by a full backfill. Re-reading a bounded overlap each cycle converts that
"stale forever" into "stale for at most the lookback", and self-heals.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from secretary.config import Settings
from secretary.ingest import reconcile


WATERMARK = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


# --- pure helper -------------------------------------------------------------

def test_no_watermark_stays_a_full_backfill():
    assert reconcile._lookback_since(None, 120) is None


def test_zero_lookback_is_the_bare_watermark():
    assert reconcile._lookback_since(WATERMARK, 0) == WATERMARK


def test_negative_lookback_is_treated_as_zero():
    assert reconcile._lookback_since(WATERMARK, -5) == WATERMARK


def test_positive_lookback_widens_the_window_backwards():
    assert reconcile._lookback_since(WATERMARK, 120) == WATERMARK - timedelta(seconds=120)


# --- reconcile wiring --------------------------------------------------------

def test_reconcile_reads_from_watermark_minus_lookback(monkeypatch):
    captured: dict = {}

    frozen_now = WATERMARK + timedelta(seconds=90)
    monkeypatch.setattr(reconcile, "_now", lambda: frozen_now)
    monkeypatch.setattr(reconcile.db_repo, "get_watermark", lambda db, repo, key: WATERMARK)
    monkeypatch.setattr(reconcile.db_repo, "set_watermark",
                        lambda db, repo, key, ts: captured.__setitem__("written", ts))
    monkeypatch.setattr(reconcile, "get_settings",
                        lambda: Settings(reconcile_lookback_seconds=300))

    def fake_sync(db, client, repo, *, since):
        captured["since"] = since
        return reconcile.SyncReport()

    monkeypatch.setattr(reconcile, "sync", fake_sync)
    monkeypatch.setattr(reconcile.embeddings, "embed_new", lambda db, **k: {})

    reconcile.reconcile(db=None, client=None, repo="o/r")

    assert captured["since"] == WATERMARK - timedelta(seconds=300)
    # the watermark we persist stays our own wall-clock start, not the widened read.
    assert captured["written"] == frozen_now


def test_reconcile_embeds_new_items_after_sync(monkeypatch):
    """The incremental cycle auto-embeds whatever it just ingested — re-upsert clears
    the old vector, so new/changed issues+PRs become `embedding IS NONE` and get one."""
    frozen_now = WATERMARK + timedelta(seconds=90)
    monkeypatch.setattr(reconcile, "_now", lambda: frozen_now)
    monkeypatch.setattr(reconcile.db_repo, "get_watermark", lambda db, repo, key: WATERMARK)
    monkeypatch.setattr(reconcile.db_repo, "set_watermark", lambda db, repo, key, ts: None)
    monkeypatch.setattr(reconcile, "get_settings",
                        lambda: Settings(reconcile_lookback_seconds=0))
    monkeypatch.setattr(reconcile, "sync",
                        lambda db, client, repo, *, since: reconcile.SyncReport())

    seen = {}

    def fake_embed(db, **k):
        seen["db"] = db
        return {"issue": 1, "pr": 0}

    monkeypatch.setattr(reconcile.embeddings, "embed_new", fake_embed)

    reconcile.reconcile(db="DB", client=None, repo="o/r")

    assert seen["db"] == "DB"  # embed ran against the same connection, after sync


def test_reconcile_survives_embed_failure(monkeypatch):
    """Sync already committed + watermark advanced, so a failing embed must not crash
    the cycle — the pending rows just get embedded next time."""
    monkeypatch.setattr(reconcile, "_now", lambda: WATERMARK)
    monkeypatch.setattr(reconcile.db_repo, "get_watermark", lambda db, repo, key: WATERMARK)
    monkeypatch.setattr(reconcile.db_repo, "set_watermark", lambda db, repo, key, ts: None)
    monkeypatch.setattr(reconcile, "get_settings",
                        lambda: Settings(reconcile_lookback_seconds=0))
    monkeypatch.setattr(reconcile, "sync",
                        lambda db, client, repo, *, since: reconcile.SyncReport())

    def boom(db, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(reconcile.embeddings, "embed_new", boom)

    report = reconcile.reconcile(db="DB", client=None, repo="o/r")
    assert isinstance(report, reconcile.SyncReport)  # did not propagate


def test_backfill_does_not_auto_embed(monkeypatch):
    """Bulk backfill stays embed-free; the one-time `secretary embed` owns that pass."""
    monkeypatch.setattr(reconcile, "_now", lambda: WATERMARK)
    monkeypatch.setattr(reconcile.db_repo, "set_watermark", lambda db, repo, key, ts: None)
    monkeypatch.setattr(reconcile, "sync",
                        lambda db, client, repo, *, since: reconcile.SyncReport())

    def boom(db, **k):
        raise AssertionError("backfill must not auto-embed")

    monkeypatch.setattr(reconcile.embeddings, "embed_new", boom)

    reconcile.backfill(db="DB", client=None, repo="o/r")  # must not raise
