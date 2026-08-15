import nbformat
import pytest

from backend.inspector import (
    inspect_notebook,
    inspect_notebook_data,
    print_compile_summary,
    _aggregate_skipped_functions,
    _list_generated_files,
    _reserved_name_conflicts,
)
from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES


def _write_notebook(path, source):
    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(nbformat.v4.new_code_cell(source))
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)


def test_reserved_name_conflicts_flags_a_colliding_function_name():

    functions = [
        {"name": "health_check"},
        {"name": "add"},
    ]

    assert _reserved_name_conflicts(functions) == ["health_check"]


def test_reserved_name_conflicts_is_empty_when_nothing_collides():

    functions = [
        {"name": "add"},
        {"name": "subtract"},
    ]

    assert _reserved_name_conflicts(functions) == []


def test_reserved_name_conflicts_is_sorted_and_deduplicated():

    functions = [
        {"name": "verify_api_key"},
        {"name": "app"},
        {"name": "add"},
    ]

    assert _reserved_name_conflicts(functions) == sorted(
        {"verify_api_key", "app"}
    )


def test_inspect_notebook_data_reports_no_conflicts_for_a_clean_notebook(tmp_path):

    notebook_path = tmp_path / "clean.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    data = inspect_notebook_data(str(notebook_path), str(tmp_path / "generated"))

    assert data["reserved_name_conflicts"] == []


def test_inspect_notebook_data_flags_a_reserved_name_before_compile_would(tmp_path):
    """Before this fix, /api/inspect and `inspect` had no idea
    generate_fastapi_code (backend/generator/api_generator.py) would
    later refuse to compile a function named "health_check" -- the first
    signal a notebook author got was a compile failure, not a preview.
    """

    notebook_path = tmp_path / "colliding.ipynb"
    _write_notebook(
        notebook_path,
        "def health_check() -> dict:\n    return {}\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    data = inspect_notebook_data(str(notebook_path), str(tmp_path / "generated"))

    assert data["reserved_name_conflicts"] == ["health_check"]
    # The clean function must still be reported normally alongside it.
    assert {f["name"] for f in data["functions"]} == {"health_check", "add"}


def test_inspect_notebook_data_flags_every_reserved_infrastructure_name(tmp_path):
    """Exercises the full RESERVED_INFRASTRUCTURE_NAMES set (not just one
    example), so a future addition to that set is automatically covered
    by inspect too, without this test needing to be updated.
    """

    source = "\n\n".join(
        f"def {name}(): pass" for name in sorted(RESERVED_INFRASTRUCTURE_NAMES)
    )

    notebook_path = tmp_path / "all_reserved.ipynb"
    _write_notebook(notebook_path, source)

    data = inspect_notebook_data(str(notebook_path), str(tmp_path / "generated"))

    assert set(data["reserved_name_conflicts"]) == RESERVED_INFRASTRUCTURE_NAMES


def test_inspect_notebook_prints_a_reserved_name_conflict_warning(tmp_path, capsys):

    notebook_path = tmp_path / "colliding.ipynb"
    _write_notebook(
        notebook_path,
        "def verify_api_key() -> dict:\n    return {}\n",
    )

    inspect_notebook(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Reserved Name Conflicts" in output
    assert "verify_api_key" in output


def test_inspect_notebook_omits_the_warning_section_for_a_clean_notebook(
    tmp_path, capsys
):

    notebook_path = tmp_path / "clean.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    inspect_notebook(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Reserved Name Conflicts" not in output


def test_inspect_notebook_prints_a_functions_own_docstring(tmp_path, capsys):
    """A notebook function's own docstring already becomes its compiled
    endpoint's OpenAPI description (see api_generator.py) -- but
    `inspect`, this tool's own "preview what compiling this notebook will
    do" report, never showed it at all, even though inspect_notebook_data
    (and `inspect --json`) already carried it.
    """

    notebook_path = tmp_path / "documented.ipynb"
    _write_notebook(
        notebook_path,
        (
            "def train_model(epochs: int) -> str:\n"
            '    """Train the classifier for the given number of epochs.\n\n'
            "    Returns a short accuracy summary.\n"
            '    """\n'
            "    return 'done'\n"
        ),
    )

    inspect_notebook(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Train the classifier for the given number of epochs." in output
    assert "Returns a short accuracy summary." in output


def test_inspect_notebook_omits_docstring_lines_for_an_undocumented_function(
    tmp_path, capsys
):

    notebook_path = tmp_path / "undocumented.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    inspect_notebook(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "1. add(a: int, b: int) -> int" in output
    # No stray docstring lines (blank or otherwise) inserted between the
    # route line and the example payload for a function with no
    # docstring at all.
    assert "   Route: POST /add\n   Example Payload:" in output


def test_inspect_notebook_data_reports_endpoints_and_flags_background_ones(tmp_path):
    """Before this fix, inspect_notebook_data -- the data behind both
    `inspect --json` and POST /api/inspect -- had no way to tell a caller
    which functions would compile into background/task_id-based endpoints
    vs synchronous ones. That distinction was only ever visible *after*
    compiling, via print_compile_summary or POST /api/compile's
    "endpoints" field.
    """

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def train_model(epochs: int) -> str:\n    return 'done'\n",
    )

    data = inspect_notebook_data(str(notebook_path), str(tmp_path / "generated"))

    endpoints = {e["path"]: e for e in data["endpoints"]}

    assert endpoints["/add"] == {"path": "/add", "method": "POST", "is_async": False}
    assert endpoints["/train_model"] == {
        "path": "/train_model", "method": "POST", "is_async": True
    }


def test_inspect_notebook_data_endpoints_is_empty_for_a_notebook_with_no_functions(
    tmp_path
):

    notebook_path = tmp_path / "empty.ipynb"
    _write_notebook(notebook_path, "x = 1\n")

    data = inspect_notebook_data(str(notebook_path), str(tmp_path / "generated"))

    assert data["endpoints"] == []


def test_list_generated_files_excludes_pycache_directories(tmp_path):

    output_dir = tmp_path / "generated"
    (output_dir / "__pycache__").mkdir(parents=True)
    (output_dir / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"\x00")
    (output_dir / "app.py").write_text("# app\n")

    assert sorted(_list_generated_files(output_dir)) == ["app.py"]


def test_list_generated_files_returns_an_empty_list_when_the_directory_does_not_exist(
    tmp_path,
):

    assert _list_generated_files(tmp_path / "does_not_exist") == []


def test_list_generated_files_excludes_compile_metadata(tmp_path):
    """.compile_metadata.json (write_compile_metadata, backend/compiler.py)
    is dashboard-internal bookkeeping -- read only by
    list_notebooks/_currently_compiled_notebook_metadata in
    routes/upload.py -- never a real compiled deliverable, and its
    "source_notebook" field is the source notebook's absolute filesystem
    path on the compiling server. Before this fix, it showed up in
    generated_files exactly like a real output file, from where it also
    flowed into GET /api/download's zip, GET /api/generated/{filename}'s
    preview, and -- worst of all -- the deployed Docker image itself.
    """

    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "app.py").write_text("# app\n")
    (output_dir / ".compile_metadata.json").write_text(
        '{"source_notebook": "/home/someuser/private/nb.ipynb"}\n'
    )

    assert sorted(_list_generated_files(output_dir)) == ["app.py"]


def test_inspect_notebook_prints_generated_files_but_excludes_pycache(
    tmp_path, capsys
):

    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "app.py").write_text("# app\n")

    pycache_dir = output_dir / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "app.cpython-314.pyc").write_bytes(b"\x00")

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    inspect_notebook(str(notebook_path), str(output_dir))

    output = capsys.readouterr().out
    assert "- app.py" in output
    assert "__pycache__" not in output
    assert ".pyc" not in output


def test_inspect_notebook_data_excludes_pycache_from_generated_files(tmp_path):
    """__pycache__ is created by Python itself the first time the compiled
    app or its runtime module gets imported (e.g. by `serve`, a prior
    `export-openapi` call, or a test suite) -- it is not part of what the
    compiler actually wrote, and its .pyc filenames are tied to whichever
    Python version happened to import it, so they aren't even stable
    across machines for the same compiled output. Before this fix, it
    still showed up in generated_files as if it were.
    """

    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "app.py").write_text("# app\n")

    pycache_dir = output_dir / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "app.cpython-314.pyc").write_bytes(b"\x00")

    nested_pycache_dir = output_dir / "runtime" / "__pycache__"
    nested_pycache_dir.mkdir(parents=True)
    (nested_pycache_dir / "notebook_module.cpython-314.pyc").write_bytes(b"\x00")
    (output_dir / "runtime" / "notebook_module.py").write_text("# runtime\n")

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path, "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    data = inspect_notebook_data(str(notebook_path), str(output_dir))

    assert data["generated_files"] == ["app.py", "runtime/notebook_module.py"]


def test_inspect_notebook_prints_the_background_marker_next_to_its_route(
    tmp_path, capsys
):
    """Matches the same "[background]" marking print_compile_summary
    already prints after compiling (see
    test_print_compile_summary_lists_endpoints_and_flags_background_ones
    below) -- `inspect` is the tool's preview step and should show the
    same classification before compiling, not just after.
    """

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def train_model(epochs: int) -> str:\n    return 'done'\n",
    )

    inspect_notebook(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Route: POST /train_model  [background]" in output

    add_route_line = next(
        line for line in output.splitlines()
        if line.strip() == "Route: POST /add"
    )
    assert "[background]" not in add_route_line


def test_print_compile_summary_lists_endpoints_and_flags_background_ones(
    tmp_path, capsys
):
    """Shared by both `compile` and `serve` (see backend/cli.py and
    backend/serve.py) so what a caller sees is identical either way, and
    matches the same background/task_id-based marking POST /api/compile's
    "endpoints" field already uses.
    """

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def train_model(epochs: int) -> str:\n    return 'done'\n",
    )

    print_compile_summary(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Generated 2 endpoint(s):" in output
    assert "POST /add" in output
    assert "POST /train_model  [background]" in output

    add_line = next(
        line for line in output.splitlines() if line.strip() == "POST /add"
    )
    assert "[background]" not in add_line


def test_print_compile_summary_lists_third_party_dependencies(tmp_path, capsys):

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "import pandas as pd\n\n"
        "def summarize(count: int) -> int:\n    return count * 2\n",
    )

    print_compile_summary(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Dependencies: pandas" in output


def test_aggregate_skipped_functions_reports_unsupported_signatures():

    code_cells = [
        "def unsupported(a, **kwargs):\n    return a\n",
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    ]

    skipped = _aggregate_skipped_functions(code_cells, {"add"})

    assert [s["name"] for s in skipped] == ["unsupported"]


def test_aggregate_skipped_functions_omits_names_that_ended_up_exposed():
    """A later cell can redefine a name that an earlier cell's unsupported
    version would otherwise be reported as skipped for -- exactly what
    running the whole notebook top to bottom in one kernel would do (see
    deduplicate_functions_by_name). Once that redefinition made it into
    the final, exposed function list, the earlier skip must not still be
    reported.
    """

    code_cells = [
        "def add(a, **kwargs):\n    return a\n",
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    ]

    skipped = _aggregate_skipped_functions(code_cells, {"add"})

    assert skipped == []


def test_aggregate_skipped_functions_is_sorted_and_deduplicated():

    code_cells = [
        "class Model:\n    def predict(self, x):\n        return x\n",
        "def outer():\n    def predict(y):\n        return y\n    return predict\n",
    ]

    skipped = _aggregate_skipped_functions(code_cells, set())

    assert [s["name"] for s in skipped] == ["predict"]


def test_inspect_notebook_data_reports_skipped_functions(tmp_path):

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def unsupported(a, **kwargs):\n    return a\n",
    )

    data = inspect_notebook_data(str(notebook_path), str(tmp_path / "generated"))

    assert data["skipped_functions"] == [
        {
            "name": "unsupported",
            "reason": (
                "uses *args/**kwargs, which can't be represented as a "
                "fixed set of request fields"
            ),
        }
    ]
    assert {f["name"] for f in data["functions"]} == {"add"}


def test_inspect_notebook_data_skipped_functions_is_empty_for_a_clean_notebook(
    tmp_path,
):

    notebook_path = tmp_path / "clean.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    data = inspect_notebook_data(str(notebook_path), str(tmp_path / "generated"))

    assert data["skipped_functions"] == []


def test_inspect_notebook_prints_a_skipped_function_warning(tmp_path, capsys):

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "class Model:\n    def predict(self, x):\n        return x\n",
    )

    inspect_notebook(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Skipped Functions" in output
    assert "predict" in output
    assert "callable as a standalone endpoint" in output


def test_inspect_notebook_omits_the_skipped_section_for_a_clean_notebook(
    tmp_path, capsys
):

    notebook_path = tmp_path / "clean.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    inspect_notebook(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Skipped Functions" not in output


def test_print_compile_summary_lists_skipped_functions(tmp_path, capsys):

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def unsupported(a, **kwargs):\n    return a\n",
    )

    print_compile_summary(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Skipped 1 function(s)" in output
    assert "unsupported:" in output


def test_print_compile_summary_omits_skipped_line_when_there_are_none(
    tmp_path, capsys
):

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    print_compile_summary(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Skipped" not in output


def test_print_compile_summary_omits_dependencies_line_when_there_are_none(
    tmp_path, capsys
):

    notebook_path = tmp_path / "nb.ipynb"
    _write_notebook(
        notebook_path,
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )

    print_compile_summary(str(notebook_path), str(tmp_path / "generated"))

    output = capsys.readouterr().out
    assert "Dependencies:" not in output
