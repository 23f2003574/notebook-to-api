"""Compile output names the promoted files, never the deleted staging directory."""
import os
import re
import subprocess
import sys
from unittest import mock

import nbformat
import pytest

from backend import compiler

ARTIFACTS = ["Dockerfile", ".dockerignore", "docker-compose.yml", ".env.example", "kubernetes.yaml", "README.md"]


def _nb(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(x: int) -> int:\n    return x\n"))
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, path)
    return str(path)


def _reported(stdout):
    return re.findall(r"^(\S+) generated at: (.+)$", stdout, re.M)


def test_every_reported_path_exists_and_is_never_staged(tmp_path):
    out = tmp_path / "app"
    run = subprocess.run([sys.executable, "-m", "backend.cli", "compile", _nb(tmp_path), "--output", str(out)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    reported = _reported(run.stdout)
    assert {name for name, _ in reported} == set(ARTIFACTS)
    for name, path in reported:
        assert os.path.isfile(path), path
        assert compiler._DEPLOY_STAGING_PREFIX not in path
        assert os.path.dirname(path) == str(out)


def test_failed_publish_claims_nothing_was_generated(tmp_path, capsys):
    out = tmp_path / "app"
    with mock.patch.object(compiler, "_publish_staged_artifacts", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            compiler.compile_notebook(_nb(tmp_path), str(out))
    printed = capsys.readouterr().out
    assert "generated at" not in printed
    assert "Successfully generated" not in printed
    assert not out.exists() or not any(n.startswith(compiler._DEPLOY_STAGING_PREFIX) for n in os.listdir(out))
