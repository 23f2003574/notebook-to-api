import os
from pathlib import Path

from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells,
)

from backend.parser.ast_parser import (
    extract_functions_from_code,
    extract_imports_from_code,
    deduplicate_functions_by_name,
)

from backend.generator.api_generator import (
    LONG_RUNNING_KEYWORDS,
    RESERVED_INFRASTRUCTURE_NAMES,
)


def _is_background_function(name):
    """Whether generate_fastapi_code (generator/api_generator.py) will
    compile `name` into a background/task_id-based endpoint rather than a
    synchronous one, per LONG_RUNNING_KEYWORDS.

    Shared by every surface that classifies a function this way --
    inspect_notebook, _endpoint_metadata below, and print_compile_summary
    -- so they can't drift from each other or from POST /api/compile's own
    identical check in routes/upload.py.
    """
    return any(kw in name.lower() for kw in LONG_RUNNING_KEYWORDS)


def _endpoint_metadata(functions):
    """The {"path", "method", "is_async"} shape POST /api/compile's
    "endpoints" field already returns (see routes/upload.py), computed
    here from a notebook that hasn't been compiled yet.

    Before this, whether a function would become a background/task_id
    endpoint or a synchronous one -- a real difference in what the
    generated app does, not just a formatting detail -- was only ever
    visible *after* compiling. inspect_notebook_data already exists
    specifically to preview compile-time outcomes (see
    reserved_name_conflicts above, added for the same reason), so it had
    the same gap for this classification that reserved_name_conflicts
    closed for name collisions.
    """
    return [
        {
            "path": f"/{func['name']}",
            "method": "POST",
            "is_async": _is_background_function(func["name"]),
        }
        for func in functions
    ]


def _reserved_name_conflicts(functions):
    """Names in `functions` that generate_fastapi_code will refuse to
    compile (see ReservedFunctionNameError in generator/api_generator.py),
    because they collide with an identifier the generated app itself
    defines (auth, task management, or infrastructure routes).

    Before this, /api/inspect and the `inspect` CLI command -- the tool's
    own "preview what compiling this notebook will do" step -- had no
    idea this check existed. A notebook with a function named
    "health_check" or "verify_api_key" inspected cleanly with no warning
    at all, and the very first time its author learned about the
    conflict was a compile failure (or, from the dashboard, a bare 500)
    at the *next* step, for a problem the tool already had the exact
    answer to.
    """
    return sorted(
        {func["name"] for func in functions} & RESERVED_INFRASTRUCTURE_NAMES
    )


def inspect_notebook(notebook_path, output_dir="generated"):
    """
    Print a detailed analysis report for a notebook.
    """

    notebook = load_notebook(notebook_path)

    code_cells = extract_code_cells(notebook)

    all_functions = []
    all_imports = set()

    for cell in code_cells:

        funcs = extract_functions_from_code(cell)
        imports = extract_imports_from_code(cell)

        all_functions.extend(funcs)
        all_imports.update(imports)

    all_functions = deduplicate_functions_by_name(all_functions)

    reserved_name_conflicts = _reserved_name_conflicts(all_functions)

    print("\nNotebook Analysis Report")
    print("=" * 30)

    if reserved_name_conflicts:
        print("\n⚠ Reserved Name Conflicts (compilation will fail):")
        print("-" * 20)
        for name in reserved_name_conflicts:
            print(f"- {name}")

    print("\nFunctions Found:")
    print("-" * 20)

    for idx, func in enumerate(all_functions, start=1):

        args_str = []

        for arg in func.get("args", []):

            if arg.get("type"):
                args_str.append(
                    f"{arg['name']}: {arg['type']}"
                )
            else:
                args_str.append(arg["name"])

        args_formatted = ", ".join(args_str)

        ret = (
            f" -> {func['return_type']}"
            if func.get("return_type")
            else ""
        )

        print(
            f"\n{idx}. {func['name']}({args_formatted}){ret}"
        )

        route_suffix = (
            "  [background]" if _is_background_function(func["name"]) else ""
        )

        print(
            f"   Route: POST /{func['name']}{route_suffix}"
        )

        print(
            f"   Example Payload: {func.get('example_payload', {})}"
        )

        print(
            f"   Example Response: {func.get('example_response', {})}"
        )

    print("\nDependencies:")
    print("-" * 20)

    for dep in sorted(all_imports):
        print(f"- {dep}")

    generated_path = Path(output_dir)

    generated_files = []

    if generated_path.is_dir():

        for root, _, files in os.walk(generated_path):

            for f in files:

                rel = (
                    Path(root)
                    .relative_to(generated_path)
                    / f
                )

                generated_files.append(str(rel))

    print("\nGenerated Files:")
    print("-" * 20)

    for gf in sorted(generated_files):
        print(f"- {gf}")


def inspect_notebook_data(
    notebook_path,
    output_dir="generated"
):
    """
    Return notebook metadata as JSON-serializable data.
    Perfect for FastAPI endpoints and frontend dashboards.
    """

    notebook = load_notebook(notebook_path)

    code_cells = extract_code_cells(notebook)

    all_functions = []
    all_imports = set()

    for cell in code_cells:

        funcs = extract_functions_from_code(cell)
        imports = extract_imports_from_code(cell)

        all_functions.extend(funcs)
        all_imports.update(imports)

    all_functions = deduplicate_functions_by_name(all_functions)

    generated_path = Path(output_dir)

    generated_files = []

    if generated_path.is_dir():

        for root, _, files in os.walk(generated_path):

            for f in files:

                rel = (
                    Path(root)
                    .relative_to(generated_path)
                    / f
                )

                generated_files.append(str(rel))

    return {
        "functions": all_functions,
        "dependencies": sorted(
            list(all_imports)
        ),
        "generated_files": sorted(
            generated_files
        ),
        "reserved_name_conflicts": _reserved_name_conflicts(all_functions),
        "endpoints": _endpoint_metadata(all_functions),
    }


def print_compile_summary(notebook_path, output_dir="generated"):
    """Print what compiling `notebook_path` into `output_dir` actually
    produced: its endpoints (flagging background/task_id-based ones the
    same way POST /api/compile's "endpoints" field does) and third-party
    dependencies.

    Shared by the CLI's `compile` command and `serve`'s initial compile
    and every hot-recompile it triggers. Before this existed for `serve`,
    a live dev session gave no feedback at all about what a recompile had
    actually changed -- just "Recompilation complete." -- even though a
    fast, informative feedback loop after every save is the entire point
    of running a live server in the first place. `compile` had the same
    gap until this was first added there and later reused here.
    """
    data = inspect_notebook_data(
        notebook_path=notebook_path, output_dir=str(output_dir)
    )

    functions = data["functions"]

    print(f"\nGenerated {len(functions)} endpoint(s):")

    for func in functions:
        name = func["name"]
        suffix = "  [background]" if _is_background_function(name) else ""
        print(f"  POST /{name}{suffix}")

    if data["dependencies"]:
        print(f"\nDependencies: {', '.join(data['dependencies'])}")