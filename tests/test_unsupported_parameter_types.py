"""Functions that require a parameter JSON cannot carry get no endpoint and are reported."""
import json
import subprocess
import sys
from typing import Callable  # noqa: F401

import nbformat
import pytest

from backend.parser.ast_parser import extract_functions_from_code, extract_skipped_functions_from_code

CODE = '''
import typing, types
from typing import Callable, Optional, BinaryIO, List
def ok(a: int, b: str = "x") -> int: return a
def needs_cb(f: Callable[[int], int]): return f(1)
def needs_cb_nested(fs: List[Callable[[int], int]]): return 1
def quoted(f: "typing.Callable[[int], int]"): return 1
def needs_module(m: types.ModuleType): return 1
def binary_ok(fh: BinaryIO): return 1
def kwonly(*, f: Callable): return 1
def optional_cb(a: int, cb: Optional[Callable] = None): return a
def kw_optional(*, cb: Callable = print): return 1
'''


def test_required_unrepresentable_parameters_are_not_endpoints():
    names = {f["name"] for f in extract_functions_from_code(CODE)}
    assert names == {"ok", "optional_cb", "kw_optional", "binary_ok"}


def test_each_dropped_function_is_reported_with_the_parameter_and_reason():
    skipped = {s["name"]: s["reason"] for s in extract_skipped_functions_from_code(CODE)}
    assert set(skipped) == {"needs_cb", "needs_cb_nested", "quoted", "needs_module", "kwonly"}
    assert "'f' (a callable)" in skipped["needs_cb"] and "JSON request cannot provide" in skipped["needs_cb"]
    assert "'m' (a module)" in skipped["needs_module"]


@pytest.fixture
def nb(tmp_path):
    book = nbformat.v4.new_notebook()
    book.cells.append(nbformat.v4.new_code_cell(CODE))
    path = tmp_path / "nb.ipynb"
    nbformat.write(book, path)
    return str(path)


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "backend.cli", *args], capture_output=True, text=True)


def test_validate_warns_and_strict_fails(nb):
    plain = _cli("validate", nb)
    assert plain.returncode == 1 and "needs_cb" in plain.stdout and "cannot provide" in plain.stdout
    strict = _cli("validate", nb, "--strict")
    assert strict.returncode == 2
    data = json.loads(_cli("validate", nb, "--json").stdout)
    assert "needs_module" in json.dumps(data["skipped_functions"])


def test_compile_generates_no_endpoint_and_lists_the_skip(nb, tmp_path):
    out = _cli("compile", nb, "--output", str(tmp_path / "app"))
    assert out.returncode == 0, out.stderr
    assert "POST /ok" in out.stdout and "POST /needs_cb" not in out.stdout
    assert "needs_cb" in out.stdout and "cannot provide" in out.stdout
    assert '"/needs_cb"' not in (tmp_path / "app" / "app.py").read_text()
