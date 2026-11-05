"""A notebook cell with a top-level `async with` / `async for` (legal in
Jupyter) and no `await` inside must still produce an importable app."""
import importlib
import sys

import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

CELLS = [
    "import asyncio\nclass CM:\n    async def __aenter__(self):\n        return self\n"
    "    async def __aexit__(self, *a):\n        return False\n"
    "async def agen():\n    for i in range(3):\n        yield i\n",
    "total = 0\nasync with CM():\n    total = 10\nasync for i in agen():\n    total += i\n",
    "def get_total(n: int) -> int:\n    '''d'''\n    return total + n\n",
]


def test_top_level_async_with_and_for_compile_and_run(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(c) for c in CELLS]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    compile_notebook(str(path), str(tmp_path / "gen_async_app"))
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("gen_async_app.app")
        with TestClient(mod.app) as client:
            r = client.post(
                "/get_total", json={"n": 1},
                headers={"X-API-Key": "notebook-to-api-dev-key"},
            )
        assert r.status_code == 200 and r.json() == {"result": 14}
    finally:
        sys.path.remove(str(tmp_path))
        for k in [k for k in sys.modules if k.startswith("gen_async_app")]:
            del sys.modules[k]
