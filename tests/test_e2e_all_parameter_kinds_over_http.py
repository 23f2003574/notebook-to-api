"""End to end: a notebook function mixing positional-only, defaulted, *args,
keyword-only and **kwargs parameters must compile into an endpoint whose real
HTTP responses match calling the function directly. Generator unit tests check
the emitted source; nothing drove requests through the compiled app."""

import importlib

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

SOURCE = (
    "def mix(a: int, /, b: int = 2, *rest: int, scale: int = 1, **extra) -> dict:\n"
    '    """Combine every parameter kind."""\n'
    '    return {"sum": (a + b + sum(rest)) * scale, "extra": extra}\n'
)
HEADERS = {"X-API-Key": "e2e-key"}


def test_every_parameter_kind_round_trips_through_the_compiled_app(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "e2e_all_kinds"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "e2e-key")
    monkeypatch.syspath_prepend(str(tmp_path))
    client = TestClient(importlib.import_module("e2e_all_kinds.app").app)

    only_required = client.post("/mix", json={"a": 1}, headers=HEADERS)
    everything = client.post(
        "/mix",
        json={"a": 1, "b": 3, "rest": [4, 5], "scale": 2, "extra": {"z": 9}},
        headers=HEADERS,
    )

    assert only_required.status_code == 200
    assert only_required.json() == {"result": {"sum": 3, "extra": {}}}
    assert everything.status_code == 200
    assert everything.json() == {"result": {"sum": 26, "extra": {"z": 9}}}
