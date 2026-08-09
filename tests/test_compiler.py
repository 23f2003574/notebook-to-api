import ast
import json
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest

from backend.compiler import (
    compile_notebook,
    package_name_for_output_dir,
    STANDARD_LIBS
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compiler_pipeline():

    output_dir = "test_generated"

    compile_notebook(
        "notebooks/sample.ipynb",
        output_dir
    )

    assert Path(
        f"{output_dir}/app.py"
    ).exists()

    assert Path(
        f"{output_dir}/requirements.txt"
    ).exists()

    assert Path(
        f"{output_dir}/Dockerfile"
    ).exists()


def test_compiler_pipeline_handles_magics_and_broken_cells(tmp_path):
    """A notebook with Jupyter magics/shell escapes, and a cell that is
    still unparseable after stripping them, must compile end-to-end
    instead of crashing, and must not lose imports detected in other,
    valid cells.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "%matplotlib inline\n"
            "!pip install pandas\n"
            "import pandas as pd\n\n"
            "def summarize(count: int) -> int:\n"
            "    return count * 2\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "%%bash\necho this cell is not python"
        )
    )

    notebook_path = tmp_path / "magics.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    runtime_module = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")

    # The generated runtime module must itself be valid, importable Python.
    ast.parse(runtime_module)

    requirements = (output_dir / "requirements.txt").read_text(
        encoding="utf-8"
    )

    assert "pandas" in requirements


def test_compiler_pipeline_does_not_expose_class_methods_or_nested_functions(
    tmp_path
):
    """A class method or a closure nested inside another function is not
    callable as a standalone module-level function, so it must not be
    turned into its own generated API endpoint.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "class Model:\n"
            "    def predict(self, x: int) -> int:\n"
            "        return x * 2\n\n"
            "def run(x: int) -> int:\n"
            "    def helper(y: int) -> int:\n"
            "        return y + 1\n"
            "    return helper(x)\n"
        )
    )

    notebook_path = tmp_path / "methods.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    assert '"/run"' in generated_app or "'/run'" in generated_app
    assert '"/predict"' not in generated_app
    assert '"/helper"' not in generated_app


def test_compiler_pipeline_deduplicates_functions_redefined_across_cells(
    tmp_path
):
    """Iteratively re-running a cell with a fixed version of the same
    function is a normal notebook workflow. The compiler must not
    register two conflicting routes for the same path -- FastAPI/Starlette
    would route every request to the *first*-registered one while the
    OpenAPI schema (dict-keyed by path) would document the *last*, so the
    served and documented behaviour would silently diverge.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    # fixed version\n"
            "    return a + b + 1\n"
        )
    )

    notebook_path = tmp_path / "redefined.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    assert generated_app.count('"/add"') == 1
    assert generated_app.count("def add(") == 1


def test_compiler_pipeline_generates_awaitable_endpoint_for_async_function(
    tmp_path
):
    """`async def` functions are common in notebooks that call external
    APIs (httpx/aiohttp). Compiling one must produce a valid, importable
    generated app whose endpoint actually awaits the coroutine instead of
    returning it unresolved.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "async def fetch_data(url: str) -> dict:\n"
            "    return {'url': url}\n"
        )
    )

    notebook_path = tmp_path / "async_func.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)

    assert "async def fetch_data(" in generated_app
    assert "await notebook_module.fetch_data(" in generated_app


def test_compiler_pipeline_calls_keyword_only_args_by_keyword(tmp_path):
    """`def train(data, *, epochs=10)` is a common ML-notebook signature.
    Keyword-only params must be forwarded as `epochs=req.epochs`, not
    positionally, or the generated endpoint raises a TypeError on every
    call.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def score(data: list, *, epochs: int = 10) -> dict:\n"
            "    return {'data': data, 'epochs': epochs}\n"
        )
    )

    notebook_path = tmp_path / "kwonly.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)

    assert "notebook_module.score(req.data, epochs=req.epochs)" in generated_app


def test_compiler_pipeline_positional_only_args_work_end_to_end(tmp_path):
    """Confirmed exploitable before this fix: positional-only params (those
    before a bare `/`) were dropped during extraction, so the generated
    endpoint called notebook_module.f(...) without them and every request
    raised a TypeError for missing required arguments.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def combine(a: int, b: int, /, c: int) -> int:\n"
            "    return a + b + c\n"
        )
    )

    notebook_path = tmp_path / "posonly.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)

    assert "notebook_module.combine(req.a, req.b, req.c)" in generated_app


def test_compiler_pipeline_zero_argument_function_compiles_end_to_end(tmp_path):
    """Confirmed exploitable before this fix: a zero-parameter notebook
    function produced an empty Pydantic model class body (no fields, no
    model_config), which is a SyntaxError -- app.py failed to even
    `compile()`, breaking every endpoint in the generated API, not just
    the zero-arg one.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def get_status() -> dict:\n"
            "    return {'ok': True}\n"
        )
    )

    notebook_path = tmp_path / "zeroarg.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)
    compile(generated_app, "app.py", "exec")


def test_compiler_pipeline_rejects_notebook_function_named_verify_api_key(tmp_path):
    """Confirmed exploitable before this fix: a notebook function named
    verify_api_key rebinds the generated app's own auth-check function at
    module load time, silently disabling API-key authentication for every
    endpoint defined after it. compile_notebook must fail loudly instead
    of producing that app.
    """
    from backend.generator.api_generator import ReservedFunctionNameError

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def verify_api_key() -> dict:\n"
            "    return {'ok': True}\n"
        )
    )

    notebook_path = tmp_path / "reserved.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    with pytest.raises(ReservedFunctionNameError):
        compile_notebook(str(notebook_path), str(output_dir))


def test_compiler_pipeline_case_colliding_function_names_get_distinct_models(tmp_path):
    """Confirmed exploitable before this fix: two notebook functions
    differing only by the case of their first letter (e.g. "get_data" and
    "Get_data") produced identically-named Pydantic request model classes,
    so the second class definition silently shadowed the first -- one
    endpoint ended up validating requests against the *other* function's
    fields.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def get_data(query: str) -> dict:\n"
            "    return {'query': query}\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def Get_data(id: int) -> dict:\n"
            "    return {'id': id}\n"
        )
    )

    notebook_path = tmp_path / "collide.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)
    compile(generated_app, "app.py", "exec")

    assert generated_app.count("class Get_dataRequest(BaseModel):") == 1
    assert generated_app.count("class Get_dataRequest_2(BaseModel):") == 1


def test_package_name_for_output_dir_uses_basename():

    assert package_name_for_output_dir("generated") == "generated"
    assert package_name_for_output_dir("my_output") == "my_output"
    assert package_name_for_output_dir("build/my_output") == "my_output"


def test_package_name_for_output_dir_rejects_invalid_identifier():

    with pytest.raises(ValueError):
        package_name_for_output_dir("my-output")


def test_package_name_for_output_dir_rejects_python_keyword():

    with pytest.raises(ValueError):
        package_name_for_output_dir("import")


def test_compiler_pipeline_respects_custom_output_dir(tmp_path):
    """The --output flag is documented as configurable (it has a CLI flag
    with a default), but write_runtime_module used to hardcode
    "generated/runtime/..." regardless of output_dir while the generated
    app.py always imported the fixed name "generated" -- so any non-
    default --output directory produced files in the wrong place with an
    import that could never resolve them.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "my_custom_output"

    compile_notebook(str(notebook_path), str(output_dir))

    # The runtime module must live under the actual output directory, not
    # the old hardcoded "generated/runtime/" path.
    assert (output_dir / "runtime" / "notebook_module.py").exists()

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")
    ast.parse(generated_app)
    assert "import my_custom_output.runtime.notebook_module" in generated_app

    dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY . my_custom_output/" in dockerfile
    assert '"my_custom_output.app:app"' in dockerfile


def test_compiler_pipeline_custom_output_dir_actually_runs(tmp_path):
    """Static checks confirm the generated files are consistent with each
    other; this drives a real request through the compiled app with a
    custom --output directory to confirm it actually imports and runs,
    not just that the generated source text looks right. Run in a fresh
    subprocess/cwd since the generated package name and its import must
    be resolved by a real Python import machinery run from the directory
    compilation happened in.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "my_custom_output")

from my_custom_output.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
resp = client.post(
    "/add",
    json={{"a": 2, "b": 3}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": 5}}, resp.json()
print("CUSTOM_OUTPUT_DIR_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CUSTOM_OUTPUT_DIR_E2E_OK" in proc.stdout


def test_standard_libs_covers_common_stdlib_modules_beyond_the_old_hardcoded_list():
    """The old STANDARD_LIBS was a hand-picked set of 12 names, missing
    the vast majority of the standard library. Any notebook using one of
    the missed modules got it written into requirements.txt as if it were
    a third-party PyPI package -- and for some names (e.g. "asyncio"),
    PyPI has an unrelated real package that pip actually installs,
    shadowing the built-in module.
    """

    commonly_missed = {
        "asyncio", "random", "logging", "subprocess", "csv", "sqlite3",
        "uuid", "hashlib", "threading", "shutil", "glob", "base64",
        "enum", "dataclasses", "copy", "pickle", "warnings", "traceback",
        "inspect", "urllib", "string", "decimal", "tempfile", "io",
    }

    assert commonly_missed <= STANDARD_LIBS


def test_standard_libs_does_not_exclude_third_party_packages():

    third_party = {"pandas", "numpy", "requests", "sklearn", "fastapi"}

    assert not (third_party & STANDARD_LIBS)


def test_compiler_pipeline_excludes_stdlib_modules_from_requirements(tmp_path):

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import asyncio\n"
            "import random\n"
            "import pandas as pd\n\n"
            "def compute(x: int) -> int:\n"
            "    return x\n"
        )
    )

    notebook_path = tmp_path / "stdlib_imports.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(
        encoding="utf-8"
    )
    deps = set(requirements.split())

    assert "asyncio" not in deps
    assert "random" not in deps
    assert "pandas" in deps


def test_compiler_pipeline_optional_none_default_param_is_actually_optional(
    tmp_path
):
    """`def greet(name, title=None)` is an extremely common Python idiom
    for an optional parameter. Confirmed live before this fix: the
    generated endpoint 422'd on a request that omitted `title`, because
    the generated Pydantic field was marked required -- default=None was
    indistinguishable from "no default" once extracted.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def greet(name: str, title: str = None) -> str:\n"
                            "    return ((title or '') + ' ' + name).strip()\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
resp = client.post(
    "/greet",
    json={{"name": "Ada"}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": "Ada"}}, resp.json()
print("OPTIONAL_NONE_DEFAULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OPTIONAL_NONE_DEFAULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_api_key_auth_still_works_end_to_end(tmp_path):
    """Behavioral check that switching the API key comparison to
    hmac.compare_digest didn't change any of the three real outcomes:
    missing header and wrong key both 401, correct key succeeds.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
payload = {{"a": 1, "b": 2}}

no_header = client.post("/add", json=payload)
assert no_header.status_code == 401, no_header.text

wrong_key = client.post("/add", json=payload, headers={{"X-API-Key": "wrong"}})
assert wrong_key.status_code == 401, wrong_key.text

correct_key = client.post(
    "/add", json=payload, headers={{"X-API-Key": "notebook-to-api-dev-key"}}
)
assert correct_key.status_code == 200, correct_key.text
assert correct_key.json() == {{"result": 3}}, correct_key.json()

print("API_KEY_AUTH_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "API_KEY_AUTH_E2E_OK" in proc.stdout


def test_compiler_pipeline_typing_generic_and_enum_params_work_end_to_end(tmp_path):
    """Confirmed exploitable before this fix: a parameter typed with a
    typing-module generic (List[float], Optional[str], Dict[str, Any]) or
    a notebook-defined Enum produced a generated Pydantic field
    referencing a name nothing in the generated app imports. The class
    definition itself didn't fail (deferred annotation evaluation), but
    the very first real use -- building the schema for /docs, /openapi.json,
    or the first request -- raised PydanticUserError/NameError.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "from typing import List, Optional, Dict, Any\n"
                            "from enum import Enum\n\n"
                            "class Priority(Enum):\n"
                            "    LOW = 'low'\n"
                            "    HIGH = 'high'\n\n"
                            "def summarize(\n"
                            "    scores: List[float],\n"
                            "    label: Optional[str] = None,\n"
                            "    meta: Dict[str, Any] = None,\n"
                            "    priority: Optional[Priority] = None,\n"
                            ") -> str:\n"
                            "    return label or 'none'\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app, SummarizeRequest
from fastapi.testclient import TestClient

# Building the schema is exactly what raised PydanticUserError before this fix.
schema = SummarizeRequest.model_json_schema()
assert schema["properties"]["scores"]["type"] == "array", schema

client = TestClient(app)
resp = client.post(
    "/summarize",
    json={{"scores": [1.0, 2.0], "label": "x", "meta": {{"a": 1}}}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": "x"}}, resp.json()
print("TYPING_GENERIC_ENUM_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TYPING_GENERIC_ENUM_E2E_OK" in proc.stdout


def test_compiler_pipeline_background_tasks_are_evicted_after_ttl_expires(tmp_path):
    """Confirmed exploitable before this fix: the generated TASKS registry
    never evicted anything on its own -- a long-running deployment
    handling steady background-task traffic accumulated one entry per
    call forever. With TASK_TTL_SECONDS forced to 0 (via the
    NOTEBOOK_API_TASK_TTL_SECONDS env var the generated app already
    reads), a task created before a second task must be gone by the time
    the second one is created, since eviction runs opportunistically on
    every new task's creation.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def process_data(x: int) -> int:\n"
                            "    return x\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys
import time

os.environ["NOTEBOOK_API_TASK_TTL_SECONDS"] = "0"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

first = client.post("/process_data", json={{"x": 1}}, headers=headers)
assert first.status_code == 200, first.text
first_task_id = first.json()["task_id"]

# Any nonzero elapsed time exceeds a TTL of 0, so the first task is
# eligible for eviction by the time the second one is created.
time.sleep(0.01)

second = client.post("/process_data", json={{"x": 2}}, headers=headers)
assert second.status_code == 200, second.text

lookup = client.get(f"/tasks/{{first_task_id}}", headers=headers)
assert lookup.json() == {{"error": "Task not found"}}, lookup.json()

print("TASK_TTL_EVICTION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TASK_TTL_EVICTION_E2E_OK" in proc.stdout


def test_compiler_pipeline_tasks_endpoints_reject_unauthenticated_requests(tmp_path):
    """Confirmed exploitable before this fix: GET /tasks and GET
    /tasks/{task_id} returned stored function call inputs/outputs with no
    API key at all, and the DELETE/POST tasks endpoints let anyone wipe
    task state -- every other endpoint in the generated app (including
    /auth/validate) required Depends(verify_api_key), but the entire
    /tasks family was left open.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def process_data(x: int) -> int:\n"
            "    return x\n"
        )
    )

    notebook_path = tmp_path / "tasksauth.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(output_dir.parent)!r})

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)

assert client.get("/tasks").status_code == 401
assert client.get("/tasks/whatever").status_code == 401
assert client.delete("/tasks/completed").status_code == 401
assert client.delete("/tasks/failed").status_code == 401
assert client.post("/tasks/cleanup").status_code == 401
assert client.post("/tasks/reset").status_code == 401
assert client.delete("/tasks/whatever").status_code == 401

headers = {{"X-API-Key": "notebook-to-api-dev-key"}}
assert client.get("/tasks", headers=headers).status_code == 200
assert client.post("/tasks/reset", headers=headers).status_code == 200

print("TASKS_AUTH_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(output_dir.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TASKS_AUTH_E2E_OK" in proc.stdout