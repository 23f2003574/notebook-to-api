"""Build the wheel, install it into an empty venv, and drive the installed CLI.

Every other test imports `backend` from the checkout, so a file missing from
the wheel (the generator reads backend/runtime/*.py from disk and inlines it)
would only surface for real users. This runs the whole path from a clean
install, with the repository off the import path.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HEALTH_CHECK = """
import importlib, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
response = TestClient(importlib.import_module("sample_api.app").app).get("/health")
print(response.status_code, response.json())
"""


def _run(cmd, cwd, **kwargs):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900, **kwargs)
    assert result.returncode == 0, f"{cmd} failed:\n{result.stdout}\n{result.stderr}"
    return result


def test_wheel_installs_cleanly_and_compiles_a_healthy_app(tmp_path):
    wheelhouse = tmp_path / "wheels"
    venv = tmp_path / "venv"
    work = tmp_path / "work"
    work.mkdir()

    _run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(wheelhouse)], work)
    wheel = next(wheelhouse.glob("notebook_to_api-*.whl"))

    _run([sys.executable, "-m", "venv", str(venv)], work)
    bindir = venv / ("Scripts" if sys.platform == "win32" else "bin")
    python = bindir / ("python.exe" if sys.platform == "win32" else "python")
    _run([str(python), "-m", "pip", "install", "-q", str(wheel)], work)

    # Runtime sources the generator inlines must ship inside the wheel.
    from_install = _run([str(python), "-I", "-c", "import backend.runtime.isolated_execution as m; print(m.__file__)"], work)
    assert str(venv) in from_install.stdout

    out = work / "sample_api"
    cli = bindir / ("notebook-to-api.exe" if sys.platform == "win32" else "notebook-to-api")
    _run([str(cli), "compile", str(ROOT / "notebooks" / "sample.ipynb"), "--output", str(out)], work)
    assert (out / "app.py").exists()

    # The compiled app needs its own generated requirements (pandas etc.).
    _run([str(python), "-m", "pip", "install", "-q", "-r", str(out / "requirements.txt")], work)
    health = _run([str(python), "-W", "ignore", "-I", "-c", HEALTH_CHECK], work)
    assert "200" in health.stdout and "healthy" in health.stdout
