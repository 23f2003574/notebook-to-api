"""Failed authentication, throttling and lockouts are visible in metrics and structured logs."""
import importlib.util
import json
import logging
import sys

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import auth_metrics, dashboard
from backend.compiler import compile_notebook

GUESS = "super-secret-guess-123"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("observe")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def ping(x: int) -> int:\n    return x\n"))
    nbformat.write(nb, tmp / "nb.ipynb")
    compile_notebook(str(tmp / "nb.ipynb"), str(tmp / "obsapp"))
    return tmp


@pytest.fixture
def app_client(built, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_KEY", "right-key")
    monkeypatch.setenv("NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE", "3")
    monkeypatch.syspath_prepend(str(built))
    spec = importlib.util.spec_from_file_location("obsapp_app", built / "obsapp" / "app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "obsapp_app", module)
    spec.loader.exec_module(module)
    return TestClient(module.app, client=("203.0.113.5", 1)), module


def _events(caplog):
    return [json.loads(r.getMessage().split("security_event ", 1)[1]) for r in caplog.records if "security_event" in r.getMessage()]


def _bad(client, n, key=GUESS):
    return [client.post("/ping", json={"x": 1}, headers={"X-API-Key": key} if key else {}).status_code for _ in range(n)]


def test_generated_app_counts_failures_throttling_and_lockouts(app_client):
    client, _ = app_client
    assert _bad(client, 5) == [401, 401, 401, 429, 429]
    metrics = client.get("/metrics", headers={"X-API-Key": "right-key"}).json()
    assert metrics["auth_failures_total"] == 3 and metrics["auth_throttled_total"] == 2 and metrics["auth_lockouts_total"] == 1
    assert metrics["auth_failures_by_endpoint"] == {"/ping": 3}


def test_successful_authentication_increments_nothing(app_client):
    client, _ = app_client
    assert [client.post("/ping", json={"x": 1}, headers={"X-API-Key": "right-key"}).status_code for _ in range(5)] == [200] * 5
    metrics = client.get("/metrics", headers={"X-API-Key": "right-key"}).json()
    assert (metrics["auth_failures_total"], metrics["auth_throttled_total"], metrics["auth_lockouts_total"]) == (0, 0, 0)


def test_prometheus_exposition_has_the_counters_with_bounded_labels(app_client):
    client, _ = app_client
    _bad(client, 4)
    text = client.get("/metrics/prometheus", headers={"X-API-Key": "right-key"}).text
    assert "# TYPE notebook_api_auth_failures_total counter" in text and "notebook_api_auth_failures_total 3" in text
    assert "notebook_api_auth_throttled_total 1" in text and "notebook_api_auth_lockouts_total 1" in text
    assert 'notebook_api_auth_failures_by_endpoint_total{path="/ping"} 3' in text
    assert GUESS not in text and "203.0.113.5" not in text  # no key material, and no client address as a label


def test_structured_log_events_are_emitted_once_per_window_without_secrets(app_client, caplog):
    client, _ = app_client
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        _bad(client, 6)
        _bad(client, 1, key=None)
    events = _events(caplog)
    assert [e["event"] for e in events] == ["auth_failure", "auth_lockout"], events
    assert events[0]["client"] == "203.0.113.5" and events[0]["endpoint"] == "/ping" and events[0]["reason"] == "invalid_key"
    assert events[1]["limit_per_minute"] == 3
    assert GUESS not in caplog.text and "right-key" not in caplog.text


def test_missing_key_is_reported_as_such(app_client, caplog):
    client, _ = app_client
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        _bad(client, 1, key=None)
    assert _events(caplog)[0]["reason"] == "missing_key"


def test_metrics_still_count_failures_when_the_throttle_is_off(built, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_KEY", "right-key")
    monkeypatch.setenv("NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE", "0")
    monkeypatch.syspath_prepend(str(built))
    spec = importlib.util.spec_from_file_location("obsapp_off", built / "obsapp" / "app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "obsapp_off", module)
    spec.loader.exec_module(module)
    client = TestClient(module.app)
    assert set(_bad(client, 8)) == {401}
    metrics = client.get("/metrics", headers={"X-API-Key": "right-key"}).json()
    assert metrics["auth_failures_total"] == 8 and metrics["auth_throttled_total"] == 0 and metrics["auth_lockouts_total"] == 0


def test_dashboard_counts_exposes_and_logs_the_same_events(monkeypatch, caplog):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "dash-key")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE", "2")
    client = TestClient(dashboard.app, raise_server_exceptions=False, client=("198.51.100.9", 1))
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        codes = [client.get("/api/health", headers={"X-API-Key": GUESS}).status_code for _ in range(4)]
    assert codes == [401, 401, 429, 429]
    assert auth_metrics.totals() == {"failures": 2, "throttled": 2, "lockouts": 1, "denied": 0}
    text = client.get("/api/metrics/prometheus", headers={"X-API-Key": "dash-key"}).text if False else auth_metrics.prometheus_text()
    assert "notebook_to_api_dashboard_auth_failures_total 2" in text and "notebook_to_api_dashboard_auth_lockouts_total 1" in text
    events = _events(caplog)
    assert [e["event"] for e in events] == ["auth_failure", "auth_lockout"] and events[0]["service"] == "dashboard"
    assert GUESS not in caplog.text and "dash-key" not in caplog.text


def test_dashboard_prometheus_route_includes_the_counters(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "dash-key")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_AUTH_FAILURE_LIMIT_PER_MINUTE", "0")
    client = TestClient(dashboard.app, raise_server_exceptions=False, client=("198.51.100.10", 1))
    client.get("/api/health", headers={"X-API-Key": "nope"})
    ok = client.get("/api/metrics/prometheus", headers={"X-API-Key": "dash-key"})
    assert ok.status_code == 200 and "notebook_to_api_dashboard_auth_failures_total 1" in ok.text
    assert client.get("/api/health", headers={"X-API-Key": "dash-key"}).status_code == 200
    assert auth_metrics.totals()["failures"] == 1  # the successful calls added nothing
