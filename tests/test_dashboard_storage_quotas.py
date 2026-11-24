"""Container deployments bound dashboard storage by default; uploads are refused with 507 on a full disk."""
import collections
import shutil
from pathlib import Path

import nbformat
import pytest
import yaml
from fastapi.testclient import TestClient

from backend import dashboard
from backend.generator.dashboard_deployment import generate_dashboard_deployment
from backend.routes import upload

NB = nbformat.writes(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("def f():\n    return 1\n")])).encode()
Usage = collections.namedtuple("usage", "total used free")


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS", "NOTEBOOK_API_DASHBOARD_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(upload, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(upload, "GENERATED_APPS_DIR", str(tmp_path / "apps"))
    (tmp_path / "up").mkdir()
    return TestClient(dashboard.app)


def _up(client, name):
    return client.post("/api/upload", files={"file": (name, NB)})


def test_notebook_count_quota(client, monkeypatch):
    monkeypatch.setattr(upload, "MAX_NOTEBOOKS", 1)
    assert _up(client, "a.ipynb").status_code == 200
    assert _up(client, "b.ipynb").status_code == 400


def test_total_storage_quota(client, monkeypatch):
    monkeypatch.setattr(upload, "MAX_TOTAL_STORAGE_BYTES", len(NB) + 10)
    assert _up(client, "a.ipynb").status_code == 200
    assert _up(client, "b.ipynb").status_code in (400, 413)


def test_low_disk_guard_returns_507_for_single_and_batch_uploads(client, monkeypatch, tmp_path):
    monkeypatch.setattr(upload, "MIN_FREE_DISK_BYTES", 1000)
    monkeypatch.setattr(upload.shutil, "disk_usage", lambda p: Usage(10_000, 9_500, 500))
    r = _up(client, "a.ipynb")
    assert r.status_code == 507 and "NOTEBOOK_API_MIN_FREE_DISK_BYTES" in r.json()["detail"]
    batch = client.post("/api/upload/batch", files=[("files", ("b.ipynb", NB))])
    assert batch.status_code in (200, 207) and batch.json()["failed_count"] == 1
    assert not list((tmp_path / "up").glob("*.ipynb"))
    monkeypatch.setattr(upload.shutil, "disk_usage", lambda p: Usage(10_000, 8_000, 2_000))
    assert _up(client, "a.ipynb").status_code == 200


def test_guard_is_off_by_default_for_local_runs(client, monkeypatch):
    assert upload.MIN_FREE_DISK_BYTES == 0
    monkeypatch.setattr(upload.shutil, "disk_usage", lambda p: Usage(10, 10, 0))
    assert _up(client, "a.ipynb").status_code == 200


def test_usage_and_limits_are_reported(client, monkeypatch):
    monkeypatch.setattr(upload, "MIN_FREE_DISK_BYTES", 5)
    _up(client, "a.ipynb")
    storage = client.get("/api/notebooks/storage").json()
    assert storage["notebook_count"] == 1 and "max_total_storage_bytes" in storage and "notebooks_remaining" in storage
    config = client.get("/api/config").json()
    assert config["min_free_disk_bytes"] == 5 and "max_notebooks" in config


def test_container_templates_bound_storage_by_default_and_allow_overrides(tmp_path):
    out = Path(__file__).resolve().parent.parent / "dashboard-deployment-quota-test"
    shutil.rmtree(out, ignore_errors=True)
    try:
        generate_dashboard_deployment(out)
        docker = (out / "Dockerfile").read_text()
        for name in ("NOTEBOOK_API_MAX_NOTEBOOKS=1000", "NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES=5368709120",
                     "NOTEBOOK_API_MIN_FREE_DISK_BYTES=536870912"):
            assert name in docker
        compose = yaml.safe_load((out / "docker-compose.yml").read_text())
        env = next(iter(compose["services"].values()))["environment"]
        assert env["NOTEBOOK_API_MAX_NOTEBOOKS"] == "${NOTEBOOK_API_MAX_NOTEBOOKS:-1000}"
        assert env["NOTEBOOK_API_MIN_FREE_DISK_BYTES"] == "${NOTEBOOK_API_MIN_FREE_DISK_BYTES:-536870912}"
        k8s = [d for d in yaml.safe_load_all((out / "kubernetes.yaml").read_text()) if d and d.get("kind") == "Deployment"][0]
        names = {e["name"]: e.get("value") for e in k8s["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert names["NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES"] == "5368709120"
    finally:
        shutil.rmtree(out, ignore_errors=True)
