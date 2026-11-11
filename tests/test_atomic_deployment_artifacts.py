import os

import nbformat
import pytest

from backend import compiler
from backend.compiler import compile_notebook_to_api

DEPLOY = ("Dockerfile", ".dockerignore", "docker-compose.yml", ".env.example", "kubernetes.yaml", "README.md")


def _nb(tmp_path, body="def add(a: int, b: int) -> int:\n    \"add\"\n    return a + b\n"):
    path = tmp_path / "nb.ipynb"
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(body)]), path)
    return str(path)


def _snapshot(out):
    return {n: (out / n).read_bytes() for n in DEPLOY}


def test_success_publishes_all_artifacts_and_leaves_no_staging(tmp_path):
    out = tmp_path / "out"
    compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    assert all((out / n).is_file() for n in DEPLOY)
    assert not [n for n in os.listdir(out) if n.startswith(compiler._DEPLOY_STAGING_PREFIX)]


def test_late_generation_failure_keeps_previous_deployment_files(tmp_path, monkeypatch):
    out = tmp_path / "out"
    nb = _nb(tmp_path)
    compile_notebook_to_api(nb, str(out / "app.py"))
    before = _snapshot(out)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(compiler, "generate_readme", boom)
    with pytest.raises(OSError):
        compile_notebook_to_api(_nb(tmp_path, "def mul(a: int, b: int) -> int:\n    \"m\"\n    return a * b\n"),
                                str(out / "app.py"))
    assert _snapshot(out) == before
    assert not [n for n in os.listdir(out) if n.startswith(compiler._DEPLOY_STAGING_PREFIX)]


def test_invalid_staged_manifest_is_not_published(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    out = tmp_path / "out"
    compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    before = _snapshot(out)
    monkeypatch.setattr(compiler, "generate_kubernetes_manifest",
                        lambda path, *a, **k: open(path, "w").write("kind: Deployment\n"))
    with pytest.raises(ValueError, match="apiVersion"):
        compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    assert _snapshot(out) == before
