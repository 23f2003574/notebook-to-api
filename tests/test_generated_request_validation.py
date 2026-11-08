import subprocess
import sys
from pathlib import Path

import nbformat

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_generated_endpoint_validates_missing_required_and_applies_defaults(tmp_path):
    """A missing required argument is a 422 naming the field (not a 500 and no
    traceback); an omitted optional argument takes the function's own default."""

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def scale(x: int, factor: int = 3) -> int:\n    return x * factor\n"
        )
    )
    notebook_path = tmp_path / "nb.ipynb"
    nbformat.write(notebook, str(notebook_path))
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

missing = client.post("/scale", json={{"factor": 2}}, headers=headers)
assert missing.status_code == 422, missing.text
error = missing.json()["detail"][0]
assert error["type"] == "missing" and error["loc"] == ["body", "x"], error
assert "Traceback" not in missing.text

default = client.post("/scale", json={{"x": 4}}, headers=headers)
assert default.status_code == 200 and default.json() == {{"result": 12}}, default.text

override = client.post("/scale", json={{"x": 4, "factor": 2}}, headers=headers)
assert override.json() == {{"result": 8}}, override.text

print("VALIDATION_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VALIDATION_OK" in proc.stdout
