"""The generated Dockerfile persists background tasks by default (plain `docker run` included)."""
import importlib.util
import os
import re
import sys

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook
from backend.generator.docker_generator import TASK_STORE_FILE, TASK_STORE_VAR


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("persist")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def train_model(n: int = 1):\n    return {'n': n}\n"))
    nbformat.write(nb, tmp / "nb.ipynb")
    compile_notebook(str(tmp / "nb.ipynb"), str(tmp / "persistapp"))
    return tmp


def _lines(tmp):
    return (tmp / "persistapp" / "Dockerfile").read_text().splitlines()


def test_dockerfile_sets_the_task_store_and_declares_the_volume_after_ownership_is_fixed(built):
    lines = _lines(built)
    env = lines.index(f"ENV {TASK_STORE_VAR}={TASK_STORE_FILE}")
    volume = lines.index("VOLUME /data")
    chown = next(i for i, line in enumerate(lines) if "chown -R appuser:appuser /app /data" in line)
    user = lines.index("USER appuser")
    assert chown < volume < user and chown < env < user  # a volume declared earlier would stay root-owned


def test_the_store_is_inside_the_volume_and_non_root_can_write_it(built):
    assert TASK_STORE_FILE.startswith("/data/")
    text = "\n".join(_lines(built))
    assert "useradd --create-home --uid 1000 appuser" in text and "mkdir -p /data" in text


def test_compose_and_kubernetes_keep_their_own_explicit_configuration(built):
    compose = (built / "persistapp" / "docker-compose.yml").read_text()
    assert f"{TASK_STORE_VAR}=${{{TASK_STORE_VAR}:-{TASK_STORE_FILE}}}" in compose and "task-data:/data" in compose
    manifest = (built / "persistapp" / "kubernetes.yaml").read_text()
    assert TASK_STORE_FILE in manifest and "mountPath: /data" in manifest


def test_the_app_persists_to_the_configured_path_and_override_disables_it(built, monkeypatch, tmp_path):
    store = tmp_path / "data" / "tasks.json"
    store.parent.mkdir()
    monkeypatch.setenv("NOTEBOOK_API_KEY", "k")
    monkeypatch.setenv(TASK_STORE_VAR, str(store))
    sys.path.insert(0, str(built))
    try:
        spec = importlib.util.spec_from_file_location("persistapp_app", built / "persistapp" / "app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["persistapp_app"] = module
        spec.loader.exec_module(module)
        client = TestClient(module.app)
        task_id = client.post("/train_model", json={}, headers={"X-API-Key": "k"}).json()["task_id"]
        assert store.is_file() and task_id in store.read_text()
        assert module.TASK_STORE_PATH == str(store)
        monkeypatch.setenv(TASK_STORE_VAR, "")
        spec.loader.exec_module(module)
        assert module.TASK_STORE_PATH == ""
    finally:
        sys.path.remove(str(built))
        sys.modules.pop("persistapp_app", None)


def test_dockerfile_still_defaults_one_port_and_user(built):
    text = "\n".join(_lines(built))
    assert len(re.findall(r"^USER ", text, re.M)) == 1 and "EXPOSE 8000" in text
    assert os.path.getsize(built / "persistapp" / "Dockerfile") > 0
