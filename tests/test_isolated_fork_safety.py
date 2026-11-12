"""Forking from a multithreaded server: serialized forks, explicit start failures."""
import os
import threading
import time

import pytest

from backend.runtime import isolated_execution as iso

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg"), reason="isolation needs POSIX process groups")


def _double(x):
    return x * 2


def _hang_before_handshake(conn, *args, **kwargs):
    time.sleep(60)  # a child that deadlocked right after fork never says "started"


def test_many_threads_run_isolated_concurrently_without_deadlock_or_crosstalk():
    noise_stop = threading.Event()

    def noise():  # keep other threads busy with locks/allocation while forks happen
        import logging
        log = logging.getLogger("fork-noise")
        while not noise_stop.is_set():
            log.debug("x" * 100)
            _ = [bytearray(1000) for _ in range(50)]

    noisy = [threading.Thread(target=noise, daemon=True) for _ in range(3)]
    for t in noisy:
        t.start()
    results = {}

    def call(i):
        results[i] = iso.run_isolated(_double, (i,), timeout=20)

    threads = [threading.Thread(target=call, args=(i,)) for i in range(24)]
    began = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    noise_stop.set()
    assert not any(t.is_alive() for t in threads) and time.monotonic() - began < 60
    assert results == {i: i * 2 for i in range(24)}
    assert not iso._ACTIVE


def test_forks_are_serialized():
    held = []
    original = iso._FORK_LOCK

    class Spy:
        def __enter__(self):
            original.acquire()
            held.append(True)

        def __exit__(self, *exc):
            original.release()

    iso._FORK_LOCK = Spy()
    try:
        assert iso.run_isolated(_double, (4,), timeout=10) == 8
    finally:
        iso._FORK_LOCK = original
    assert held == [True, True]  # one for the fork, one for the reap


def test_child_that_never_starts_fails_explicitly_and_is_reaped(monkeypatch):
    monkeypatch.setattr(iso, "_child", _hang_before_handshake)
    monkeypatch.setattr(iso, "_START_TIMEOUT_SECONDS", 0.5)
    began = time.monotonic()
    with pytest.raises(iso.IsolatedExecutionError, match="did not start"):
        iso.run_isolated(_double, (1,), timeout=None)  # no call timeout: must still not hang
    assert time.monotonic() - began < 10
    assert not iso._ACTIVE


def test_limits_timeout_and_errors_still_behave_after_the_handshake():
    with pytest.raises(iso.IsolatedExecutionTimeout):
        iso.run_isolated(time.sleep, (30,), timeout=0.5)
    with pytest.raises(iso.IsolatedExecutionError, match="ValueError"):
        iso.run_isolated(int, ("nope",), timeout=10)
    with pytest.raises(iso.IsolatedExecutionError, match="memory limit exceeded"):
        iso.run_isolated(lambda: bytearray(600 * 1024 * 1024), timeout=10, memory_limit_mb=300)
