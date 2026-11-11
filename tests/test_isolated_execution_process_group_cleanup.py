"""Subprocesses spawned by isolated notebook code must not outlive the execution."""
import os
import subprocess
import sys
import time

import pytest

from backend.runtime.isolated_execution import IsolatedExecutionTimeout, run_isolated

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX only")


def _spawn_and_hang(pid_file):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(pid_file, "w") as fh:
        fh.write(str(child.pid))
    time.sleep(60)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie still counts as dead
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def test_grandchild_killed_on_timeout(tmp_path):
    pid_file = tmp_path / "pid"
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated(_spawn_and_hang, (str(pid_file),), timeout=1.5)
    pid = int(pid_file.read_text())
    deadline = time.time() + 3
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def _spawn_and_return(pid_file):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(pid_file, "w") as fh:
        fh.write(str(child.pid))
    return "done"


def _spawn_and_raise(pid_file):
    _spawn_and_return(pid_file)
    raise RuntimeError("boom")


@pytest.mark.parametrize("func,outcome", [(_spawn_and_return, "done"), (_spawn_and_raise, None)])
def test_grandchild_killed_after_return_or_error(tmp_path, func, outcome):
    from backend.runtime.isolated_execution import IsolatedExecutionError

    pid_file = tmp_path / "pid"
    if outcome is None:
        with pytest.raises(IsolatedExecutionError, match="boom"):
            run_isolated(func, (str(pid_file),), timeout=10)
    else:
        assert run_isolated(func, (str(pid_file),), timeout=10) == outcome
    pid = int(pid_file.read_text())
    deadline = time.time() + 3
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)
