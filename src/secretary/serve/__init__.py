"""Event-driven triage (#7): the `secretary serve` webhook receiver.

A latency optimization over the reconcile loop — it runs the existing ingest +
triage subsystems for the single item a GitHub webhook names. The poll loop still
owns ingestion; a missed/duplicate/out-of-order webhook costs latency only.

`serve()` is re-exported from `secretary.serve.server` once that module exists.
"""

from secretary.serve.server import serve

__all__ = ["serve"]
