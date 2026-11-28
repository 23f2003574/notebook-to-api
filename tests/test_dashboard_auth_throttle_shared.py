"""The failed-authentication throttle counts in the shared store, so it holds across worker processes."""
import os
import subprocess
import sys
from pathlib import Path

from backend import dashboard, dashboard_rate_limit as rl

ROOT = str(Path(__file__).resolve().parent.parent)
WORKER = ("import sys\nfrom backend import dashboard\n"
          "print(sorted(dashboard._auth_record_failure('203.0.113.7') for _ in range(int(sys.argv[1]))))")


def _env(path, limit="6"):
    return {**os.environ, "NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB": path, "NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE": limit}


def test_failures_in_separate_workers_add_up_to_one_threshold(tmp_path, monkeypatch):
    path = str(tmp_path / "shared.db")
    env = _env(path)
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, "3"], cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
             for _ in range(2)]
    counts = sorted(int(n) for p in procs for n in p.communicate(timeout=120)[0].strip("[]\n").split(","))
    assert counts == [1, 2, 3, 4, 5, 6]  # six attempts split over two workers, each counted exactly once
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert dashboard._auth_retry_after_if_blocked("203.0.113.7") >= 1  # a third process sees the block
    assert dashboard._auth_retry_after_if_blocked("198.51.100.1") is None  # other clients are unaffected


def test_below_the_limit_is_not_blocked_and_the_window_expires(tmp_path, monkeypatch):
    path = str(tmp_path / "shared.db")
    for k, v in _env(path, "3").items():
        monkeypatch.setenv(k, v)
    dashboard._auth_record_failure("c")
    dashboard._auth_record_failure("c")
    assert dashboard._auth_retry_after_if_blocked("c") is None
    dashboard._auth_record_failure("c")
    assert dashboard._auth_retry_after_if_blocked("c") is not None
    assert rl.peek(path, "c", 1e12, 60) == 0  # a finished window counts as empty
    assert rl.hit(path, "c", 1e12, 60, "auth_failures") == (1e12, 1)


def test_identifiers_are_digests_and_store_failure_degrades_to_this_worker(tmp_path, monkeypatch):
    import sqlite3
    path = str(tmp_path / "shared.db")
    for k, v in _env(path, "2").items():
        monkeypatch.setenv(k, v)
    dashboard._auth_record_failure("203.0.113.55")
    assert "203.0.113.55" not in str(sqlite3.connect(path).execute("SELECT * FROM auth_failures").fetchall())
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", str(tmp_path / "no" / "such" / "dir" / "x.db"))
    dashboard._AUTH_FAILURES.clear()
    assert dashboard._auth_record_failure("z") == 1 and dashboard._auth_record_failure("z") == 2
    assert dashboard._auth_retry_after_if_blocked("z") is not None  # still throttled, in this worker
