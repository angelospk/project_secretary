import threading

from secretary.serve.pool import WorkerPool


def test_submitted_tasks_run_on_workers():
    seen = []
    lock = threading.Lock()

    def handler(item):
        with lock:
            seen.append(item)

    pool = WorkerPool(workers=2, queue_max=8, handler=handler)
    pool.start()
    for i in range(5):
        assert pool.submit(i) is True
    pool.shutdown()  # drains
    assert sorted(seen) == [0, 1, 2, 3, 4]


def test_overflow_submit_returns_false():
    release = threading.Event()

    def handler(item):
        release.wait(2.0)  # block workers so the queue fills

    pool = WorkerPool(workers=1, queue_max=1, handler=handler)
    pool.start()
    accepted, refused = 0, 0
    # 1 in-flight on the worker + 1 in the queue, the rest refused.
    for i in range(10):
        if pool.submit(i):
            accepted += 1
        else:
            refused += 1
    assert refused > 0          # overflow path exercised
    release.set()
    pool.shutdown()


def test_handler_exception_does_not_kill_worker():
    seen = []

    def handler(item):
        if item == "boom":
            raise RuntimeError("kaboom")
        seen.append(item)

    pool = WorkerPool(workers=1, queue_max=8, handler=handler)
    pool.start()
    pool.submit("boom")
    pool.submit("ok")
    pool.shutdown()
    assert seen == ["ok"]       # worker survived the exception and kept processing


def test_submit_after_shutdown_is_refused():
    pool = WorkerPool(workers=1, queue_max=8, handler=lambda item: None)
    pool.start()
    pool.shutdown()
    # Once draining has begun, late submits (from in-flight request threads) are refused
    # rather than stranded behind the shutdown sentinels.
    assert pool.submit("late") is False
