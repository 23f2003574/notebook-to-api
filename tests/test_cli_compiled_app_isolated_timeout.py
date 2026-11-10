import os
import subprocess
import sys
from pathlib import Path

import nbformat

PROJECT_ROOT = Path(__file__).resolve().parent.parent

NOTEBOOK_SOURCE = '''def add(a: int, b: int) -> int:
    return a + b


def spin(pid_path: str) -> int:
    import os
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))
    while True:
        pass
'''

CLIENT_SCRIPT = """
import os, sys, time
sys.path.insert(0, {out!r})
from fastapi.testclient import TestClient
import app

client = TestClient(app.app)
h = {{"X-API-Key": "notebook-to-api-dev-key"}}

assert client.post("/add", json={{"a": 2, "b": 3}}, headers=h).json() == {{"result": 5}}

r = client.post("/spin", json={{"pid_path": {pid_path!r}}}, headers=h)
assert r.status_code == 504, (r.status_code, r.text)

pid = int(open({pid_path!r}).read())
assert pid != os.getpid()
deadline = time.monotonic() + 10
while True:  # the child must really be gone (killed and reaped), not just detached
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        break
    assert time.monotonic() < deadline, "isolated child still alive after 504"
    time.sleep(0.05)

assert client.post("/add", json={{"a": 4, "b": 5}}, headers=h).json() == {{"result": 9}}
print("ISOLATED_E2E_OK")
"""


def test_compiled_app_times_out_non_terminating_function_and_keeps_serving(tmp_path):
    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(nbformat.v4.new_code_cell(NOTEBOOK_SOURCE))
    notebook_path = tmp_path / "nb.ipynb"
    nbformat.write(notebook, str(notebook_path))
    out = tmp_path / "generated"

    compiled = subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(notebook_path), "--output", str(out)],
        cwd=str(tmp_path), env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT)),
        capture_output=True, text=True, timeout=120,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    env = dict(
        os.environ,
        NOTEBOOK_API_ISOLATE_EXECUTION="1",
        NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS="1",
    )
    script = CLIENT_SCRIPT.format(out=str(out), pid_path=str(tmp_path / "child.pid"))
    called = subprocess.run(
        [sys.executable, "-c", script], cwd=str(out), env=env,
        capture_output=True, text=True, timeout=90,
    )
    assert called.returncode == 0, called.stdout + called.stderr
    assert "ISOLATED_E2E_OK" in called.stdout
