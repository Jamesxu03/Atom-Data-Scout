import threading

from worker.llm import QuotaExceeded
from worker.parallel import run_parallel


def test_collects_results_and_errors():
    def boom():
        raise ValueError("bad")

    b = run_parallel([("a", lambda: 1), ("b", boom), ("c", lambda: 3)], workers=3, progress=lambda _: None)
    assert sorted(b.results) == [("a", 1), ("c", 3)]
    assert b.errors == ["b: ValueError: bad"]
    assert b.quota_error is None


def test_quota_stops_unstarted_tasks_but_keeps_finished():
    ran = []
    first_done = threading.Event()

    def ok(i):
        def f():
            ran.append(i)
            first_done.set()
            return i
        return f

    def quota():
        first_done.wait(1)
        raise QuotaExceeded("daily cap")

    # one worker: runs in order, so everything after the quota error is skipped
    tasks = [("t0", ok(0)), ("q", quota)] + [(f"t{i}", ok(i)) for i in range(1, 5)]
    b = run_parallel(tasks, workers=1, progress=lambda _: None)
    assert b.results == [("t0", 0)]
    assert ran == [0]
    assert "daily cap" in b.quota_error
