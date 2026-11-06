"""End to end: a data file the notebook reads *inside an endpoint* at request
time must be shipped with the compiled app and found when the app runs from a
different working directory. The existing coverage execs the module at import
time; nothing drove a real HTTP request through the compiled app."""

import subprocess
import sys
from pathlib import Path

import nbformat

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_endpoint_reads_shipped_data_file_at_request_time_from_another_cwd(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "prices.csv").write_text("item,cost\na,2\nb,5\n")
    notebook = nbformat.v4.new_notebook()
    notebook.cells = [
        nbformat.v4.new_code_cell(
            "import csv\n\n"
            "def total_cost(item: str) -> int:\n"
            '    """Sum the cost column for one item."""\n'
            "    with open('prices.csv') as f:\n"
            "        return sum(int(r['cost']) for r in csv.DictReader(f) if r['item'] == item)\n"
        )
    ]
    notebook_path = src / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})
from backend.compiler import compile_notebook
compile_notebook({str(notebook_path)!r}, "generated")
from pathlib import Path
assert Path("generated/runtime/prices.csv").exists()
from generated.app import app
from fastapi.testclient import TestClient
r = TestClient(app).post(
    "/total_cost", json={{"item": "b"}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert r.status_code == 200 and r.json() == {{"result": 5}}, r.text
print("REQUEST_TIME_DATA_FILE_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "REQUEST_TIME_DATA_FILE_OK" in proc.stdout
