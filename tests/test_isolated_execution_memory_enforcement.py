"""Real OS enforcement: a child that allocates past its memory limit must be
stopped by RLIMIT_AS and reported as a distinguishable, controlled failure.
Nothing here is mocked; the allocation is bounded (~192 MB over the cap)."""

import os

import pytest

from backend.runtime.isolated_execution import IsolatedExecutionError, run_isolated

resource = pytest.importorskip("resource", reason="memory limits need resource.setrlimit (POSIX)")
pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="needs POSIX process groups")


def _address_space_mb():
    # The cap covers the whole inherited address space, so size it from the
    # real baseline instead of guessing.
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    except OSError:
        pytest.skip("cannot measure address space here (no /proc); e.g. macOS does not enforce RLIMIT_AS")


def _allocate(mb):
    block = bytearray(mb * 2**20)
    return len(block)


def _spawn_and_hang():
    import subprocess

    subprocess.Popen(["sleep", "30"])
    return bytearray(192 * 2**20)


def _probe_enforced(limit_mb):
    try:
        run_isolated(_allocate, args=(192,), timeout=20, memory_limit_mb=limit_mb)
    except IsolatedExecutionError:
        return True
    return False


def test_allocation_beyond_the_limit_is_stopped_and_reported():
    limit = _address_space_mb() + 64
    if not _probe_enforced(limit):
        pytest.skip("this OS does not enforce RLIMIT_AS")

    with pytest.raises(IsolatedExecutionError, match="memory limit exceeded"):
        run_isolated(_allocate, args=(192,), timeout=20, memory_limit_mb=limit)


def test_allocation_within_the_limit_still_succeeds():
    limit = _address_space_mb() + 64
    if not _probe_enforced(limit):
        pytest.skip("this OS does not enforce RLIMIT_AS")

    assert run_isolated(_allocate, args=(8,), timeout=20, memory_limit_mb=limit) == 8 * 2**20


def test_subprocess_started_before_the_memory_failure_is_cleaned_up():
    import subprocess
    import time

    limit = _address_space_mb() + 64
    if not _probe_enforced(limit):
        pytest.skip("this OS does not enforce RLIMIT_AS")
    import shutil

    if not shutil.which("pgrep"):
        pytest.skip("needs pgrep to count leftover processes")

    def leftovers():
        return int(subprocess.run(["pgrep", "-fc", "^sleep 30$"], capture_output=True, text=True).stdout.strip() or 0)

    before = leftovers()

    with pytest.raises(IsolatedExecutionError):
        run_isolated(_spawn_and_hang, timeout=20, memory_limit_mb=limit)

    time.sleep(0.3)
    assert leftovers() <= before
