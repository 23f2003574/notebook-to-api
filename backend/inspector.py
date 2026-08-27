import json
import os
from pathlib import Path

from backend.compiler import (
    COMPILE_METADATA_FILENAME,
    _extract_excluded_imports,
    _filter_functions_by_name,
    distribution_name_for_import,
    STANDARD_LIBS,
)

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


def list_generated_files(output_dir):
    """Public, sorted wrapper around _list_generated_files.

    Used directly by GET /api/generated (backend/routes/upload.py) to list
    what's currently sitting in an output directory without needing a
    notebook_path to inspect alongside it -- unlike inspect_notebook and
    inspect_notebook_data above, both of which require a notebook to
    inspect and only report generated_files as one field of that larger
    report. GET /api/generated has no notebook to parse at all (the whole
    point is listing what's compiled even after its source notebook is
    gone -- see DELETE /api/notebooks/{filename}'s "was_currently_compiled"
    flag), so it calls straight through to this instead.
    """
    return sorted(_list_generated_files(output_dir))


def _third_party_dependencies(all_imports):
    """`all_imports` (raw import names collected via
    extract_imports_from_code) filtered down to the ones that will
    actually end up pinned in requirements.txt by write_requirements
    (backend/compiler.py), and resolved to the exact same distribution
    names write_requirements itself pins -- both apply this identical
    STANDARD_LIBS filter and distribution_name_for_import resolution to
    what write_requirements writes, so this can't drift from it.

    Before the STANDARD_LIBS filter, inspect_notebook/inspect_notebook_data's
    "dependencies" listed every import a notebook made, standard-library
    ones included (e.g. "os", "json", "sys") -- confirmed misleading: a
    notebook doing `import os` and `import pandas` had both `inspect` and,
    after an actual compile, `compile`/`serve`/`deploy`'s own
    print_compile_summary (below) report "Dependencies: json, os, pandas",
    even though requirements.txt -- and from there the Docker image
    `deploy`/`docker build` would actually ship -- only ever contained
    "pandas".

    Before the distribution_name_for_import resolution, added later, the
    identical drift reopened for a different reason: a notebook's `import`
    statement names a *module*, not necessarily the PyPI *distribution*
    that provides it (`import multipart` is provided by "python-multipart",
    `import cv2` by "opencv-python", ...) -- write_requirements was fixed
    to resolve and pin the real distribution name, but this function kept
    returning the raw import name unchanged. Confirmed: compiling a
    notebook importing "multipart" wrote "python-multipart==<version>" to
    requirements.txt, while `compile --json`'s own "dependencies" field
    (built from this function) still reported "multipart" -- a name that
    appears nowhere in the requirements.txt that same compile just
    produced, and isn't installable via pip on its own. A notebook author
    reading either "preview what compiling will do" or "here's what this
    compile just produced" had no way to tell which of their imports
    would actually be installed, or under what name, without separately
    reading requirements.txt themselves.
    """
    return sorted({
        distribution_name_for_import(imp)
        for imp in all_imports
        if imp not in STANDARD_LIBS
    })


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

    # Same "# notebook-to-api: exclude <import-name>" directive
    # extract_third_party_imports (backend/compiler.py) already applies
    # before write_requirements pins anything -- without this, an import
    # a notebook author explicitly opted out of requirements.txt would
    # still show up here as a "dependency", even though it's never
    # actually pinned by an ensuing compile.
    all_imports -= _extract_excluded_imports(code_cells)

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

    # See the identical comment in inspect_notebook above -- keeps this
    # "dependencies" field (POST /api/inspect, POST /api/validate, POST
    # /api/compile's own response, and print_compile_summary below, which
    # all read it through this one function) from drifting out of sync
    # with what write_requirements/extract_third_party_imports
    # (backend/compiler.py) actually excludes.
    all_imports -= _extract_excluded_imports(code_cells)

    return {
        "functions": all_functions,
        "dependencies": _third_party_dependencies(all_imports),
        "generated_files": list_generated_files(output_dir),
        "reserved_name_conflicts": _reserved_name_conflicts(all_functions),
        "endpoints": _endpoint_metadata(all_functions),
        "skipped_functions": _aggregate_skipped_functions(
            code_cells, {func["name"] for func in all_functions}
        ),
    }


def print_compile_summary(notebook_path, output_dir="generated", only=None, exclude=None):
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

    `only`/`exclude`, when given, restrict which functions are reported
    here to whichever ones _filter_functions_by_name (backend/compiler.py)
    actually left compiled -- inspect_notebook_data below re-parses the
    notebook fresh and has no idea a compile that just ran was restricted
    at all, so without this, `compile --only add` would still report an
    endpoint for every *other* function the notebook defines, claiming
    endpoints exist for functions the compiled app doesn't actually have.
    """
    data = inspect_notebook_data(
        notebook_path=notebook_path, output_dir=str(output_dir)
    )

    functions = data["functions"]

    if only or exclude:
        functions = _filter_functions_by_name(functions, only, exclude)

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


def _extract_notebook_functions(notebook_path):
    """The same extract-every-cell/deduplicate pipeline inspect_notebook
    and inspect_notebook_data (above) already run, on their way to their
    own "functions" field -- factored out here for diff_notebook_functions
    below, which only ever needs this half of what those two compute (no
    dependencies, generated_files, or reserved_name_conflicts to diff two
    notebooks' function signatures against each other).
    """
    notebook = load_notebook(notebook_path)

    code_cells = extract_code_cells(notebook)

    all_functions = []

    for cell in code_cells:
        all_functions.extend(extract_functions_from_code(cell))

    return deduplicate_functions_by_name(all_functions)


def _function_signature_key(func):
    """Comparable signature summary for a function dict (as produced by
    extract_functions_from_code) -- used by diff_notebook_functions below
    to decide whether a function present in both notebooks actually
    changed, or is merely present in both untouched.

    Deliberately excludes "docstring", "example_payload", and
    "example_response": those affect the endpoint's *documentation*, not
    its actual request/response contract, so an edit to just a
    function's docstring shouldn't be reported as a breaking change to
    its signature.
    """
    return (
        func.get("args", []),
        func.get("return_type"),
        func.get("is_async", False),
    )


def diff_notebook_functions(old_notebook_path, new_notebook_path):
    """Compare the top-level functions two notebooks would each compile
    into endpoints, without actually compiling either one.

    Before this, the only way to tell whether editing a notebook changed
    its compiled API's surface -- a new endpoint, a removed one, or an
    existing one's request/response contract changing in a way that could
    break an existing caller -- was to compile both versions and diff
    app.py by eye. Generated code changes shape for plenty of reasons that
    have nothing to do with the actual API contract (formatting, internal
    ordering, ...), making a genuine "did this endpoint's request shape
    change" question much harder to answer than it needs to be. This
    compares just the extracted function signatures instead -- the same
    per-function shape inspect_notebook_data's own "functions" field
    already exposes for a single notebook -- so a CI check (or a
    developer reviewing a notebook change before merging or deploying it)
    can answer that directly, against two arbitrary notebook paths (an
    old revision checked out at another path, a previous upload's
    downloaded copy, ...), with no compile step involved at all.

    Returns {"added", "removed", "changed", "unchanged"}:
      - "added": full function entries present only in new_notebook_path.
      - "removed": full function entries present only in
        old_notebook_path.
      - "changed": functions present in both, sorted by name, each as
        {"name", "old", "new"} (that function's own full entry from each
        notebook) wherever its args, return type, or sync/async-ness
        differ (see _function_signature_key) -- a docstring-only edit
        does not count as "changed".
      - "unchanged": names present in both with an identical signature.
    """
    old_functions = {
        func["name"]: func
        for func in _extract_notebook_functions(old_notebook_path)
    }
    new_functions = {
        func["name"]: func
        for func in _extract_notebook_functions(new_notebook_path)
    }

    old_names = set(old_functions)
    new_names = set(new_functions)

    added_names = sorted(new_names - old_names)
    removed_names = sorted(old_names - new_names)

    changed = []
    unchanged = []

    for name in sorted(old_names & new_names):

        old_func = old_functions[name]
        new_func = new_functions[name]

        if _function_signature_key(old_func) != _function_signature_key(new_func):
            changed.append({"name": name, "old": old_func, "new": new_func})
        else:
            unchanged.append(name)

    return {
        "added": [new_functions[name] for name in added_names],
        "removed": [old_functions[name] for name in removed_names],
        "changed": changed,
        "unchanged": unchanged,
    }


def print_notebook_diff(diff):
    """Print diff_notebook_functions' own report in the same
    human-readable style print_compile_summary/inspect_notebook already
    use elsewhere in this file.
    """
    if diff["added"]:
        print(f"\n+ Added {len(diff['added'])} endpoint(s):")
        for func in diff["added"]:
            print(f"  + POST /{func['name']}")

    if diff["removed"]:
        print(f"\n- Removed {len(diff['removed'])} endpoint(s):")
        for func in diff["removed"]:
            print(f"  - POST /{func['name']}")

    if diff["changed"]:
        print(f"\n~ Changed {len(diff['changed'])} endpoint(s):")
        for entry in diff["changed"]:
            print(f"  ~ POST /{entry['name']}")

    if not (diff["added"] or diff["removed"] or diff["changed"]):
        print("\nNo changes to the compiled API surface.")

# The generated app's own default API key (see write_app_config /
# verify_api_key in generator/api_generator.py: `API_KEYS` defaults to
# os.getenv("NOTEBOOK_API_KEY", "notebook-to-api-dev-key")) -- duplicated
# here as a literal, not imported, since api_generator.py only ever
# embeds it as a string inside generated source lines, never as an
# importable constant of its own. The same soft-coupling
# EXCLUDED_GENERATED_FILE_NAMES's own docstring above already accepts for
# generator/docker_generator.py's identical inability to import
# COMPILE_METADATA_FILENAME without a circular import.
DEFAULT_DEV_API_KEY = "notebook-to-api-dev-key"


def generate_curl_commands(
    notebook_path, host="localhost", port=8000, api_key=None,
    only=None, exclude=None,
):
    """A ready-to-run `curl` command for every function `notebook_path`
    would compile into an endpoint, using each function's own
    "example_payload" (see inspect_notebook_data's "functions" field) as
    the request body.

    Before this, trying out a notebook's compiled API meant either
    hand-writing a curl invocation (or opening a separate HTTP client)
    per endpoint -- correctly guessing its JSON body shape and, easy to
    miss, that every generated endpoint requires an `X-API-Key` header at
    all (see verify_api_key, generator/api_generator.py) -- or compiling
    first and reading /docs. This needs no compile step at all: it works
    directly from the same notebook analysis `inspect`/POST /api/inspect
    already do, so a caller can preview exactly what they'll be able to
    call before ever running `compile`.

    Functions with a reserved-name collision (see
    _reserved_name_conflicts above) are skipped -- generate_fastapi_code
    refuses to compile a notebook containing one at all, so a curl
    command hitting that path would never actually resolve to anything.

    A background/task_id-based function's own command is commented to
    note that its POST only returns {"task_id": ...}, not the actual
    result -- unlike a synchronous endpoint's command, there's no way to
    know the task_id ahead of actually running it, so this can't also
    generate the GET /tasks/{task_id} follow-up command the way it can
    for everything else.

    `api_key` defaults to DEFAULT_DEV_API_KEY -- the generated app's own
    default when NOTEBOOK_API_KEY isn't set -- so the emitted commands
    work out of the box against a locally `serve`d app with no
    configuration; a caller who has set NOTEBOOK_API_KEY to something
    else needs to pass the matching value here instead.

    `only`/`exclude` restrict which functions get a command at all, via
    the exact same _filter_functions_by_name (backend/compiler.py) every
    other function-selecting entry point here already goes through
    (`compile`, `deploy`, `serve`, `watch`, POST /api/compile, POST
    /api/app-preview). Before this, `export-curl`/`remote-curl`/POST
    /api/curl-preview always emitted a command for *every* function a
    notebook defines, even one a caller's own --only/--exclude compile
    would never actually expose as an endpoint -- silently wrong output
    for anyone previewing curl commands for a notebook they intend to
    compile with either flag, the exact "can't drift from what an actual
    compile would produce" guarantee every sibling preview endpoint in
    this file already gives. Raises the identical ValueError
    _filter_functions_by_name itself raises for both-given or an
    unrecognized function name.

    Returns a list of ready-to-paste (or execute) multi-line command
    strings, one per function, in the same order inspect_notebook_data's
    own "functions" field returns them.
    """
    if api_key is None:
        api_key = DEFAULT_DEV_API_KEY

    data = inspect_notebook_data(notebook_path)

    functions = _filter_functions_by_name(data["functions"], only, exclude)

    reserved_names = set(data["reserved_name_conflicts"])

    base_url = f"http://{host}:{port}"

    commands = []

    for func in functions:

        name = func["name"]

        if name in reserved_names:
            continue

        payload = json.dumps(func.get("example_payload", {}))

        if _is_background_function(name):
            comment = (
                f'# {name} (background task -- POST only returns '
                f'{{"task_id": ...}}; poll GET /tasks/{{task_id}} for the '
                f"actual result)"
            )
        else:
            comment = f"# {name}"

        command = (
            f"{comment}\n"
            f"curl -X POST {base_url}/{name} \\\n"
            f'  -H "Content-Type: application/json" \\\n'
            f'  -H "X-API-Key: {api_key}" \\\n'
            f"  -d '{payload}'"
        )

        commands.append(command)

    return commands
