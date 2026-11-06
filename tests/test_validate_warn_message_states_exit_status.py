import subprocess
import sys
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parent.parent


def test_validate_warn_message_matches_the_exit_status_it_returns(tmp_path):
    """`validate` printed "would still compile cleanly" next to exit status 1,
    so a CI log showed a failing job with a reassuring message. The message
    must state the status the command actually returns."""
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("x = 1\n")]  # no endpoints -> warn
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "validate", str(path)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 1
    assert "would still compile cleanly (exit status 1; 2 means it would not)" in proc.stdout
