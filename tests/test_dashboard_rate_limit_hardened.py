"""Trusted client identity, connection reuse, bounded cleanup and the fail-closed option of the shared limiter."""
import sqlite3
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import dashboard, dashboard_rate_limit as rl


def _request(peer, forwarded=None):
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_forwarded_address_is_used_only_from_a_trusted_proxy(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES", "10.0.0.0/8")
    assert dashboard._auth_client(_request("10.1.2.3", "203.0.113.9")) == "203.0.113.9"
    assert dashboard._auth_client(_request("198.51.100.4", "203.0.113.9")) == "198.51.100.4"  # spoofed header
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES")
    assert dashboard._auth_client(_request("10.1.2.3", "203.0.113.9")) == "10.1.2.3"


def test_the_limiter_keys_on_that_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", str(tmp_path / "rl.db"))
    seen = []
    real = rl.hit
    monkeypatch.setattr(rl, "hit", lambda path, client, *a, **k: seen.append(client) or real(path, client, *a, **k))
    TestClient(dashboard.app).get("/api/health")
    assert seen == ["testclient"]  # the peer: nobody trusted, so nothing forwarded is believed


def test_one_connection_per_thread_no_repeated_ddl_and_bounded_cleanup(tmp_path):
    path = str(tmp_path / "rl.db")
    rl._LAST_CLEANUP.clear()
    before = dict(rl.STATS)
    statements = []
    for i in range(50):
        rl.hit(path, f"c{i}", 1000.0 + i * 0.01, 60)
    assert rl.STATS["connections_opened"] - before["connections_opened"] == 1
    assert rl.STATS["cleanups"] - before["cleanups"] == 1  # swept once, not on every request
    conn = rl._connect(path)
    conn.set_trace_callback(statements.append)
    rl.hit(path, "x", 1001.0, 60)
    conn.set_trace_callback(None)
    assert not any("CREATE" in s.upper() or "DELETE" in s.upper() for s in statements)
    other = []
    t = threading.Thread(target=lambda: other.append(rl._connect(path)))
    t.start(); t.join()
    assert other[0] is not conn  # connections are never shared between threads
    rl.hit(path, "late", 1000.0 + 61, 60)  # the interval passed: expired rows go
    assert len(sqlite3.connect(path).execute("SELECT * FROM rate_windows").fetchall()) < 52


def test_unusable_store_fails_open_by_default_and_closed_on_request(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", str(tmp_path / "missing" / "dir" / "rl.db"))
    client = TestClient(dashboard.app)
    assert client.get("/api/health").status_code == 200  # limited in this worker only
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_FAIL_CLOSED", "1")
    response = client.get("/api/health")
    assert response.status_code == 503 and response.headers["Retry-After"] == "5"
