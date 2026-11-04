"""Regression: two notebooks compiled into same-named output directories
(the default `generated/`) and loaded in one process must each serve their
own functions -- the second app used to silently serve the first's."""
import importlib.util
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
nbformat = pytest.importorskip("nbformat")
from fastapi.testclient import TestClient  # noqa: E402

from backend.compiler import compile_notebook_to_api  # noqa: E402

SOURCES = {
    "a": "def who():\n    return {'app': 'a'}",
    "b": "def who():\n    return {'app': 'b'}\ndef only_b():\n    return {'b': 1}",
}


def _compile(root, name, monkeypatch):
    folder = root / name
    folder.mkdir()
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SOURCES[name])])
    nbformat.write(nb, str(folder / "nb.ipynb"))
    monkeypatch.chdir(folder)
    compile_notebook_to_api(str(folder / "nb.ipynb"), str(folder / "generated" / "app.py"))


def _load(root, name, monkeypatch):
    monkeypatch.syspath_prepend(str(root / name))
    spec = importlib.util.spec_from_file_location(
        f"iso_app_{name}", root / name / "generated" / "app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return TestClient(mod.app, headers={"X-API-Key": mod.DEFAULT_API_KEY})


def test_two_same_named_apps_in_one_process_stay_independent(tmp_path, monkeypatch):
    for key in [k for k in sys.modules if k == "generated" or k.startswith("generated.")]:
        monkeypatch.delitem(sys.modules, key)
    _compile(tmp_path, "a", monkeypatch)
    _compile(tmp_path, "b", monkeypatch)
    client_a = _load(tmp_path, "a", monkeypatch)
    client_b = _load(tmp_path, "b", monkeypatch)
    try:
        assert client_a.post("/who", json={}).json() == {"result": {"app": "a"}}
        assert client_a.post("/only_b", json={}).status_code == 404
        assert client_b.post("/who", json={}).json() == {"result": {"app": "b"}}
        assert client_b.post("/only_b", json={}).json() == {"result": {"b": 1}}
    finally:
        for key in [k for k in sys.modules if k == "generated" or k.startswith("generated.")]:
            sys.modules.pop(key, None)
