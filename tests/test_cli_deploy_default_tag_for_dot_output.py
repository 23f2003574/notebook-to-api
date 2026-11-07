import json
import subprocess
import sys
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parent.parent


def test_deploy_into_current_directory_tags_the_image_after_that_directory(tmp_path):
    project = tmp_path / "My_Service"
    project.mkdir()
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("def f(a: int) -> int:\n    return a\n")]
    nbformat.write(nb, str(project / "nb.ipynb"))

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", "nb.ipynb", "--output", ".", "--dry-run", "--json"],
        cwd=project, capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["tag"] == "my_service:latest"
