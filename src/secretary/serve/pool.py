"""A small bounded worker pool: N threads draining one bounded queue.

The HTTP handler ACKs immediately and hands the triage task here. A burst can't
exhaust the box: the queue is capped, and `submit` returns False on overflow so the
caller responds 503 (GitHub retries; reconcile covers it regardless). A handler that
raises is logged and the worker keeps going — one bad event never stops the pool.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_SHUTDOWN = object()  # sentinel pushed to wake workers for a clean exit


class WorkerPool:
    def __init__(self, workers: int, queue_max: int, handler: Callable[[Any], None]):
        self._handler = handler
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, queue_max))
        self._stopping = False
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(target=self._work, name=f"triage-{i}", daemon=True)
            for i in range(max(1, workers))
        ]

    def start(self) -> None:
        for t in self._threads:
            t.start()

    def submit(self, item: Any) -> bool:
        """Enqueue without blocking.

        Returns False if the queue is full (overflow → drop) OR if the pool is shutting
        down — a late submit from an in-flight request thread must not be stranded behind
        the shutdown sentinels.
        """
        with self._lock:
            if self._stopping:
                return False
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            return False

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SHUTDOWN:
                    return
                self._handler(item)
            except Exception:  # noqa: BLE001 - one bad event must not kill the worker
                log.exception("triage task failed; dropping it (reconcile will catch it)")
            finally:
                self._queue.task_done()

    def shutdown(self) -> None:
        """Refuse new work, drain what's queued, and stop every worker."""
        with self._lock:
            self._stopping = True
        for _ in self._threads:
            self._queue.put(_SHUTDOWN)
        for t in self._threads:
            t.join()
