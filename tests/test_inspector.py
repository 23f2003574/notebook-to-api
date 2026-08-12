import nbformat
import pytest

from backend.inspector import (
    inspect_notebook,
    inspect_notebook_data,
    print_compile_summary,
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
