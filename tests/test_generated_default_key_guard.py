"""A generated app never serves the public default API key on a non-loopback address unless told to."""
import os
import subprocess
import sys

import nbformat
import pytest

from backend.compiler import compile_notebook


@pytest.fixture(scope="module")
def app_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("guard")
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("def f(a: int) -> int:\n    return a\n")])
    path = root / "nb.ipynb"
    nbformat.write(nb, path)
    compile_notebook(str(path), str(root / "svc"))
    return root


def _import_app(root, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_")}
    clean.update(env)
    code = "import svc.app; print('started')"
    return subprocess.run([sys.executable, "-c", code], cwd=root, env=clean, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("env", [
    {"NOTEBOOK_API_HOST": "0.0.0.0"},
    {"NOTEBOOK_API_HOST": "0.0.0.0", "NOTEBOOK_API_KEY": "notebook-to-api-dev-key"},
    {"NOTEBOOK_API_HOST": "192.168.1.5"},
])
def test_default_key_on_a_public_address_is_refused(app_dir, env):
    proc = _import_app(app_dir, **env)
    assert proc.returncode != 0 and "NOTEBOOK_API_ALLOW_DEFAULT_KEY" in proc.stderr


def test_explicit_opt_in_starts(app_dir):
    proc = _import_app(app_dir, NOTEBOOK_API_HOST="0.0.0.0", NOTEBOOK_API_ALLOW_DEFAULT_KEY="true")
    assert proc.returncode == 0 and "started" in proc.stdout


def test_a_custom_key_starts_on_a_public_address(app_dir):
    proc = _import_app(app_dir, NOTEBOOK_API_HOST="0.0.0.0", NOTEBOOK_API_KEY="a-real-secret-key-0123456789")
    assert proc.returncode == 0


@pytest.mark.parametrize("host", [None, "127.0.0.1", "localhost", "::1"])
def test_loopback_development_keeps_working(app_dir, host):
    env = {"NOTEBOOK_API_HOST": host} if host else {}
    proc = _import_app(app_dir, **env)
    assert proc.returncode == 0 and "started" in proc.stdout


def test_the_image_sets_the_host_and_documents_the_opt_in(app_dir):
    dockerfile = (app_dir / "svc" / "Dockerfile").read_text()
    assert "ENV NOTEBOOK_API_HOST=0.0.0.0" in dockerfile and "${NOTEBOOK_API_HOST:-0.0.0.0}" in dockerfile
    readme = (app_dir / "svc" / "README.md").read_text()
    assert "NOTEBOOK_API_ALLOW_DEFAULT_KEY" in readme
