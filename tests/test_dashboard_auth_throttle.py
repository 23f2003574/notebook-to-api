"""The dashboard throttles clients that keep sending wrong API keys."""
import threading

import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.dashboard import app

KEY = "dashboard-secret"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE", "4")
    dashboard._AUTH_FAILURES.clear()
    yield
    dashboard._AUTH_FAILURES.clear()


def _client(peer=None):
    return TestClient(app, raise_server_exceptions=False, client=peer) if peer else TestClient(app, raise_server_exceptions=False)


def test_repeated_wrong_keys_are_throttled_with_retry_after():
    client = _client(("203.0.113.5", 1))
    codes = [client.get("/api/notebooks", headers={"X-API-Key": f"bad{i}"}).status_code for i in range(7)]
    assert codes == [401] * 4 + [429] * 3
    blocked = client.get("/api/notebooks", headers={"X-API-Key": "bad-again"})
    assert blocked.status_code == 429 and 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert "bad-again" not in blocked.text and KEY not in blocked.text


def test_the_correct_key_works_while_under_the_limit_and_successes_are_free():
    client = _client(("203.0.113.6", 1))
    assert [client.get("/api/health", headers={"X-API-Key": KEY}).status_code for _ in range(20)] == [200] * 20
    assert client.get("/api/health").status_code == 401


def test_clients_are_throttled_independently():
    a, b = _client(("203.0.113.7", 1)), _client(("203.0.113.8", 1))
    for _ in range(4):
        a.get("/api/health", headers={"X-API-Key": "x"})
    assert a.get("/api/health", headers={"X-API-Key": KEY}).status_code == 429  # documented lockout of that address
    assert b.get("/api/health", headers={"X-API-Key": KEY}).status_code == 200


def test_expired_windows_are_dropped(monkeypatch):
    for _ in range(4):
        dashboard._auth_record_failure("198.51.100.1")
    assert dashboard._auth_retry_after_if_blocked("198.51.100.1") is not None
    now = dashboard.time.time()
    monkeypatch.setattr(dashboard.time, "time", lambda: now + 61)
    assert dashboard._auth_retry_after_if_blocked("198.51.100.1") is None
    assert "198.51.100.1" not in dashboard._AUTH_FAILURES


def test_tracking_is_bounded():
    for i in range(dashboard._AUTH_FAILURE_MAX_CLIENTS + 100):
        dashboard._auth_record_failure(f"client-{i}")
    assert len(dashboard._AUTH_FAILURES) <= dashboard._AUTH_FAILURE_MAX_CLIENTS


def test_concurrent_failures_are_all_counted():
    threads = [threading.Thread(target=lambda: [dashboard._auth_record_failure("c") for _ in range(25)]) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert dashboard._AUTH_FAILURES["c"][1] == 200


def test_zero_disables_and_a_bad_value_is_rejected_clearly(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE", "0")
    client = _client(("203.0.113.9", 1))
    assert {client.get("/api/health", headers={"X-API-Key": f"b{i}"}).status_code for i in range(40)} == {401}
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE", "many")
    with pytest.raises(RuntimeError, match="whole number"):
        dashboard.dashboard_auth_failure_limit_per_minute()


def test_remote_refusal_without_a_key_is_not_counted_as_a_guess(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "")
    client = _client(("203.0.113.10", 1))
    assert {client.get("/api/health").status_code for _ in range(10)} == {401}
    assert dashboard._AUTH_FAILURES == {}
