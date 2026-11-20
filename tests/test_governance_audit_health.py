"""Audit-storage health is visible, separate from compile/deploy outcomes, and leaks nothing."""
import json
import logging

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app
from backend.observability import deployment_governance_audit as audit
from backend.observability.deployment_governance_audit_health import audit_health
from backend.observability.deployment_governance_audit_store import GovernanceAuditStorageError

client = TestClient(app)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "audit.db"))
    audit.reset_audit_service()
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(x: int) -> int:\n    return x\n"))
    nbformat.write(nb, tmp_path / "uploads" / "secret_name.ipynb")
    yield tmp_path
    audit.reset_audit_service()


def _compile():
    return client.post("/api/compile", json={"notebook_path": "secret_name.ipynb", "per_notebook": True})


def _health():
    return client.get("/governance/audit/health")


def _prom():
    return client.get("/api/metrics/prometheus").text


def test_healthy_operation_is_reported(env):
    assert _compile().status_code == 200
    body = _health().json()
    assert _health().status_code == 200
    assert body["state"] == "healthy" and body["backend"] == "sqlite" and body["writes_total"] >= 1
    assert body["consecutive_failures"] == 0 and sum(body["write_failures_total"].values()) == 0
    text = _prom()
    assert "notebook_to_api_governance_audit_up 1" in text
    assert 'notebook_to_api_governance_audit_write_failures_total{reason="storage_error"} 0' in text


def test_a_write_failure_is_degraded_not_an_operation_failure(env, caplog):
    _compile()
    store = audit.get_audit_service()._store
    original = store.append
    store.append = lambda build: (_ for _ in ()).throw(GovernanceAuditStorageError("governance audit write failed (OperationalError)"))
    try:
        with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
            resp = _compile()
        assert resp.status_code == 200 and "governance_audit_error" in resp.json()  # the compile itself succeeded
        body = _health().json()
        assert body["state"] == "degraded" and body["reason"] == "storage_error"
        assert body["write_failures_total"]["storage_error"] == 1 and body["consecutive_failures"] == 1
        assert "notebook_to_api_governance_audit_up 0" in _prom()
        events = [json.loads(r.getMessage().split("governance_audit_event ", 1)[1]) for r in caplog.records if "governance_audit_event" in r.getMessage()]
        assert events == [{"event": "audit_write_failed", "reason": "storage_error", "consecutive_failures": 1}]

        # a real compile failure while auditing is down is still reported as the compile's own failure
        assert client.post("/api/compile", json={"notebook_path": "absent.ipynb"}).status_code >= 400
    finally:
        store.append = original


def test_recovery_after_the_storage_comes_back(env, caplog):
    _compile()
    store = audit.get_audit_service()._store
    original = store.append
    store.append = lambda build: (_ for _ in ()).throw(GovernanceAuditStorageError("down"))
    _compile()
    store.append = original
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert "governance_audit_error" not in _compile().json()
    body = _health().json()
    assert body["state"] == "healthy" and body["consecutive_failures"] == 0 and body["recovered_at"]
    assert body["write_failures_total"]["storage_error"] == 1  # history is kept
    assert any("audit_write_recovered" in r.getMessage() for r in caplog.records)


def test_an_unavailable_backend_is_503_and_diagnosable_without_paths(env):
    _compile()
    store = audit.get_audit_service()._store
    store.count = lambda: (_ for _ in ()).throw(GovernanceAuditStorageError("governance audit read failed (OperationalError)"))
    resp = _health()
    assert resp.status_code == 503
    body = resp.json()
    assert body["state"] == "unavailable" and body["reason"] == "storage_error"
    assert "notebook_to_api_governance_audit_up 0" in _prom()
    assert str(env) not in resp.text


def test_misconfiguration_is_its_own_reason(env, monkeypatch):
    monkeypatch.delenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND")
    monkeypatch.setenv("NOTEBOOK2API_ENVIRONMENT", "production")
    audit.reset_audit_service()
    resp = _compile()
    assert resp.status_code == 200 and "governance_audit_error" in resp.json()
    body = _health().json()
    assert body["state"] == "unavailable" and body["reason"] == "configuration"
    assert audit_health.failures["configuration"] >= 1


def test_nothing_sensitive_reaches_metrics_health_or_logs(env, caplog):
    _compile()
    store = audit.get_audit_service()._store
    store.append = lambda build: (_ for _ in ()).throw(GovernanceAuditStorageError("down"))
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        _compile()
    everything = _prom() + _health().text + caplog.text
    for secret in ("secret_name", str(env), "audit.db"):
        assert secret not in everything
    labels = {line.split("{", 1)[1].split("}")[0] for line in _prom().splitlines() if "governance_audit" in line and "{" in line}
    assert labels <= {f'reason="{r}"' for r in ("storage_error", "configuration", "other")}
