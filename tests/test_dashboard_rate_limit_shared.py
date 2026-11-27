"""NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB makes the dashboard's request limit hold across workers."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import dashboard, dashboard_rate_limit as rl


HAMMER = ("import sys; from backend import dashboard_rate_limit as rl; "
          "print(*[rl.hit(sys.argv[1], '10.0.0.1', 1000.0, 60)[1] for _ in range(25)])")


def test_concurrent_workers_never_lose_an_increment(tmp_path):
    path = str(tmp_path / "rl.db")
    root = Path(__file__).resolve().parent.parent
    workers = [subprocess.Popen([sys.executable, "-c", HAMMER, path], cwd=root, stdout=subprocess.PIPE, text=True)
               for _ in range(4)]
    counts = sorted(int(c) for w in workers for c in w.communicate(timeout=120)[0].split())
    assert counts == list(range(1, 101))  # 100 hits, each seen exactly once, none repeated


def test_window_semantics_and_cleanup(tmp_path):
    path = str(tmp_path / "rl.db")
    assert rl.hit(path, "a", 100.0, 60) == (100.0, 1)
    assert rl.hit(path, "a", 130.0, 60) == (100.0, 2)
    assert rl.hit(path, "b", 150.0, 60) == (150.0, 1)
    assert rl.hit(path, "a", 161.0, 60) == (161.0, 1)  # a's window ended: a new one starts
    rows = sqlite3.connect(path).execute("SELECT client FROM rate_windows").fetchall()
    assert len(rows) == 2  # expired rows are removed on every hit


def test_clients_are_stored_as_digests_only(tmp_path):
    path = str(tmp_path / "rl.db")
    rl.hit(path, "203.0.113.9", 1.0, 60)
    stored = str(sqlite3.connect(path).execute("SELECT * FROM rate_windows").fetchall())
    assert "203.0.113.9" not in stored and rl.client_id("203.0.113.9") in stored


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEY", raising=False)
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEYS", raising=False)
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEYS_FILE", raising=False)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", str(tmp_path / "rl.db"))
    dashboard._DASHBOARD_RATE_LIMIT_WINDOWS.clear()
    return TestClient(dashboard.app)


def test_limit_is_enforced_from_the_shared_store_not_process_memory(client):
    statuses = []
    for _ in range(5):
        statuses.append(client.get("/api/health").status_code)
        dashboard._DASHBOARD_RATE_LIMIT_WINDOWS.clear()  # a different worker has its own, empty memory
    assert statuses == [200, 200, 200, 429, 429]


def test_a_restart_does_not_reset_the_window(client):
    for _ in range(3):
        client.get("/api/health")
    dashboard._DASHBOARD_RATE_LIMIT_WINDOWS.clear()
    response = TestClient(dashboard.app).get("/api/health")
    assert response.status_code == 429 and response.headers["Retry-After"]


def test_unusable_store_degrades_to_the_in_process_limit(client, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", "/nonexistent-dir/x/rl.db")
    codes = [client.get("/api/health").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
