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

from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES


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

        print(
            f"   Route: POST /{func['name']}"
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
    }