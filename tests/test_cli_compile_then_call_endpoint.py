import os
import subprocess
import sys
from pathlib import Path

import nbformat

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_cli_compiled_app_serves_the_notebook_function(tmp_path):
    """The whole workflow a user follows: `compile` via the real CLI, then
    call the generated app. CLI tests check the artifacts and compiler tests
    call apps built with compile_notebook; nothing tied the two together."""

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n")
    )
    notebook_path = tmp_path / "nb.ipynb"
    nbformat.write(notebook, str(notebook_path))
    out = tmp_path / "generated"

    env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
    compiled = subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(notebook_path), "--output", str(out)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert "POST /add" in compiled.stdout
    for name in ("app.py", "requirements.txt", "Dockerfile", "runtime/notebook_module.py"):
        assert (out / name).is_file(), name

    script = f"""
import sys
sys.path.insert(0, {str(out)!r})
from fastapi.testclient import TestClient
import app

client = TestClient(app.app)
response = client.post("/add", json={{"a": 2, "b": 3}}, headers={{"X-API-Key": "notebook-to-api-dev-key"}})
assert response.status_code == 200 and response.json() == {{"result": 5}}, response.text
print("E2E_OK")
"""
    called = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(out), env=env, capture_output=True, text=True, timeout=60,
    )
    assert called.returncode == 0, called.stdout + called.stderr
    assert "E2E_OK" in called.stdout
