"""Dashboard deployment files persist the governance audit trail, and startup refuses non-durable production storage."""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from backend.generator.dashboard_deployment import generate_dashboard_deployment
from backend.observability.deployment_governance_audit_store import (
    GovernanceAuditStorageError,
    audit_storage_status,
    validate_audit_storage_for_startup,
)


@pytest.fixture
def files(tmp_path):
    generate_dashboard_deployment(tmp_path)
    return tmp_path


def test_dockerfile_declares_the_audit_volume_and_production_sqlite(files):
    text = (files / "Dockerfile").read_text()
    assert "VOLUME /data" in text
    assert "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND=sqlite" in text
    assert "NOTEBOOK2API_ENVIRONMENT=production" in text
    assert "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH=/data/governance/audit.db" in text


def test_compose_mounts_a_named_volume_over_the_database_directory(files):
    doc = yaml.safe_load((files / "docker-compose.yml").read_text())
    svc = doc["services"]["dashboard"]
    assert "governance-data:/data" in svc["volumes"] and "governance-data" in doc["volumes"]
    env = svc["environment"]
    assert env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"].startswith("/data/") and env["NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT"] == "1"


def test_kubernetes_uses_a_pvc_not_emptydir_and_never_two_writers(files):
    docs = list(yaml.safe_load_all((files / "kubernetes.yaml").read_text()))
    pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
    dep = next(d for d in docs if d["kind"] == "Deployment")
    assert dep["spec"]["replicas"] == 1 and dep["spec"]["strategy"]["type"] == "Recreate"
    volumes = dep["spec"]["template"]["spec"]["volumes"]
    assert volumes[0]["persistentVolumeClaim"]["claimName"] == pvc["metadata"]["name"]
    assert "emptyDir" not in (files / "kubernetes.yaml").read_text()


def test_no_credentials_are_written_to_any_generated_file(files):
    for name in ("Dockerfile", "docker-compose.yml", "kubernetes.yaml"):
        text = (files / name).read_text()
        assert "change-me" not in text
    assert "secretKeyRef" in (files / "kubernetes.yaml").read_text()
    assert "${NOTEBOOK_API_DASHBOARD_KEY:?" in (files / "docker-compose.yml").read_text()


def test_the_cli_writes_the_files(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "backend.cli", "dashboard-deployment", "--output", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert {p.name for p in out.iterdir()} == {"Dockerfile", "docker-compose.yml", "kubernetes.yaml", ".env.example"}


def test_status_never_claims_durability_for_memory_or_temp_storage(tmp_path):
    assert audit_storage_status({})["durable"] is False
    tmpish = audit_storage_status({"NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "sqlite",
                                   "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": "/tmp/x/audit.db"})
    assert tmpish["durable"] is False and "temporary" in tmpish["issues"][0]
    assert "/tmp/x" not in str(tmpish)


def test_status_trusts_an_asserted_volume_and_startup_validates(tmp_path):
    env = {"NOTEBOOK2API_ENVIRONMENT": "production", "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "sqlite",
           "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": str(tmp_path / "a.db"),
           "NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT": "1"}
    assert validate_audit_storage_for_startup(env)["durable"] is True


def test_production_startup_refuses_ephemeral_or_unset_storage():
    with pytest.raises(GovernanceAuditStorageError, match="not durable"):
        validate_audit_storage_for_startup({"NOTEBOOK2API_ENVIRONMENT": "production",
                                            "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "sqlite",
                                            "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": "/tmp/n2a-never-created/gov/audit.db"})
    assert not Path("/tmp/n2a-never-created").exists()  # refused before anything was created there
    with pytest.raises(GovernanceAuditStorageError, match="PERSISTENCE_BACKEND"):
        validate_audit_storage_for_startup({"NOTEBOOK2API_ENVIRONMENT": "production"})


def test_the_dashboard_process_exits_with_an_actionable_error_in_misconfigured_production():
    import os

    env = {**os.environ, "NOTEBOOK2API_ENVIRONMENT": "production", "NOTEBOOK_API_DASHBOARD_PORT": "0"}
    env.pop("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", None)
    r = subprocess.run([sys.executable, "-m", "backend.dashboard"], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode != 0 and "PERSISTENCE_BACKEND" in (r.stderr + r.stdout)
