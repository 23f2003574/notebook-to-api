import os
from pathlib import Path

from backend.compiler import COMPILE_METADATA_FILENAME, STANDARD_LIBS

from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells,
)

from backend.parser.ast_parser import (
    extract_functions_from_code,
    extract_imports_from_code,
    extract_skipped_functions_from_code,
    deduplicate_functions_by_name,
)

from backend.generator.api_generator import (
    LONG_RUNNING_KEYWORDS,
    RESERVED_INFRASTRUCTURE_NAMES,
)

# Directory names to exclude when walking an output directory for
# generated_files (below) and, via the same set, GET /api/download's zip
# archive (see backend/routes/upload.py). __pycache__ is created by Python
# itself the first time the compiled app or its runtime module gets
# imported (e.g. by `serve`'s uvicorn subprocess, `export-openapi`, or a
# test suite) -- it is not part of what compile_notebook_to_api actually
# wrote, and its .pyc filenames are tied to the interpreter that happened
# to import it (e.g. "app.cpython-314.pyc"), so they vary across machines
# and Python versions for the exact same compiled output. Left unfiltered,
# this polluted both `inspect`'s generated-files listing and the
# downloadable app bundle with a non-deliverable, non-deterministic
# implementation artifact that has nothing to do with what was actually
# compiled (confirmed against this repo's own generated/ directory, which
# already had a __pycache__ picked up by both).
EXCLUDED_GENERATED_DIR_NAMES = frozenset({"__pycache__"})

# File names to exclude when walking an output directory for generated_files
# (below) and, via the same set, GET /api/download's zip archive and GET
# /api/generated/{filename}'s preview (see backend/routes/upload.py), and
# the generated Dockerfile's .dockerignore (see
# generator/docker_generator.py, which excludes this same filename by its
# own literal copy of it -- it can't import COMPILE_METADATA_FILENAME
# without introducing a circular import, since compiler.py already imports
# docker_generator.py).
#
# .compile_metadata.json (write_compile_metadata, backend/compiler.py) is
# pure dashboard-internal bookkeeping -- read only by
# list_notebooks/_currently_compiled_notebook_metadata in routes/upload.py
# to answer "which uploaded notebook produced what's currently compiled" --
# never by the compiled app itself at runtime, exactly like the
# openapi.json/openapi.yaml/sdk/ exports .dockerignore already excludes for
# that same reason (see generate_dockerignore's own docstring). Left
# unfiltered here, it was previously baked into every `deploy`/`docker
# build`, downloadable via GET /api/download, and readable via GET
# /api/generated/.compile_metadata.json -- leaking the source notebook's
# *absolute filesystem path on the compiling server* (its "source_notebook"
# field) into a deployable image and a client-facing API response, neither
# of which has any business exposing server-side filesystem layout.
EXCLUDED_GENERATED_FILE_NAMES = frozenset({COMPILE_METADATA_FILENAME})


def _list_generated_files(output_dir):
    """Files under `output_dir`, relative to it, excluding
    EXCLUDED_GENERATED_DIR_NAMES subtrees and EXCLUDED_GENERATED_FILE_NAMES
    files. Shared by inspect_notebook and inspect_notebook_data so their
    "Generated Files" listings can't drift from each other.
    """
    generated_path = Path(output_dir)

    generated_files = []

    if generated_path.is_dir():

        for root, dirs, files in os.walk(generated_path):

            dirs[:] = [
                d for d in dirs if d not in EXCLUDED_GENERATED_DIR_NAMES
            ]

            for f in files:

                if f in EXCLUDED_GENERATED_FILE_NAMES:
                    continue

                rel = (
                    Path(root)
                    .relative_to(generated_path)
                    / f
                )

                generated_files.append(str(rel))

    return generated_files


def _third_party_dependencies(all_imports):
    """`all_imports` (raw import names collected via
    extract_imports_from_code) filtered down to the ones that will
    actually end up pinned in requirements.txt by write_requirements
    (backend/compiler.py), which applies this exact same STANDARD_LIBS
    filter to what it writes.

    Before this, inspect_notebook/inspect_notebook_data's "dependencies"
    listed every import a notebook made, standard-library ones included
    (e.g. "os", "json", "sys") -- confirmed misleading: a notebook doing
    `import os` and `import pandas` had both `inspect` and, after an
    actual compile, `compile`/`serve`/`deploy`'s own print_compile_summary
    (below) report "Dependencies: json, os, pandas", even though
    requirements.txt -- and from there the Docker image `deploy`/`docker
    build` would actually ship -- only ever contained "pandas". A
    notebook author reading either "preview what compiling will do" or
    "here's what this compile just produced" had no way to tell which of
    their imports would actually be installed without separately knowing
    which of them happen to be standard-library.
    """
    return sorted(imp for imp in all_imports if imp not in STANDARD_LIBS)


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


def _aggregate_skipped_functions(code_cells, exposed_function_names):
    """Skipped-function reports (see extract_skipped_functions_from_code)
    across every cell in `code_cells`, deduplicated by name and with any
    name that ultimately made it into `exposed_function_names` filtered
    back out.

    That last part matters because deduplicate_functions_by_name lets a
    later cell's clean redefinition of a name override an earlier cell's
    unsupported one (e.g. a `**kwargs` version fixed up and re-run under
    the same name) -- exactly what running the whole notebook top to
    bottom in a single kernel would do. Without this filter, a name that
    ended up perfectly fine would still be reported as skipped because of
    what an earlier, superseded cell once looked like.
    """
    skipped_by_name = {}

    for cell in code_cells:
        for skipped in extract_skipped_functions_from_code(cell):
            skipped_by_name[skipped["name"]] = skipped

    return [
        skipped
        for name, skipped in sorted(skipped_by_name.items())
        if name not in exposed_function_names
    ]


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

    skipped_functions = _aggregate_skipped_functions(
        code_cells, {func["name"] for func in all_functions}
    )

    print("\nNotebook Analysis Report")
    print("=" * 30)

    if reserved_name_conflicts:
        print("\n⚠ Reserved Name Conflicts (compilation will fail):")
        print("-" * 20)
        for name in reserved_name_conflicts:
            print(f"- {name}")

    if skipped_functions:
        print("\n⚠ Skipped Functions (no endpoint will be generated):")
        print("-" * 20)
        for skipped in skipped_functions:
            print(f"- {skipped['name']}: {skipped['reason']}")

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

        # A notebook function's own docstring already becomes its
        # compiled endpoint's OpenAPI description (see api_generator.py)
        # -- but `inspect` is this tool's own "preview what compiling
        # this notebook will do" report, and never showed it at all, even
        # though inspect_notebook_data's "functions" field (and `inspect
        # --json`) already carries it. A notebook author checking
        # `inspect` to confirm their documentation looks right before
        # compiling had no way to see it here.
        docstring = func.get("docstring")

        if docstring:
            for line in docstring.split("\n"):
                print(f"   {line}" if line else "")

        print(
            f"   Example Payload: {func.get('example_payload', {})}"
        )

        print(
            f"   Example Response: {func.get('example_response', {})}"
        )

    print("\nDependencies:")
    print("-" * 20)

    for dep in _third_party_dependencies(all_imports):
        print(f"- {dep}")

    generated_files = _list_generated_files(output_dir)

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

    return {
        "functions": all_functions,
        "dependencies": _third_party_dependencies(all_imports),
        "generated_files": sorted(
            _list_generated_files(output_dir)
        ),
        "reserved_name_conflicts": _reserved_name_conflicts(all_functions),
        "endpoints": _endpoint_metadata(all_functions),
        "skipped_functions": _aggregate_skipped_functions(
            code_cells, {func["name"] for func in all_functions}
        ),
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

    if data["skipped_functions"]:
        print(
            f"\nSkipped {len(data['skipped_functions'])} function(s) "
            "(no endpoint generated):"
        )
        for skipped in data["skipped_functions"]:
            print(f"  {skipped['name']}: {skipped['reason']}")