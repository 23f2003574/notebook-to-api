"""Module-level dunder functions (`__init__`, `__repr__`) are protocol hooks,
not API: they must not be compiled into `POST /__init__` style endpoints."""
import importlib
import sys

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook


def test_dunder_functions_are_skipped_but_regular_ones_are_served(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        "def __init__(a: int = 1) -> int:\n    '''d'''\n    return a\n"
        "def __repr__(a: int = 1) -> int:\n    '''d'''\n    return a\n"
        "def ok(a: int = 1) -> int:\n    '''d'''\n    return a\n"
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    compile_notebook(str(path), str(tmp_path / "gen_dunder_app"))
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("gen_dunder_app.app")
        paths = mod.app.openapi()["paths"]
        assert "/ok" in paths
        assert "/__init__" not in paths and "/__repr__" not in paths
        with TestClient(mod.app) as client:
            r = client.post(
                "/ok", json={"a": 4},
                headers={"X-API-Key": "notebook-to-api-dev-key"},
            )
        assert r.json() == {"result": 4}
    finally:
        sys.path.remove(str(tmp_path))
        for k in [k for k in sys.modules if k.startswith("gen_dunder_app")]:
            del sys.modules[k]
