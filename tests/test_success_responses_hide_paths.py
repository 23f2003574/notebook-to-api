"""Successful responses name files relative to their root, never by absolute server path."""
import json
import re

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)
ABSOLUTE = re.compile(r'"(/(?:tmp|home|var|private|root|usr|opt|mnt)[^"]{2,}|[A-Za-z]:\\\\[^"]+)"')


def _nb():
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(x: int) -> int:\n    return x\n"))
    return nbformat.writes(nb).encode()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _clean(response, tmp_path):
    assert response.status_code == 200, response.text
    assert not ABSOLUTE.search(response.text), response.text
    assert str(tmp_path) not in response.text
    return response.json()


def test_upload_and_batch_upload_report_the_filename(dirs):
    body = _clean(client.post("/api/upload", files={"file": ("a.ipynb", _nb(), "application/json")}), dirs)
    assert body["filename"] == "a.ipynb" and body["path"] == "a.ipynb"
    batch = client.post("/api/upload/batch", files=[("files", ("b.ipynb", _nb(), "application/json"))])
    _clean(batch, dirs)


def test_exports_report_paths_relative_to_the_app_and_they_can_be_downloaded(dirs):
    client.post("/api/upload", files={"file": ("a.ipynb", _nb(), "application/json")})
    assert client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True}).status_code == 200
    openapi = _clean(client.post("/api/export-openapi", json={"notebook": "a.ipynb"}), dirs)
    assert openapi["path"] == "openapi.json"
    sdk = _clean(client.post("/api/export-sdk", json={"notebook": "a.ipynb", "language": "python"}), dirs)
    assert sdk["path"] == "sdk/python_client.py"
    package = _clean(client.post("/api/export-sdk", json={"notebook": "a.ipynb", "language": "typescript", "package": True}), dirs)
    assert package["path"] == "sdk/typescript-package" and "package.json" in package["files"]
    # the reported names are real: they can be fetched back through the generated-file route
    for name in (openapi["path"], sdk["path"], package["path"] + "/package.json"):
        fetched = client.get(f"/api/generated/{name}", params={"notebook": "a.ipynb"})
        assert fetched.status_code == 200, name


def test_other_success_responses_stay_free_of_server_paths(dirs):
    client.post("/api/upload", files={"file": ("a.ipynb", _nb(), "application/json")})
    client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True})
    for method, path, kw in (
        ("POST", "/api/compile", {"json": {"notebook_path": "a.ipynb"}}), ("POST", "/api/inspect", {"json": {"notebook_path": "a.ipynb"}}),
        ("GET", "/api/notebooks", {}), ("GET", "/api/apps", {}), ("GET", "/api/generated", {}), ("GET", "/api/health", {}),
        ("GET", "/api/compile/history", {}), ("GET", "/api/deploy/history", {}),
    ):
        _clean(client.request(method, path, **kw), dirs)


def test_the_cli_still_reports_where_the_artifact_went(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(x: int) -> int:\n    return x\n"))
    nbformat.write(nb, tmp_path / "n.ipynb")
    env = {**os.environ, "PYTHONPATH": str(root)}
    for args in (["compile", str(tmp_path / "n.ipynb"), "--output", str(tmp_path / "cliapp")],):
        assert subprocess.run([sys.executable, "-m", "backend.cli", *args], capture_output=True, text=True, env=env, cwd=root).returncode == 0
    done = subprocess.run([sys.executable, "-m", "backend.cli", "export-openapi", "--app-dir", str(tmp_path / "cliapp"), "--json"],
                          capture_output=True, text=True, env=env, cwd=root)
    assert done.returncode == 0 and "openapi.json" in done.stdout
    assert json.loads(done.stdout[done.stdout.index("{"):])["path"].endswith("openapi.json")
