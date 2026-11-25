"""Unknown fields nested inside request models are reported (default) or rejected (strict) by dotted path."""
import json
import os
import subprocess
import sys

import nbformat
import pytest

from backend.compiler import compile_notebook

SRC = '''
from dataclasses import dataclass
from typing import Optional, TypedDict, Union

@dataclass
class Point:
    x: int
    y: int = 0

class Opts(TypedDict):
    a: int

def one(p: Point) -> int:
    return p.x

def many(ps: list[Point]) -> int:
    return len(ps)

def typed(o: Opts) -> int:
    return o["a"]

def maybe(p: Optional[Point] = None) -> int:
    return 0 if p is None else p.x

def either(v: Union[Point, int]) -> int:
    return 1

def plain(a: int) -> int:
    return a
'''
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
    root = tmp_path_factory.mktemp("nested")
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SRC)]), root / "nb.ipynb")
    compile_notebook(str(root / "nb.ipynb"), str(root / "svc"))
    return root


def run(root, calls, **env):
    environ = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_")}
    environ.update(NOTEBOOK_API_KEY="k" * 20, **env)
    proc = subprocess.run([sys.executable, "-c", RUNNER, json.dumps(calls)], cwd=root, env=environ,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


CASES = [
    ("nested", "/one", {"p": {"x": 1, "extra": 2}}),
    ("multi", "/one", {"p": {"x": 1, "e1": 2, "e2": 3}, "top": 1}),
    ("list", "/many", {"ps": [{"x": 1}, {"x": 2, "bad": 1}]}),
    ("typed", "/typed", {"o": {"a": 1, "zzz": 2}}),
    ("optional", "/maybe", {"p": {"x": 1, "oops": 2}}),
    ("union_ok", "/either", {"v": {"x": 1, "y": 2}}),
    ("clean", "/one", {"p": {"x": 1, "y": 2}}),
    ("plain_top", "/plain", {"a": 1, "typo": 2}),
]


def test_default_mode_reports_dotted_paths_and_keeps_working(root):
    out = run(root, CASES, NOTEBOOK_API_STRICT_FIELDS="false")
    assert out["nested"][:2] == [200, "p.extra"]
    assert out["multi"][1] == "p.e1, p.e2, top"
    assert out["list"][:2] == [200, "ps[1].bad"]
    assert out["typed"][:2] == [200, "o.zzz"]
    assert out["optional"][:2] == [200, "p.oops"]
    assert out["union_ok"][1] is None and out["clean"][1] is None
    assert out["plain_top"][:2] == [200, "typo"]  # top-level contract unchanged


def test_strict_mode_rejects_nested_unknown_fields_with_422(root):
    out = run(root, CASES, NOTEBOOK_API_STRICT_FIELDS="true")
    for name, path in (("nested", "p.extra"), ("list", "ps[1].bad"), ("typed", "o.zzz"), ("optional", "p.oops")):
        assert out[name][0] == 422 and path in out[name][2], name
    assert out["clean"][0] == 200 and out["union_ok"][0] == 200
    assert out["plain_top"][0] == 422


def test_header_is_bounded(root):
    body = {"p": {"x": 1, **{f"field_{i:04d}": i for i in range(300)}}}
    out = run(root, [("big", "/one", body)], NOTEBOOK_API_STRICT_FIELDS="false")
    assert out["big"][0] == 200 and len(out["big"][1]) <= 500
