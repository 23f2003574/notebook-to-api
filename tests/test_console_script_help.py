import importlib
import sys
import tomllib
from pathlib import Path

import pytest

PYPROJECT = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
)


def test_declared_console_script_runs_help_and_exits_zero(monkeypatch, capsys):
    """Invoke the callable `pip install .` wires up as `notebook-to-api`.

    test_pyproject_packaging only checks that the entry point resolves; this
    calls it the way the installed script does, with --help.
    """
    module, _, attr = PYPROJECT["project"]["scripts"]["notebook-to-api"].partition(":")
    entry = getattr(importlib.import_module(module), attr)
    monkeypatch.setattr(sys, "argv", ["notebook-to-api", "--help"])

    with pytest.raises(SystemExit) as exit_info:
        entry()

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "usage: notebook-to-api" in out
    assert "Compile Jupyter notebooks into FastAPI services" in out
