import os
import threading
import time

import pytest

from backend.runtime import isolated_execution as iso

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="needs POSIX process groups")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def _sleep_forever(pidfile):
    import subprocess

    grandchild = subprocess.Popen(["sleep", "60"])
    with open(pidfile, "w") as f:
        f.write(f"{os.getpid()} {grandchild.pid}")
    time.sleep(60)


def _start(tmp_path):
    pidfile = tmp_path / "pids"
    outcome = {}

    def run():
        try:
            iso.run_isolated(_sleep_forever, args=(str(pidfile),), timeout=30)
        except iso.IsolatedExecutionError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text().count(" ")):
        time.sleep(0.05)
    child, grandchild = map(int, pidfile.read_text().split())
    return thread, outcome, child, grandchild


def test_shutdown_terminates_active_child_its_subprocess_and_frees_the_caller(tmp_path):
    thread, outcome, child, grandchild = _start(tmp_path)
    try:
        assert iso._ACTIVE  # tracked while running
        assert iso.terminate_active_processes(grace_seconds=2) == 1

        thread.join(10)
        assert not thread.is_alive()  # run_isolated returned: its slot/capacity is free
        assert isinstance(outcome["error"], iso.IsolatedExecutionError)
        assert not _alive(child) and not _alive(grandchild)
        assert not iso._ACTIVE
    finally:
        for pid in (child, grandchild):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def test_shutdown_with_nothing_running_is_a_noop():
    assert iso.terminate_active_processes() == 0


def test_shutdown_after_normal_completion_has_nothing_to_clean():
    assert iso.run_isolated(lambda: 7, timeout=10) == 7
    assert not iso._ACTIVE
    assert iso.terminate_active_processes() == 0


def test_child_already_exited_before_cleanup_is_tolerated():
    class Gone:
        pid = 2**22 + 12345  # no such process

        def terminate(self):
            raise ProcessLookupError

    iso._ACTIVE[Gone.pid] = Gone()
    try:
        assert iso.terminate_active_processes(grace_seconds=0.1) == 1
    finally:
        iso._ACTIVE.pop(Gone.pid, None)


def test_sigterm_ignoring_child_is_escalated_to_kill(tmp_path):
    def stubborn():
        import signal

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (tmp_path / "ready").write_text("1")
        time.sleep(60)

    thread = threading.Thread(target=lambda: _swallow(stubborn), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (tmp_path / "ready").exists():
        time.sleep(0.05)
    pid = next(iter(iso._ACTIVE))
    started = time.monotonic()

    iso.terminate_active_processes(grace_seconds=0.5)
    thread.join(10)

    assert not _alive(pid) and time.monotonic() - started < 5


def _swallow(func):
    try:
        iso.run_isolated(func, timeout=30)
    except iso.IsolatedExecutionError:
        pass
