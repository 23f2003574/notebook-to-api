"""Generated Compose and Kubernetes deployments configure the existing task store.

Structural checks only: Docker and a cluster are not available here, so volume
mounting, ownership of a fresh named volume and Pod replacement were not exercised.
"""
import re

import nbformat
import pytest
import yaml

from backend.compiler import compile_notebook

STORE = "/data/tasks.json"


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("persist")
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("# notebook-to-api: background\ndef work(x: int) -> int:\n    return x\n")]
    nbformat.write(nb, str(tmp / "nb.ipynb"))
    compile_notebook(str(tmp / "nb.ipynb"), str(tmp / "app"))
    return tmp / "app"


def _text(out, name):
    return (out / name).read_text(encoding="utf-8")


def test_compose_sets_the_task_store_and_mounts_a_named_volume(out):
    text = _text(out, "docker-compose.yml")
    compose = yaml.safe_load(text)
    service = next(iter(compose["services"].values()))
    assert f"NOTEBOOK_API_TASK_STORE_PATH=${{NOTEBOOK_API_TASK_STORE_PATH:-{STORE}}}" in service["environment"]
    assert service["volumes"] == ["task-data:/data"]  # a named volume, not a host bind mount
    assert "task-data" in compose["volumes"]
    # The store file lives inside the mounted directory.
    assert STORE.startswith("/data/")


def test_compose_keeps_its_security_settings(out):
    compose = yaml.safe_load(_text(out, "docker-compose.yml"))
    service = next(iter(compose["services"].values()))
    assert any(e.startswith("NOTEBOOK_API_KEY=${NOTEBOOK_API_KEY:?") for e in service["environment"])
    assert service["cap_drop"] == ["ALL"] and service["security_opt"] == ["no-new-privileges:true"]
    assert service["ports"][0].startswith("${NOTEBOOK_API_BIND_ADDRESS:-127.0.0.1}")


def test_kubernetes_sets_the_task_store_and_mounts_an_emptydir(out):
    text = _text(out, "kubernetes.yaml")
    pod = next(d for d in yaml.safe_load_all(text) if d["kind"] == "Deployment")["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert {"name": "NOTEBOOK_API_TASK_STORE_PATH", "value": STORE} in container["env"]
    assert container["volumeMounts"] == [{"name": "task-data", "mountPath": "/data"}]
    assert pod["volumes"] == [{"name": "task-data", "emptyDir": {}}]
    # Honest about durability: emptyDir does not survive Pod replacement.
    assert "not Pod replacement" in text and "PersistentVolumeClaim" in text
    # Existing hardening and secret handling are intact.
    assert container["securityContext"]["runAsUser"] == 1000 and container["securityContext"]["runAsNonRoot"] is True
    assert "secretKeyRef" in text and "NOTEBOOK_API_KEY" in text


def test_image_creates_the_data_directory_owned_by_the_non_root_user(out):
    lines = [l.strip() for l in _text(out, "Dockerfile").splitlines()]
    run = " ".join(lines[next(i for i, l in enumerate(lines) if l.startswith("RUN useradd")):][:3])
    assert "mkdir -p /data" in run and re.search(r"chown -R appuser:appuser /app /data", run)
    # Ownership is set before dropping privileges, so a fresh named volume copies it.
    assert lines.index("USER appuser") > next(i for i, l in enumerate(lines) if "chown -R" in l or "/data" in l)


def test_the_configured_store_path_round_trips_through_the_runtime(out, tmp_path, monkeypatch):
    """The path style used by the deployments works with the real persistence code."""
    import importlib
    import sys

    store = tmp_path / "data" / "tasks.json"
    store.parent.mkdir()
    monkeypatch.setenv("NOTEBOOK_API_TASK_STORE_PATH", str(store))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "k")
    monkeypatch.syspath_prepend(str(out.parent))
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        monkeypatch.delitem(sys.modules, name)
    module = importlib.import_module("app.app")
    module.TASKS["t1"] = {"status": "completed", "result": 1, "created_at": 1.0}
    assert '"t1"' in store.read_text(encoding="utf-8")
