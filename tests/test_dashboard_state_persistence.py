"""Notebooks, versions, generated apps and audit history survive a dashboard restart; the deployment
files wire that storage to a volume; health reports whether the storage is durable."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import nbformat
import pytest
import yaml

from backend import dashboard_storage
from backend.generator.dashboard_deployment import generate_dashboard_deployment

ROOT = Path(__file__).resolve().parent.parent
KEY = "persistent-state-key-0123456789"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Dashboard:
    """A real dashboard process over directories that outlive it (what a mounted volume gives)."""

    def __init__(self, data):
        self.data = data
        self.port = _free_port()
        self.proc = None

    def start(self):
        env = {
            **os.environ, "PYTHONPATH": str(ROOT), "NOTEBOOK_API_DASHBOARD_HOST": "127.0.0.1",
            "NOTEBOOK_API_DASHBOARD_PORT": str(self.port), "NOTEBOOK_API_DASHBOARD_KEY": KEY,
            "NOTEBOOK_API_UPLOAD_DIR": str(self.data / "uploads"), "NOTEBOOK_API_GENERATED_DIR": str(self.data / "generated"),
            "NOTEBOOK_API_GENERATED_APPS_DIR": str(self.data / "generated_apps"),
            "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "sqlite",
            "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": str(self.data / "governance" / "audit.db"),
            "NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT": "1", "NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT": "1",
        }
        env.pop("NOTEBOOK2API_ENVIRONMENT", None)
        self.proc = subprocess.Popen([sys.executable, "-m", "backend.dashboard"], cwd=ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(300):
            try:
                if self.get("/api/health").status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.1)
        self.stop()
        pytest.fail("dashboard did not start")

    def stop(self):
        self.proc.kill()
        self.proc.wait(30)

    def get(self, path, **kw):
        return httpx.get(f"http://127.0.0.1:{self.port}{path}", headers={"X-API-Key": KEY}, timeout=30, **kw)

    def post(self, path, **kw):
        return httpx.post(f"http://127.0.0.1:{self.port}{path}", headers={"X-API-Key": KEY}, timeout=120, **kw)


def _bundle(version):
    import io
    import zipfile

    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(f"def f() -> str:\n    return open('w.json').read() + {version!r}\n"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("keep.ipynb", nbformat.writes(nb))
        archive.writestr("w.json", f"data-{version}")
    return buffer.getvalue()


def test_everything_survives_replacing_the_dashboard_process(tmp_path):
    data = tmp_path / "data"
    first = Dashboard(data).start()
    try:
        for version, overwrite in (("v1", "false"), ("v2", "true")):
            r = first.post("/api/notebooks/import", params={"overwrite": overwrite},
                           files={"file": ("b.zip", _bundle(version), "application/zip")})
            assert r.status_code == 200, r.text
        compiled = first.post("/api/compile", json={"notebook_path": "keep.ipynb", "per_notebook": True})
        assert compiled.status_code == 200, compiled.text
        sequence = compiled.json()["governance_audit"]["sequence"]
        before = first.get("/api/notebooks").json()["notebooks"]
    finally:
        first.stop()

    second = Dashboard(data).start()
    try:
        after = second.get("/api/notebooks").json()["notebooks"]
        assert [n["filename"] for n in after] == ["keep.ipynb"]
        assert after[0].get("per_notebook_app") and after[0]["per_notebook_app"]["status"] == "compiled"
        assert after[0]["size_bytes"] == before[0]["size_bytes"]
        versions = second.get("/api/notebooks/keep.ipynb/versions", params={"companions": "true"}).json()["versions"]
        assert len(versions) == 1 and versions[0]["companion_files"] == 1
        assert (data / "uploads" / ".keep.ipynb.files" / "w.json").read_text() == "data-v2"
        record = second.get(f"/governance/audit/{sequence}").json()
        assert record["action"] == "notebook.compile" and second.get("/governance/audit/verify").json()["valid"]
        health = second.get("/api/health").json()
        assert health["notebook_storage"] == {"durable": True, "production": False, "issues": []}
    finally:
        second.stop()


def test_storage_status_never_claims_durability_for_temporary_or_ephemeral_storage(tmp_path, monkeypatch):
    dirs = {"uploads": str(tmp_path / "u"), "generated": str(tmp_path / "g"), "generated_apps": str(tmp_path / "a")}
    temp = dashboard_storage.notebook_storage_status(dirs, {})
    assert temp["durable"] is False and len(temp["issues"]) == 3
    assert str(tmp_path) not in json.dumps(temp)  # no paths
    asserted = dashboard_storage.notebook_storage_status(dirs, {"NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT": "1"})
    assert asserted["durable"] is True
    # in a container, a directory on the same device as "/" is the disposable layer
    monkeypatch.setattr(dashboard_storage, "_in_container", lambda environ: True)
    home = Path.home() / ".n2a-storage-probe"
    local = dashboard_storage.notebook_storage_status({"uploads": str(home)}, {"NOTEBOOK2API_ENVIRONMENT": "production"})
    assert local["production"] is True
    assert local["durable"] is False and "ephemeral filesystem" in local["issues"][0]
    assert dashboard_storage.notebook_storage_status({"uploads": str(home)}, {"NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT": "true"})["durable"]


def test_health_and_metrics_report_non_durable_notebook_storage(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import dashboard
    from backend.routes import upload

    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "u"))
    monkeypatch.delenv("NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT", raising=False)
    client = TestClient(dashboard.app)
    health = client.get("/api/health").json()["notebook_storage"]
    assert health["durable"] is False and health["issues"] and str(tmp_path) not in json.dumps(health)
    assert "notebook_to_api_notebook_storage_durable 0" in client.get("/api/metrics/prometheus").text
    monkeypatch.setenv("NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT", "1")
    assert client.get("/api/health").json()["notebook_storage"]["durable"] is True
    assert "notebook_to_api_notebook_storage_durable 1" in client.get("/api/metrics/prometheus").text


@pytest.fixture
def files(tmp_path):
    generate_dashboard_deployment(tmp_path / "out")
    return tmp_path / "out"


def test_the_image_stores_everything_under_data(files):
    text = (files / "Dockerfile").read_text()
    for variable in ("NOTEBOOK_API_UPLOAD_DIR=/data/uploads", "NOTEBOOK_API_GENERATED_DIR=/data/generated",
                     "NOTEBOOK_API_GENERATED_APPS_DIR=/data/generated_apps"):
        assert variable in text
    assert "mkdir -p /data/governance /data/uploads /data/generated /data/generated_apps" in text


def test_compose_mounts_the_volume_and_hardens_the_container(files):
    svc = yaml.safe_load((files / "docker-compose.yml").read_text())["services"]["dashboard"]
    assert svc["read_only"] is True and "/tmp" in svc["tmpfs"] and svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["environment"]["NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT"] == "1"
    assert svc["volumes"] == ["dashboard-data:/data"]


def test_kubernetes_has_probes_a_secure_context_resources_and_keeps_one_writer(files):
    docs = {d["kind"]: d for d in yaml.safe_load_all((files / "kubernetes.yaml").read_text())}
    deployment = docs["Deployment"]["spec"]
    pod = deployment["template"]["spec"]
    container = pod["containers"][0]
    assert deployment["replicas"] == 1 and deployment["strategy"]["type"] == "Recreate"
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"]["fsGroup"] == 1000
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert container[probe]["exec"]["command"] == ["python", "-m", "backend.dashboard_healthcheck"]
    assert container["resources"]["requests"]["memory"] and container["resources"]["limits"]["memory"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["NOTEBOOK_API_UPLOAD_DIR"] == "/data/uploads" and env["NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT"] == "1"
    mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert set(mounts) == {"/data", "/tmp"} and "persistentVolumeClaim" in volumes[mounts["/data"]]
    assert "emptyDir" in volumes[mounts["/tmp"]]
    assert "secretKeyRef" in json.dumps(container["env"]) and "change-me" not in (files / "kubernetes.yaml").read_text()
