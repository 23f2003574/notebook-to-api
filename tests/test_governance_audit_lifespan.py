"""Audit storage validation runs under every launch path, and health agrees with it."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.observability.deployment_governance_audit import reset_audit_service

ROOT = Path(__file__).resolve().parent.parent


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NOTEBOOK2API_", "NOTEBOOK_API_"))}
    env.update(PYTHONPATH=str(ROOT), NOTEBOOK_API_UPLOAD_DIR=str(tmp_path / "up"),
               NOTEBOOK_API_GENERATED_DIR=str(tmp_path / "gen"), **extra)
    return env


def test_direct_uvicorn_launch_fails_in_production_with_non_durable_storage(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "backend.dashboard:app", "--port", "0"],
        env=_env(tmp_path, NOTEBOOK2API_ENVIRONMENT="production",
                 NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND="sqlite",
                 NOTEBOOK2API_GOVERNANCE_DATABASE_PATH=str(tmp_path / "audit.db")),
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "not durable" in output and "Application startup failed" in output


def test_direct_uvicorn_launch_fails_in_production_without_an_explicit_backend(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "backend.dashboard:app", "--port", "0"],
        env=_env(tmp_path, NOTEBOOK2API_ENVIRONMENT="production"),
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    assert proc.returncode != 0 and "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND" in proc.stdout + proc.stderr


def test_explicit_memory_mode_still_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory")
    reset_audit_service()
    with TestClient(dashboard.app) as client:  # runs the lifespan
        body = client.get("/governance/audit/health").json()
    assert body["state"] == "healthy" and body["durable"] is False


def test_health_and_metrics_report_non_durable_sqlite_as_degraded(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("NOTEBOOK2API_ENVIRONMENT", "development")
    reset_audit_service()
    client = TestClient(dashboard.app)
    body = client.get("/governance/audit/health").json()
    # pytest's tmp_path lives under the system temp directory, which audit_storage_status refuses
    assert body["durable"] is False
    assert body["state"] == "degraded" and body["reason"] == "not_durable"
    metrics = client.get("/api/metrics/prometheus").text
    assert "notebook_to_api_governance_audit_up 0" in metrics
    assert "notebook_to_api_governance_audit_durable 0" in metrics


def test_health_is_healthy_when_a_volume_is_asserted(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT", "1")
    reset_audit_service()
    body = TestClient(dashboard.app).get("/governance/audit/health").json()
    assert body["state"] == "healthy" and body["durable"] is True
    reset_audit_service()


@pytest.fixture(autouse=True)
def _reset():
    reset_audit_service()
    yield
    reset_audit_service()
