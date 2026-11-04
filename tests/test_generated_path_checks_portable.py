"""Regression: os.path.exists/isfile/getsize on a shipped data file must
resolve against the generated project, not the app's launch directory --
open() on the same path already did."""
import importlib
import os
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
nbformat = pytest.importorskip("nbformat")
from fastapi.testclient import TestClient  # noqa: E402

from backend.compiler import compile_notebook_to_api  # noqa: E402

SOURCE = """
import os
def has_data():
    return {"exists": os.path.exists("data.csv"), "isfile": os.path.isfile("data.csv"),
            "size": os.path.getsize("data.csv")}
def has_missing():
    return {"exists": os.path.exists("data.csv") and os.path.exists("nope.csv")}
"""


def test_path_checks_work_from_a_different_launch_directory(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "data.csv").write_text("a,b\n1,2\n")
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SOURCE)])
    nbformat.write(nb, str(src / "nb.ipynb"))
    monkeypatch.chdir(src)
    compile_notebook_to_api(str(src / "nb.ipynb"), str(tmp_path / "gen_app" / "app.py"))

    # The original notebook directory is gone and the app launches elsewhere.
    (src / "data.csv").unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = importlib.import_module("gen_app.app")
    try:
        client = TestClient(mod.app, headers={"X-API-Key": mod.DEFAULT_API_KEY})
        r = client.post("/has_data", json={})
        assert r.status_code == 200, r.text
        assert r.json()["result"] == {"exists": True, "isfile": True, "size": 8}
        assert client.post("/has_missing", json={}).json()["result"] == {"exists": False}
    finally:
        sys.modules.pop("gen_app.app", None)
