"""A synchronous endpoint answers 504 when the call outlives the request
timeout, so its OpenAPI responses must document 504 (matching real behaviour)."""
import asyncio
import importlib
import sys

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook


def test_504_is_documented_and_is_what_the_endpoint_returns(tmp_path, monkeypatch):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        "import asyncio\nasync def slow(n: int) -> int:\n    '''d'''\n"
        "    await asyncio.sleep(5)\n    return n\n"
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    compile_notebook(str(path), str(tmp_path / "gen_timeout_app"))
    monkeypatch.setenv("NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS", "1")
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("gen_timeout_app.app")
        responses = mod.app.openapi()["paths"]["/slow"]["post"]["responses"]
        with TestClient(mod.app) as client:
            r = client.post(
                "/slow", json={"n": 1},
                headers={"X-API-Key": "notebook-to-api-dev-key"},
            )
        assert r.status_code == 504
        assert str(r.status_code) in responses
        assert "TIMEOUT" in responses["504"]["description"]
    finally:
        sys.path.remove(str(tmp_path))
        for k in [k for k in sys.modules if k.startswith("gen_timeout_app")]:
            del sys.modules[k]
