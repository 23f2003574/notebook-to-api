"""A generated app names the unknown request fields it ignored, so a typo is visible without strict mode."""
import json
import subprocess
import sys

import nbformat
import pytest

from backend.compiler import compile_notebook

SRC = "def add(a: int, b: int = 1) -> int:\n    return a + b\n\ndef other(x: int) -> int:\n    return x\n"
RUNNER = """
import json, sys
from fastapi.testclient import TestClient
import svc.app as m
c = TestClient(m.app)
h = {"X-API-Key": "k" * 20}
out = {}
for name, path, body in json.loads(sys.argv[1]):
    r = c.post(path, json=body, headers=h)
    out[name] = [r.status_code, r.headers.get("X-Notebook-API-Ignored-Fields"), r.text]
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    root = tmp_path_factory.mktemp("ignored")
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SRC)]), root / "nb.ipynb")
    compile_notebook(str(root / "nb.ipynb"), str(root / "svc"))
    return root


def _run(root, calls, **env):
    import os
    environ = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_")}
    environ.update(NOTEBOOK_API_KEY="k" * 20, **env)
    proc = subprocess.run([sys.executable, "-c", RUNNER, json.dumps(calls)], cwd=root, env=environ,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_unknown_fields_are_reported_and_the_call_still_succeeds(root):
    out = _run(root, [["typo", "/add", {"a": 1, "bb": 2, "c": 3}], ["clean", "/add", {"a": 1}],
                      ["other", "/other", {"x": 1, "a": 5}]])
    assert out["typo"][0] == 200 and out["typo"][1] == "bb, c" and json.loads(out["typo"][2]) == {"result": 2}
    assert out["clean"][1] is None
    assert out["other"][1] == "a"  # "a" is known to /add but not to /other


def test_strict_mode_still_rejects_and_adds_no_header(root):
    out = _run(root, [["typo", "/add", {"a": 1, "bb": 2}]], NOTEBOOK_API_STRICT_FIELDS="true")
    assert out["typo"][0] == 422 and out["typo"][1] is None


def test_the_header_is_exposed_to_browsers(root):
    assert "X-Notebook-API-Ignored-Fields" in (root / "svc" / "app.py").read_text().split("expose_headers=")[1].split("]")[0]
