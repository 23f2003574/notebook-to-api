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
