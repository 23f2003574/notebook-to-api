"""Regression: a JSON true/false sent for an int/float parameter used to be
coerced to 1/0 by Pydantic's lax mode, running the notebook function with a
value the caller never meant. Valid numbers and defaults must still work."""
import importlib.util

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


SOURCE = """
from typing import Optional
def scale(n: int, ratio: float = 1.5, limit: Optional[int] = None, label: str = "x"):
    return {"n": n, "ratio": ratio, "limit": limit, "label": label}
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    nbformat = pytest.importorskip("nbformat")
    from backend.compiler import compile_notebook_to_api

    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SOURCE)])
    nb_path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(nb_path))
    out = tmp_path / "out" / "app.py"
    compile_notebook_to_api(str(nb_path), str(out))
    monkeypatch.syspath_prepend(str(out.parent))
    spec = importlib.util.spec_from_file_location("nb_bool_app", out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return TestClient(mod.app, headers={"X-API-Key": mod.DEFAULT_API_KEY})


@pytest.mark.parametrize("field", ["n", "ratio", "limit"])
@pytest.mark.parametrize("value", [True, False])
def test_boolean_for_numeric_parameter_is_rejected(client, field, value):
    body = {"n": 1, field: value}
    r = client.post("/scale", json=body)
    assert r.status_code == 422, r.text
    assert "not a boolean" in r.text


def test_valid_numbers_and_none_still_accepted(client):
    r = client.post("/scale", json={"n": 2, "ratio": 3, "limit": None})
    assert r.status_code != 422, r.text
    r = client.post("/scale", json={"n": 0})
    assert r.status_code != 422, r.text


def test_boolean_for_str_parameter_unchanged(client):
    assert client.post("/scale", json={"n": 1, "label": True}).status_code == 422
