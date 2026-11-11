import os
import threading
import time

import pytest

from backend.runtime import isolated_execution as iso
from backend.runtime.isolated_execution import IsolatedExecutionError, IsolatedExecutionTimeout, IsolatedWorker

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="needs POSIX process groups")

STATE = {"calls": 0}


def handler(payload):
    kind = payload["kind"]
    if kind == "count":
        STATE["calls"] += 1
        return {"calls": STATE["calls"], "pid": os.getpid()}
    if kind == "crash":
        os._exit(3)
    if kind == "sleep":
        time.sleep(payload["seconds"])
    if kind == "raise":
        raise ValueError("boom")
    return payload.get("value")


@pytest.fixture
def worker():
    w = IsolatedWorker(handler, startup_grace_seconds=1)
    yield w
    w.close()


def test_start_serve_and_clean_shutdown(worker):
    assert not worker.running
    assert worker.request({"kind": "echo", "value": 5}, timeout=10) == 5
    pid = worker._proc.pid
    assert worker.running and pid in iso._WORKERS

    worker.close()

    assert not worker.running and pid not in iso._WORKERS
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # reaped, not a zombie


def test_many_sequential_requests_reuse_one_worker(worker):
    for i in range(5):
        assert worker.request({"kind": "echo", "value": i}, timeout=10) == i

    assert worker.starts == 1


def test_state_never_leaks_between_requests(worker):
    results = [worker.request({"kind": "count"}, timeout=10) for _ in range(3)]

    assert [r["calls"] for r in results] == [1, 1, 1]  # each request saw pristine state
    assert len({r["pid"] for r in results}) == 3  # each ran in its own child
    assert STATE["calls"] == 0  # and the app's own state is untouched


def test_handler_exception_is_reported_and_worker_keeps_serving(worker):
    with pytest.raises(IsolatedExecutionError, match="ValueError: boom"):
        worker.request({"kind": "raise"}, timeout=10)

    assert worker.request({"kind": "echo", "value": 1}, timeout=10) == 1
    assert worker.starts == 1


def test_child_crash_is_contained(worker):
    with pytest.raises(IsolatedExecutionError, match="exitcode=3"):
        worker.request({"kind": "crash"}, timeout=10)

    assert worker.request({"kind": "echo", "value": 2}, timeout=10) == 2


def test_timeout_kills_the_request_and_the_next_one_still_works(worker):
    started = time.monotonic()
    with pytest.raises(IsolatedExecutionTimeout):
        worker.request({"kind": "sleep", "seconds": 30}, timeout=0.5)

    assert time.monotonic() - started < 10
    assert worker.request({"kind": "echo", "value": 3}, timeout=10) == 3


def test_killed_worker_is_replaced_not_reused(worker):
    worker.request({"kind": "echo"}, timeout=10)
    old = worker._proc.pid
    os.kill(old, 9)
    time.sleep(0.2)

    assert worker.request({"kind": "echo", "value": 4}, timeout=10) == 4
    assert worker.starts == 2 and worker._proc.pid != old


def test_app_shutdown_cleanup_also_stops_workers(worker):
    worker.request({"kind": "echo"}, timeout=10)
    pid = worker._proc.pid

    iso.terminate_active_processes(grace_seconds=2)
    time.sleep(0.2)

    assert not worker.running
    assert pid not in iso._WORKERS


def test_concurrent_callers_are_serialized_safely(worker):
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(worker.request({"kind": "echo", "value": i}, timeout=10))) for i in range(4)]
    [t.start() for t in threads]
    [t.join(20) for t in threads]

    assert sorted(out) == [0, 1, 2, 3]
