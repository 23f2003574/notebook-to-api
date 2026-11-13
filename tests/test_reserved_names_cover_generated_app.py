"""Notebook functions must not be able to replace generated infrastructure."""
import ast
import io
import re
from contextlib import redirect_stdout

import nbformat
import pytest

from backend.compiler import compile_notebook
from backend.generator.api_generator import (
    RESERVED_INFRASTRUCTURE_NAMES,
    ReservedFunctionNameError,
    generate_fastapi_code,
)

# Infrastructure the audit found unprotected, each read by name at call time.
SHADOWABLE = [
    "cancel_task", "_isolated_call", "ISOLATE_EXECUTION", "_ISOLATE_SLOTS",
    "_webhook_connect", "_is_unsafe_webhook_host", "_webhook_ip_unsafe",
    "_TASK_STORE_LOCK", "run_isolated", "IsolatedWorker",
]

NOTEBOOK = '''
# notebook-to-api: background
def train_model(rows: int) -> int:
    """Background job."""
    return rows

def add_numbers(a: int, b: int) -> int:
    """Plain sync endpoint."""
    return a + b

async def fetch_value(key: str) -> str:
    """Async endpoint."""
    return key

# notebook-to-api: background
async def slow_async_job(n: int) -> int:
    """Async background job."""
    return n
'''
USER_FUNCTIONS = {"train_model", "add_numbers", "fetch_value", "slow_async_job"}


@pytest.mark.parametrize("name", SHADOWABLE)
def test_function_named_like_generated_infrastructure_is_rejected(name):
    assert name in RESERVED_INFRASTRUCTURE_NAMES
    with pytest.raises(ReservedFunctionNameError, match=name) as exc:
        generate_fastapi_code([{"name": name, "args": [], "return_type": "int"}])
    assert "Rename the function" in str(exc.value)


def test_ordinary_function_names_are_still_accepted():
    code = generate_fastapi_code([
        {"name": "predict", "args": [], "return_type": "int"},
        {"name": "cancel_order", "args": [], "return_type": "int"},
    ])
    assert "predict" in code and "cancel_order" in code


def _module_level_names(source):
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def test_every_module_level_name_in_a_generated_app_is_reserved(tmp_path):
    """Derived from a real generated app.py (not from the reserved set itself), so adding
    generated infrastructure without reserving its name fails here."""
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(NOTEBOOK)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    with redirect_stdout(io.StringIO()):
        compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "out"))
    generated = _module_level_names((tmp_path / "out" / "app.py").read_text(encoding="utf-8"))

    request_models = {
        name for name in generated
        if re.fullmatch(r"[A-Z]\w*Request", name)
        and any(name[:-7].lower() == f.lower() for f in USER_FUNCTIONS)
    }
    unreserved = sorted(generated - RESERVED_INFRASTRUCTURE_NAMES - USER_FUNCTIONS - request_models)
    assert not unreserved, (
        "generated module-level names a notebook function could shadow; add them to "
        f"RESERVED_INFRASTRUCTURE_NAMES in api_generator.py: {unreserved}"
    )
    assert {"cancel_task", "_isolated_call", "_webhook_connect"} <= generated  # inventory is real
