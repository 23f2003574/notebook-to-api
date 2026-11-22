"""Generator output is bounded while it is consumed (NOTEBOOK_API_MAX_RESULT_ITEMS / _MAX_RESPONSE_BYTES)."""
import importlib
import sys

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

SOURCE = '''
progress = {"n": 0}

def count(n: int):
    for i in range(n):
        yield i

def endless():
    while True:
        progress["n"] += 1
        yield progress["n"]

def produced() -> int:
    return progress["n"]

async def aendless():
    i = 0
    while True:
        i += 1
        yield i

def blob(n: int) -> str:
    return "x" * n

def fat():
    for _ in range(1000):
        yield "y" * 100
'''
H = {"X-API-Key": "limit-key"}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "limit_app"))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "limit-key")

    def make(**env):
        for key in ("NOTEBOOK_API_MAX_RESULT_ITEMS", "NOTEBOOK_API_MAX_RESPONSE_BYTES"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        for name in [k for k in sys.modules if k.startswith("limit_app")]:
            del sys.modules[name]
        module = importlib.import_module("limit_app.app")
        return module, TestClient(module.app, raise_server_exceptions=False)

    return make


def test_finite_generator_still_works_with_defaults(make_client):
    _, c = make_client()
    r = c.post("/count", json={"n": 5}, headers=H)
    assert r.status_code == 200 and r.json() == {"result": [0, 1, 2, 3, 4]}


def test_item_limit_stops_an_endless_generator_while_it_runs(make_client):
    module, c = make_client(NOTEBOOK_API_MAX_RESULT_ITEMS=50)
    r = c.post("/endless", json={}, headers=H)
    assert r.status_code == 500 and "NOTEBOOK_API_MAX_RESULT_ITEMS=50" in r.json()["detail"]
    # consumed incrementally: the generator was cut off at the limit, not drained
    assert module.notebook_module.progress["n"] <= 51  # the item past the limit is produced, then refused


def test_async_generator_is_bounded(make_client):
    _, c = make_client(NOTEBOOK_API_MAX_RESULT_ITEMS=10)
    r = c.post("/aendless", json={}, headers=H)
    assert r.status_code == 500 and "NOTEBOOK_API_MAX_RESULT_ITEMS=10" in r.json()["detail"]


def test_item_limit_exact_boundary_passes(make_client):
    _, c = make_client(NOTEBOOK_API_MAX_RESULT_ITEMS=5)
    assert c.post("/count", json={"n": 5}, headers=H).status_code == 200
    assert c.post("/count", json={"n": 6}, headers=H).status_code == 500


def test_byte_limit_is_independent_of_item_limit(make_client):
    _, c = make_client(NOTEBOOK_API_MAX_RESULT_ITEMS=0, NOTEBOOK_API_MAX_RESPONSE_BYTES=2000)
    r = c.post("/fat", json={}, headers=H)  # 1000 items, ~100 KB: items are unlimited, bytes are not
    assert r.status_code == 500 and "NOTEBOOK_API_MAX_RESPONSE_BYTES=2000" in r.json()["detail"]


def test_non_generator_response_over_byte_limit_is_refused(make_client):
    _, c = make_client(NOTEBOOK_API_MAX_RESPONSE_BYTES=1000)
    assert c.post("/blob", json={"n": 900}, headers=H).status_code == 200
    r = c.post("/blob", json={"n": 5000}, headers=H)
    assert r.status_code == 500 and "NOTEBOOK_API_MAX_RESPONSE_BYTES=1000" in r.json()["detail"]


def test_zero_disables_limits(make_client):
    _, c = make_client(NOTEBOOK_API_MAX_RESULT_ITEMS=0, NOTEBOOK_API_MAX_RESPONSE_BYTES=0)
    assert c.post("/count", json={"n": 3000}, headers=H).status_code == 200
    assert c.post("/blob", json={"n": 20000}, headers=H).status_code == 200


def test_limits_are_in_generated_deployment_files(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("def f(a: int) -> int:\n    return a\n")]
    nbformat.write(nb, str(tmp_path / "n.ipynb"))
    compile_notebook(str(tmp_path / "n.ipynb"), str(tmp_path / "out"))
    for name in ("docker-compose.yml", "kubernetes.yaml", ".env.example"):
        text = (tmp_path / "out" / name).read_text()
        assert "NOTEBOOK_API_MAX_RESULT_ITEMS" in text and "NOTEBOOK_API_MAX_RESPONSE_BYTES" in text, name
