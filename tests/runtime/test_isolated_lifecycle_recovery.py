import multiprocessing as mp
import os
import signal
import sys
import threading
import time
from multiprocessing.connection import Connection

import pytest

from backend.runtime.isolated_execution import IsolatedExecutionError, run_isolated

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX lifecycle tests")


def _add(a, b):
    return a + b


def _publish_pid_then_block(pid_path):
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(120)


def _wait_for_pid(pid_path, deadline_s=10):
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            text = open(pid_path).read()
        except FileNotFoundError:
            text = ""
        if text:
            return int(text)
        time.sleep(0.01)
    raise AssertionError("child never published its pid")


def _assert_gone(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # reaped: not even a zombie remains


def _open_fds():
    return len(os.listdir("/proc/self/fd"))


def test_child_killed_by_external_signal_is_reported_and_reaped(tmp_path):
    pid_path = str(tmp_path / "pid")
    killed = {}

    def killer():
        killed["pid"] = _wait_for_pid(pid_path)
        os.kill(killed["pid"], signal.SIGKILL)

    t = threading.Thread(target=killer)
    t.start()
    with pytest.raises(IsolatedExecutionError, match="exitcode=-9"):
        run_isolated(_publish_pid_then_block, (pid_path,), timeout=30)
    t.join()
    _assert_gone(killed["pid"])
    assert mp.active_children() == []
    assert run_isolated(_add, (1, 2), timeout=10) == 3


def test_parent_interrupt_mid_execution_kills_the_real_child_and_recovers(tmp_path, monkeypatch):
    pid_path = str(tmp_path / "pid")
    real_poll = Connection.poll

    def interrupt_once_child_is_running(self, timeout=None):
        _wait_for_pid(pid_path)
        raise KeyboardInterrupt

    monkeypatch.setattr(Connection, "poll", interrupt_once_child_is_running)
    with pytest.raises(KeyboardInterrupt):
        run_isolated(_publish_pid_then_block, (pid_path,), timeout=30)
    monkeypatch.setattr(Connection, "poll", real_poll)

    _assert_gone(int(open(pid_path).read()))
    assert mp.active_children() == []
    assert run_isolated(_add, (2, 3), timeout=10) == 5


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc/self/fd")
def test_repeated_failures_do_not_leak_file_descriptors(tmp_path):
    run_isolated(_add, (1, 1), timeout=10)  # warm up lazy imports/handles
    baseline = _open_fds()
    for i in range(3):
        with pytest.raises(IsolatedExecutionError):
            run_isolated(os._exit, (7,), timeout=10)
        with pytest.raises(Exception):
            run_isolated(_publish_pid_then_block, (str(tmp_path / f"p{i}"),), timeout=0.3)
    assert mp.active_children() == []
    assert _open_fds() == baseline
