import datetime
import hashlib
import importlib.metadata
import json
import keyword
import os
import shutil
import sys
import pathlib
import threading

from pathlib import Path

# sys.stdlib_module_names (Python 3.10+) is the authoritative list of
# standard-library module names. The previous hand-picked set of 12 names
# missed the vast majority of the standard library (asyncio, random,
# logging, subprocess, csv, sqlite3, uuid, hashlib, threading, ...), so any
# notebook using one of them got that name written into requirements.txt
# as if it were a third-party PyPI package. That's not just noise: PyPI
# has real (unofficial, unrelated) packages published under some stdlib
# names -- e.g. `pip install asyncio` installs a bogus package that
# shadows the built-in module -- so this could actively break the
# generated app rather than just add a redundant line.
STANDARD_LIBS = set(sys.stdlib_module_names)

# Ensure project root is in sys.path for proper imports
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells
)

from backend.parser.ast_parser import (
    extract_functions_from_code,
    extract_imports_from_code,
    is_parseable_python,
    deduplicate_functions_by_name
)

from backend.generator.api_generator import (
    generate_fastapi_code,
    write_generated_api
)

from backend.generator.docker_generator import (
    generate_dockerfile,
    generate_dockerignore
)


def package_name_for_output_dir(output_dir):
    """The generated app imports its runtime module as
    `<package_name>.runtime.notebook_module`, so the output directory's
    basename must double as a valid Python package name. This was
    previously just hardcoded to "generated" everywhere, which silently
    broke the documented --output flag for any other directory: the
    runtime module ended up written to a fixed generated/runtime/ path
    while app.py (written wherever --output pointed) still imported from
    it by the fixed name "generated", regardless of where it actually
    landed on disk.
    """
    name = os.path.basename(os.path.normpath(output_dir))

    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(
            f"Output directory {output_dir!r} (basename {name!r}) can't be "
            "used as a Python package name for the generated app's "
            "`import <name>.runtime.notebook_module` statement. Choose an "
            "--output directory whose final path segment is a valid Python "
            "identifier (letters, digits, underscores; not starting with a "
            "digit; not a reserved keyword like 'import')."
        )

    # Confirmed exploitable: `--output json` (or os/sys/time/re/... --
    # any real standard-library module name, already collected in
    # STANDARD_LIBS above for the identical name-collision hazard in
    # write_requirements) compiled without error, but the generated
    # app.py's `import json.runtime.notebook_module as notebook_module`
    # statement then resolved to the real, already-imported stdlib `json`
    # module instead of the locally compiled package -- Python's import
    # system finds and caches a standard-library module ahead of same-
    # named packages under the working directory. This isn't just a
    # cosmetic naming clash: `python -m uvicorn json.app:app` (what
    # `serve`, the generated Dockerfile's CMD, and any real deployment
    # all run) fails outright with "No module named 'json.app'" -- the
    # generated app is entirely unusable, with the failure only ever
    # surfacing later, disconnected from the --output choice that
    # actually caused it, and looking like a packaging bug rather than a
    # bad directory name.
    if name in STANDARD_LIBS:
        raise ValueError(
            f"Output directory {output_dir!r} (basename {name!r}) collides "
            f"with the Python standard library module {name!r} -- the "
            f"generated app's `import {name}.runtime.notebook_module` "
            "statement would resolve to that standard-library module "
            "instead of the locally compiled package. Choose a different "
            "--output directory whose final path segment isn't a standard "
            "library module name."
        )

    return name


def write_runtime_module(code_cells, output_dir):

    runtime_path = Path(output_dir) / "runtime" / "notebook_module.py"

    runtime_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    combined_code = "\n\n".join(code_cells)

    with open(runtime_path, "w", encoding="utf-8") as f:
        f.write(combined_code)

    print("Runtime module generated.")


def _pinned_requirement(package_name):
    """Return "package_name==version" if `package_name`'s version can be
    resolved in the environment compiling the notebook, else just
    `package_name` unchanged.

    Without pinning, every generated app's requirements.txt listed bare
    package names ("fastapi", "pandas", ...), so `pip install -r
    requirements.txt` resolved whichever release happened to be latest at
    *deploy* time -- not the version the notebook was actually compiled
    and tested against. A breaking release published upstream between
    compile time and deploy time would silently change the generated
    app's behavior (or break it outright) with nothing in the generated
    output to indicate what had changed. Falling back to the bare name
    when the package isn't installed in this environment (e.g. a
    notebook imports a third-party library the compiling host doesn't
    have) preserves the previous behavior instead of failing the whole
    compile over a dependency this tool has no way to introspect.
    """
    try:
        version = importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return package_name

    return f"{package_name}=={version}"


def compiling_python_version():
    """The "<major>.<minor>" Python version of the interpreter running this
    compile, e.g. "3.11".

    _pinned_requirement (above) resolves every dependency's pinned version
    from *this* interpreter's installed packages. A Docker base image
    running a different Python than the one that produced those pins can
    silently break `docker build`'s `pip install -r requirements.txt` the
    moment a pinned package's wheels don't cover that Python version, or
    fall back to a source build that behaves differently from what was
    actually resolved and tested locally. Passed straight through to
    generate_dockerfile (see compile_notebook_to_api below) so the base
    image always matches the interpreter that produced requirements.txt,
    instead of the Dockerfile hardcoding a fixed version unrelated to it.
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def write_requirements(imports, output_dir):

    requirements_path = os.path.join(
        output_dir,
        "requirements.txt"
    )

    # watchdog is a dependency of this tool's own `serve` command (it
    # watches the notebook file for changes to trigger a hot recompile --
    # see backend/serve.py). The generated app itself never imports it
    # (see generator/api_generator.py); every compiled app was shipping it
    # as dead weight in requirements.txt and, from there, in its Docker
    # image.
    core_dependencies = [
        "fastapi",
        "uvicorn",
        "pydantic",
    ]

    final_deps = sorted(
        set(list(imports) + core_dependencies)
    )

    pinned_deps = [_pinned_requirement(dep) for dep in final_deps]

    with open(requirements_path, "w", encoding="utf-8") as f:
        for dep in pinned_deps:
            f.write(dep + "\n")

    print(
        f"requirements.txt generated with dependencies: {pinned_deps}"
    )


COMPILE_METADATA_FILENAME = ".compile_metadata.json"

# Serializes compile_notebook_to_api's multi-file writes to a given
# output_dir (see its own docstring below), and is also held by any
# dashboard route that reads that same directory's compiled output --
# POST /api/deploy's Docker build, POST /api/export-openapi's import of
# the compiled app, and GET /api/download's zip of it (see
# routes/upload.py) -- so none of them can observe a directory
# mid-write, torn between an old and a new compile. A plain
# threading.Lock, not a per-output_dir one: every dashboard process
# already funnels all of these through a single GENERATED_DIR, and the
# CLI's own compile/inspect/serve/deploy commands each run in their own
# process with nothing else to contend with, so one process-wide lock is
# exactly the right granularity without adding a locking scheme keyed by
# path.
COMPILE_LOCK = threading.Lock()


def hash_notebook_file(notebook_path):
    """SHA-256 of `notebook_path`'s raw bytes, as a hex digest.

    Used to detect when the notebook that produced the current
    `generated/` output has since been modified (see
    write_compile_metadata below and
    list_notebooks/_currently_compiled_notebook_metadata in
    routes/upload.py) -- a content hash survives a touch/copy/re-upload
    that doesn't actually change the bytes, unlike comparing mtimes,
    which would flag those as "changed" even though nothing meaningful
    did.
    """
    hasher = hashlib.sha256()

    with open(notebook_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def write_compile_metadata(notebook_path, output_dir):
    """Record which notebook produced the app in `output_dir`, its
    content hash at that moment, and when.

    Without this, nothing -- on disk or via the API -- recorded which
    notebook a given `generated/` output actually came from. GET
    /api/notebooks could list every uploaded notebook, but had no way to
    say "this is the one currently reflected in generated/", so a
    dashboard frontend had to track that itself client-side: fragile
    (lost on refresh) and wrong the moment a second compile happens
    (from this browser tab or another) without it finding out.

    The content hash closes a related gap: even once "this is the
    currently-compiled notebook" was known, there was still no way to
    tell whether that notebook had since been edited and re-uploaded
    (e.g. via /api/upload?overwrite=true) *after* the compile that
    produced the current generated/ output -- silently leaving the
    served app stale relative to the notebook a caller might think it
    still matches.

    notebook_path is stored as an absolute path so it can be compared
    directly against a resolved upload path later (see
    list_notebooks/_currently_compiled_notebook_metadata in
    routes/upload.py) regardless of whether it was originally relative.
    """
    metadata_path = os.path.join(output_dir, COMPILE_METADATA_FILENAME)

    metadata = {
        "source_notebook": os.path.abspath(notebook_path),
        "source_notebook_sha256": hash_notebook_file(notebook_path),
        "compiled_at": (
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        ),
    }

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def clear_stale_export_artifacts(output_dir):
    """Remove a previous compile's exported openapi.json/openapi.yaml and
    sdk/ directory from `output_dir`, if present.

    POST /api/export-openapi and POST /api/export-sdk (and the CLI's
    export-openapi/export-sdk commands, by default -- see 354bb6c) always
    write into output_dir alongside the compiled app itself. Recompiling
    a notebook overwrites app.py, the runtime module, requirements.txt,
    and the Dockerfile -- but previously left any openapi.json/
    openapi.yaml/sdk/ already sitting in output_dir completely untouched,
    silently describing the *previous* compile's endpoints instead.
    Confirmed: compiling a notebook exposing `add`, exporting its schema,
    then recompiling to expose `multiply` instead (without re-exporting)
    left openapi.json still listing "/add" and omitting "/multiply"
    entirely -- exactly the mismatch GET /api/download's zip and GET
    /api/generated/openapi.json would then hand a caller alongside the
    freshly compiled app.py that no longer matches either of them.

    Only ever called from the successful-compile path in
    compile_notebook_to_api below (after generate_fastapi_code has
    already succeeded), never on a failed compile -- a notebook that
    fails to compile must leave a previous good compile's exports
    untouched too, for the same reason it already leaves app.py,
    requirements.txt, and everything else untouched (see the
    generate_fastapi_code comment below).
    """
    output_path = Path(output_dir)

    for stale_export_file in ("openapi.json", "openapi.yaml"):
        (output_path / stale_export_file).unlink(missing_ok=True)

    stale_sdk_dir = output_path / "sdk"

    if stale_sdk_dir.is_dir():
        shutil.rmtree(stale_sdk_dir)


def compile_notebook_to_api(
    notebook_path,
    output_path
):

    # compile_notebook_to_api writes several files to output_dir
    # (runtime module, requirements.txt, app.py, Dockerfile,
    # .dockerignore, .compile_metadata.json) one after another with no
    # atomicity across the set. Every dashboard route that can trigger
    # this -- POST /api/compile chief among them -- is declared a plain
    # `def`, not `async def` (see routes/upload.py), specifically so a
    # slow compile runs in FastAPI's worker threadpool instead of
    # blocking the single event loop -- but that means two overlapping
    # POST /api/compile calls (two browser tabs, a retry racing the
    # original request, ...) can now genuinely execute this function in
    # two different threads at once, both writing into the *same*
    # GENERATED_DIR. Without serializing them, their writes interleave:
    # output_dir can end up with, say, one notebook's runtime module
    # alongside a different notebook's app.py, expecting functions the
    # runtime module doesn't define -- a corrupted, mismatched compile
    # output neither request actually produced on its own, with nothing
    # to indicate it happened.
    with COMPILE_LOCK:

        print(f"Starting compilation for: {notebook_path}")

        output_dir = os.path.dirname(output_path)

        os.makedirs(output_dir, exist_ok=True)

        package_name = package_name_for_output_dir(output_dir)

        notebook = load_notebook(notebook_path)

        code_cells = [
            cell for cell in extract_code_cells(notebook)
            if is_parseable_python(cell)
        ]

        functions = []

        for cell in code_cells:

            funcs = extract_functions_from_code(cell)

            functions.extend(funcs)

        functions = deduplicate_functions_by_name(functions)

        # Generate the API code -- and let it raise (e.g.
        # ReservedFunctionNameError, generator/api_generator.py, for a
        # function name that collides with an identifier the generated app
        # itself defines) -- before writing anything to output_dir at all.
        # This used to run after write_runtime_module/write_requirements
        # below, so a notebook that failed this check still overwrote the
        # runtime module and requirements.txt from a previous successful
        # compile with content generated from the *failing* notebook, while
        # app.py, the Dockerfile, and .compile_metadata.json were left
        # untouched from that previous compile -- leaving output_dir in an
        # inconsistent state that matched neither the old nor the new
        # notebook (confirmed: recompiling a working app with a notebook that
        # has one reserved-name collision left its runtime module rewritten
        # to the broken notebook's code while app.py and
        # .compile_metadata.json still described the last working one).
        api_code = generate_fastapi_code(functions, package_name)

        # generate_fastapi_code succeeding means this compile is now
        # guaranteed to go through -- from here on the only failures left
        # are I/O errors, not this notebook's own content -- so it's now
        # safe to clear out any openapi.json/openapi.yaml/sdk/ left over
        # from a previous compile's export (see its own docstring for why
        # this can't just be left alone).
        clear_stale_export_artifacts(output_dir)

        write_runtime_module(code_cells, output_dir)

        imports = set()

        for cell in code_cells:

            imports.update(extract_imports_from_code(cell))

        filtered_imports = [
            imp for imp in imports
            if imp not in STANDARD_LIBS
        ]

        write_requirements(
            filtered_imports,
            output_dir
        )

        write_generated_api(
            api_code,
            output_path
        )

        dockerfile_path = os.path.join(
            output_dir,
            "Dockerfile"
        )

        generate_dockerfile(
            dockerfile_path, package_name, compiling_python_version()
        )

        dockerignore_path = os.path.join(
            output_dir,
            ".dockerignore"
        )

        generate_dockerignore(dockerignore_path)

        write_compile_metadata(notebook_path, output_dir)

        print(
            f"Successfully generated FastAPI app at: {output_path}"
        )


def compile_notebook(
    notebook_path,
    output_dir
):
    """
    Convenient wrapper for CLI.
    Generates the FastAPI app at <output_dir>/app.py.
    """

    output_path = os.path.join(
        output_dir,
        "app.py"
    )

    compile_notebook_to_api(
        notebook_path,
        output_path
    )