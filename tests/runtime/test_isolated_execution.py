import multiprocessing as mp
import os
import time

import pytest

from backend.runtime.isolated_execution import (
    IsolatedExecutionError,
    IsolatedExecutionTimeout,
    run_isolated,
)


def _add(a, b=0):
    return a + b


def _boom():
    raise ValueError("bad input")


def _sleep_forever():
    time.sleep(60)


def _pid():
    return os.getpid()


def test_success_returns_result():
    assert run_isolated(_add, (1,), {"b": 2}, timeout=10) == 3


def test_runs_in_separate_process():
    assert run_isolated(_pid, timeout=10) != os.getpid()


def test_exception_is_controlled_error():
    with pytest.raises(IsolatedExecutionError, match="ValueError: bad input"):
        run_isolated(_boom, timeout=10)


def test_timeout_terminates_child():
    start = time.monotonic()
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated(_sleep_forever, timeout=0.5)
    assert time.monotonic() - start < 10


def test_no_child_processes_left_behind():
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated(_sleep_forever, timeout=0.3)
    run_isolated(_add, (1, 1), timeout=10)
    assert mp.active_children() == []


def _ignore_sigterm_and_hang():
    import signal

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)


def _hard_exit():
    os._exit(3)


def _unpicklable():
    return lambda: None


def test_sigterm_ignoring_child_is_force_killed():
    start = time.monotonic()
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated(_ignore_sigterm_and_hang, timeout=0.5)
    assert time.monotonic() - start < 6
    assert mp.active_children() == []


def test_abnormal_exit_is_controlled_error_and_reaped():
    with pytest.raises(IsolatedExecutionError, match="exitcode=3"):
        run_isolated(_hard_exit, timeout=5)
    assert mp.active_children() == []


def test_unserializable_result_is_controlled_error():
    with pytest.raises(IsolatedExecutionError):
        run_isolated(_unpicklable, timeout=5)
    assert mp.active_children() == []


def test_parent_interrupt_cleans_up_child(monkeypatch):
    from multiprocessing.connection import Connection

    def interrupted(self, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(Connection, "poll", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_isolated(_sleep_forever, timeout=30)
    assert mp.active_children() == []


def _allocate(mb):
    return len(bytearray(mb * 1024 * 1024))


@pytest.mark.skipif(not hasattr(__import__("os"), "fork") or __import__("sys").platform == "darwin",
                    reason="RLIMIT_AS is POSIX-only and not enforced on macOS")
def test_memory_limit_is_enforced_and_child_reaped():
    assert run_isolated(_allocate, (1,), timeout=10, memory_limit_mb=2048) == 1024 * 1024
    with pytest.raises(IsolatedExecutionError, match="memory limit exceeded"):
        run_isolated(_allocate, (4096,), timeout=10, memory_limit_mb=2048)
    assert mp.active_children() == []


def test_no_memory_limit_by_default():
    assert run_isolated(_allocate, (64,), timeout=10) == 64 * 1024 * 1024


def _burn_cpu():
    while True:
        pass


@pytest.mark.skipif(not hasattr(os, "fork"), reason="RLIMIT_CPU is POSIX-only")
def test_cpu_limit_is_enforced_distinct_from_wall_clock_and_child_reaped():
    start = time.monotonic()
    with pytest.raises(IsolatedExecutionError, match="CPU limit exceeded") as info:
        run_isolated(_burn_cpu, timeout=30, cpu_limit_seconds=1)
    assert not isinstance(info.value, IsolatedExecutionTimeout)
    assert time.monotonic() - start < 10
    assert mp.active_children() == []


@pytest.mark.skipif(not hasattr(os, "fork"), reason="RLIMIT_CPU is POSIX-only")
def test_idle_time_does_not_count_toward_cpu_limit():
    assert run_isolated(_add, (1, 2), timeout=10, cpu_limit_seconds=1) == 3
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated(_sleep_forever, timeout=2, cpu_limit_seconds=1)
    assert mp.active_children() == []
