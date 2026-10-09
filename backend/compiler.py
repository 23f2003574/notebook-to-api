import ast
import datetime
import fnmatch
import functools
import hashlib
import importlib.metadata
import io
import json
import keyword
import os
import re
import shlex
import shutil
import sys
import pathlib
import threading
import tokenize

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

# This tool's own top-level package name -- derived from __name__ (this
# module's own fully-qualified name is "backend.compiler") rather than
# hardcoded, so it can't drift if this package is ever renamed. Used by
# package_name_for_output_dir's own-package collision check below.
THIS_TOOLS_OWN_PACKAGE_NAME = __name__.partition(".")[0]

# This tool's own version -- previously duplicated as two independent
# "0.1.0" string literals (backend/dashboard.py's own FastAPI(version=...)
# and its GET / root endpoint's own "version" field), with nothing
# enforcing they'd ever be bumped together, and no way for the CLI to
# report its own version at all (no --version flag existed, and
# backend/cli.py deliberately never imports from backend.dashboard --
# doing so would drag in that module's own FastAPI app and every
# unrelated subsystem router it mounts, just for a version string).
# backend/compiler.py is the one lightweight module both already import
# from at module level, so this is the one place a caller on either side
# reads it from -- bump this single constant and both dashboard.py's
# responses and the CLI's own --version follow automatically, with no way
# for them to drift out of sync with each other again.
NOTEBOOK_TO_API_VERSION = "0.1.0"

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
    _deprecation_sunset_date,
    GENERATED_APP_ENV_VARS,
    generate_fastapi_code,
    write_generated_api
)

from backend.generator.docker_generator import (
    generate_dockerfile,
    generate_dockerignore,
    generate_docker_compose,
    generate_env_example,
    generate_readme
)

from backend.generator.kubernetes_generator import (
    generate_kubernetes_manifest,
)


@functools.lru_cache(maxsize=1)
def _installed_packages_distributions():
    """Cached importlib.metadata.packages_distributions() result: a map of
    top-level import name -> list of distribution names that provide it,
    for every distribution installed in the interpreter compiling this
    notebook (e.g. "cv2" -> ["opencv-python"], "fastapi" -> ["fastapi"]).

    Reads each installed distribution's own metadata (effectively its
    top_level.txt) rather than searching sys.path for anything that
    merely happens to be importable right now -- unlike
    importlib.util.find_spec, it can't pick up a local directory that
    just looks like a package. That distinction matters here specifically
    because this tool's own previous output directories (e.g.
    "generated", "test_generated") are exactly that: real, on-disk Python
    packages sitting in this project's own directory tree the moment
    they've been compiled once. Using find_spec instead would have made
    this tool's own documented default, `--output generated`, start
    failing the very next time it ran (see
    _installed_third_party_package_names below, built from this same
    call).

    Cached (this scan costs ~100ms and re-reads every installed
    distribution's metadata each time) since the set of installed
    packages doesn't change over the lifetime of a single compiling
    process -- the CLI's compile/serve/deploy commands each run once per
    process anyway, and the dashboard's compile-on-every-request path
    would otherwise re-pay this cost on every single POST /api/compile.
    Kept as its own function (rather than having each caller below call
    importlib.metadata.packages_distributions() directly) so the
    underlying scan only ever runs once per process no matter how many of
    them need it.
    """
    return importlib.metadata.packages_distributions()


def _installed_third_party_package_names():
    """Top-level import names of every distribution installed in the
    interpreter compiling this notebook (e.g. "fastapi", "numpy",
    "pandas", ...), used by package_name_for_output_dir's collision check
    below.
    """
    return frozenset(_installed_packages_distributions().keys())


# Import names whose PyPI distribution has a *different* name, for when the
# compiling host doesn't have the package installed to read that from (the
# usual case: a dashboard host compiling a data-science notebook whose
# libraries it never installed). Without this, `import sklearn` was written
# to requirements.txt as "sklearn" -- a deprecated stub that makes `pip
# install` fail outright -- and `import PIL`/`cv2`/`bs4`/`yaml` as names that
# don't exist on PyPI at all, failing every `docker build` for the notebook.
KNOWN_IMPORT_TO_DISTRIBUTION = {
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "attr": "attrs",
    "jwt": "PyJWT",
    "IPython": "ipython",
    "Bio": "biopython",
    "fitz": "pymupdf",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "git": "GitPython",
    "serial": "pyserial",
    "usb": "pyusb",
    "zmq": "pyzmq",
    "Crypto": "pycryptodome",
    "nacl": "PyNaCl",
    "OpenSSL": "pyOpenSSL",
    "MySQLdb": "mysqlclient",
    "psycopg2": "psycopg2-binary",
    "magic": "python-magic",
    "umap": "umap-learn",
    "win32com": "pywin32",
    "wx": "wxPython",
    "mpl_toolkits": "matplotlib",
    "tensorflow_datasets": "tensorflow-datasets",
}


def distribution_name_for_import(import_name):
    """The actual PyPI distribution name that provides `import_name`, if
    it's satisfied by something installed in the environment compiling
    this notebook, else `import_name` unchanged.

    A notebook's own `import` statement names a *module*, not necessarily
    the PyPI *distribution* that provides it -- `pip install <name>` only
    works for the latter, and the two frequently differ: `import cv2` is
    provided by "opencv-python", `import yaml` by "PyYAML", `import
    dateutil` by "python-dateutil", and so on. Before this, write_requirements
    (below) wrote the raw import name straight into requirements.txt
    unchanged, so `pip install -r requirements.txt` -- and from there
    every `deploy`/`docker build` -- failed outright for any notebook
    using one of these: confirmed against this exact environment, a
    notebook doing `import dateutil` (satisfied here by the installed
    "python-dateutil") produced a requirements.txt literally containing
    "dateutil", not a real PyPI package name. _pinned_requirement's own
    importlib.metadata.version() lookup didn't catch this either -- it
    resolves *distribution* names, not import names, so it raised
    PackageNotFoundError for "dateutil" too and silently fell back to
    writing that same wrong, unpinned name, with nothing in the output to
    indicate anything was off.

    _installed_packages_distributions() above is the authoritative
    reverse mapping for this: it reads each installed distribution's own
    metadata to report which top-level import names it actually provides.
    On the rare case more than one installed distribution provides the
    same import name, picks one deterministically (sorted, first). Falls
    back to `import_name` unchanged if it isn't installed here at all --
    preserving _pinned_requirement's own existing fallback for a
    dependency this tool has no way to introspect, rather than guessing
    at a distribution name it can't confirm.
    """
    distributions = _installed_packages_distributions().get(import_name)

    if not distributions:
        # Not installed here: a well-known mismatch is still better than the
        # raw import name, which for these is never a valid PyPI package.
        return KNOWN_IMPORT_TO_DISTRIBUTION.get(import_name, import_name)

    return sorted(distributions)[0]


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

    # Same import-shadowing hazard as the standard-library check above,
    # but for this tool's own top-level package -- the one this very
    # function (backend/compiler.py) lives inside. Confirmed exploitable:
    # `--output backend` passed the isidentifier()/keyword/STANDARD_LIBS
    # checks fine and compiled without error, but the generated app.py's
    # `import backend.runtime.notebook_module` statement would then
    # resolve to this tool's own real "backend" package instead of the
    # locally compiled one. Neither check above catches this: "backend"
    # isn't a standard-library module, and
    # importlib.metadata.packages_distributions() (used by the
    # already-installed-package check just below) doesn't know about it
    # either -- this project was never `pip install`ed, so it has no
    # distribution metadata of its own, the identical reason this tool's
    # own prior --output dirs like "generated" don't trip that check (see
    # _installed_third_party_package_names's own docstring). Unlike an
    # ordinary third-party package, though, this collision isn't merely
    # *possible* -- backend/compiler.py (this very module) is part of the
    # "backend" package, so it is unconditionally already imported and in
    # sys.modules by the time this function ever runs, in every single
    # invocation of this tool: the CLI, the dashboard, and any process
    # that has imported backend.compiler at all. Checked here, ahead of
    # the installed-third-party-package scan below, since it's a plain
    # string comparison against a known constant rather than needing that
    # scan's own ~100ms metadata read to answer a question this already
    # answers for free.
    if name == THIS_TOOLS_OWN_PACKAGE_NAME:
        raise ValueError(
            f"Output directory {output_dir!r} (basename {name!r}) collides "
            f"with this tool's own top-level package {name!r} -- the "
            f"generated app's `import {name}.runtime.notebook_module` "
            "statement would resolve to this tool's own package instead of "
            "the locally compiled one. Choose a different --output "
            "directory whose final path segment isn't this tool's own "
            "package name."
        )

    # Same import-shadowing hazard as the standard-library check above,
    # but for an already-installed *third-party* package rather than one
    # built into the interpreter. Confirmed exploitable: `--output
    # fastapi` (or numpy/pandas/httpx/... any package actually `pip
    # install`ed in the compiling environment, discovered via
    # _installed_third_party_package_names() above) compiled without
    # error, but the generated app.py's `import
    # fastapi.runtime.notebook_module` statement then resolved to the
    # real installed `fastapi` package instead of the locally compiled
    # one -- reproduced against a real `python -m uvicorn fastapi.app:app`,
    # which fails outright with "Could not import module 'fastapi.app'"
    # since the real package has no such submodule. It's worse still when
    # the notebook itself imports the same package: the runtime module's
    # own `import fastapi` (or whichever package collided) would then
    # resolve to the local, generated package -- which has none of the
    # real library's actual content -- breaking the notebook's own code,
    # not just app.py's outer import.
    if name in _installed_third_party_package_names():
        raise ValueError(
            f"Output directory {output_dir!r} (basename {name!r}) collides "
            f"with the already-installed Python package {name!r} -- the "
            f"generated app's `import {name}.runtime.notebook_module` "
            "statement would resolve to that installed package instead of "
            "the locally compiled one. Choose a different --output "
            "directory whose final path segment isn't an installed "
            "package name."
        )

    return name


JUPYTER_BUILTIN_STUBS = {
    "display": (
        "def display(*objs, **kwargs):\n"
        "    return None\n"
    ),
    "get_ipython": (
        "def get_ipython():\n"
        "    return None\n"
    ),
}


def _jupyter_builtin_prelude(combined_code):
    """Stub definitions for the Jupyter-only builtins `combined_code` calls
    but never defines or imports.

    `display(df)` and `get_ipython()` are injected into a notebook's
    namespace by IPython, so they exist in Jupyter and nowhere else: a
    notebook that used either at top level (an ordinary `display(df)` to
    inspect a frame, or the idiomatic `if get_ipython() is not None:` guard)
    raised NameError the moment the compiled app imported its runtime
    module, taking every endpoint down. display() becomes a no-op and
    get_ipython() returns None -- IPython's own "not running under IPython"
    answer, so such guards take their non-IPython branch. A notebook that
    defines or imports either name itself is left untouched. Where the stubs
    go -- after any `from __future__` imports, which must stay first in the
    module -- is _with_jupyter_prelude's job.
    """
    try:
        tree = ast.parse(combined_code)
    except SyntaxError:
        return ""

    bound = set()
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Name):
            (used if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)

    stubs = [
        stub for name, stub in JUPYTER_BUILTIN_STUBS.items()
        if name in used and name not in bound
    ]
    if not stubs:
        return ""
    return "# Jupyter built-ins, stubbed outside Jupyter (notebook-to-api)\n" + "\n".join(stubs) + "\n\n"


_IPYTHON_SHELL_METHODS = frozenset({
    "run_line_magic", "run_cell_magic", "magic", "system", "getoutput", "run_cell",
})


def _neutralize_ipython_shell_calls(combined_code):
    """`combined_code` with each `get_ipython().run_line_magic(...)` (and
    `.run_cell_magic` / `.magic` / `.system` / `.getoutput` / `.run_cell`)
    call replaced by `None`, keeping the line count.

    That is how `jupytext` / `nbconvert --to python` write `%matplotlib
    inline` or `!pip install x` -- an unguarded call on get_ipython()'s
    result. Outside IPython the stub get_ipython() returns None, so every
    such line raised AttributeError on import and took the app down; the
    magic itself has nothing to do in a server, so the call becomes a
    no-op. Only the outermost call is replaced, and an unparsable source
    is returned untouched.
    """
    try:
        tree = ast.parse(combined_code)
    except SyntaxError:
        return combined_code

    spans = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _IPYTHON_SHELL_METHODS
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "get_ipython"
            and not node.func.value.args and not node.func.value.keywords
        ):
            spans.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
    if not spans:
        return combined_code

    spans.sort()
    outermost = []
    for span in spans:
        if outermost and (span[0], span[1]) < (outermost[-1][2], outermost[-1][3]):
            continue
        outermost.append(span)

    lines = [line.encode("utf-8") for line in combined_code.split("\n")]
    for start_line, start_col, end_line, end_col in reversed(outermost):
        head = lines[start_line - 1][:start_col]
        tail = lines[end_line - 1][end_col:]
        # Parenthesised, with the original newlines kept, so later line
        # numbers (and tracebacks) still match the notebook.
        replacement = [head + b"(None"] + [b""] * (end_line - start_line - 1)
        if end_line > start_line:
            replacement.append(b")" + tail)
        else:
            replacement[0] += b")" + tail
        lines[start_line - 1:end_line] = replacement
    return b"\n".join(lines).decode("utf-8")


def _with_jupyter_prelude(combined_code, extra_prelude=""):
    """`combined_code` with _jupyter_builtin_prelude's stubs inserted -- at the
    very top, or, for a notebook that opens with `from __future__` imports
    (optionally after a module docstring), right after the last of them:
    Python requires those to be the module's first statements, so a stub
    placed above them is a SyntaxError. Such a notebook used to get no stubs
    at all, and a top-level display(...) still crashed the app on import."""
    prelude = _jupyter_builtin_prelude(combined_code)
    if "def get_ipython" in prelude:
        combined_code = _neutralize_ipython_shell_calls(combined_code)
    prelude = extra_prelude + prelude
    if not prelude:
        return combined_code

    insert_after_line = 0
    try:
        body = ast.parse(combined_code).body
    except SyntaxError:
        return combined_code
    index = 0
    if (
        body and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)
    ):
        insert_after_line = body[0].end_lineno
        index = 1
    while (
        index < len(body) and isinstance(body[index], ast.ImportFrom)
        and body[index].module == "__future__"
    ):
        insert_after_line = body[index].end_lineno
        index += 1

    if insert_after_line == 0:
        return prelude + combined_code

    lines = combined_code.split("\n")
    return "\n".join(lines[:insert_after_line]) + "\n" + prelude + "\n".join(lines[insert_after_line:])


def _ship_local_modules(local_modules, runtime_dir):
    """Copy each local module/package into the runtime directory (replacing
    a stale copy from an earlier compile), so the compiled app -- and the
    Docker image, which copies the whole output directory -- carries them."""
    for name, source in local_modules.items():
        if isinstance(source, str):
            (runtime_dir / f"{name}.py").write_text(source, encoding="utf-8")
        elif source.is_dir():
            target = runtime_dir / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copyfile(source, runtime_dir / f"{name}.py")


# Which data files the last compile copied into the runtime directory, so the
# next one can remove those it no longer ships. Without it a deleted or
# renamed data file lived on in every later build: `os.listdir("data")` in the
# app still listed it, and the Docker image kept carrying it.
SHIPPED_DATA_MANIFEST = ".shipped_data.json"


def _sync_shipped_data_files(data_files, runtime_dir):
    """Copy `data_files` ({relative path: source}) into `runtime_dir` and
    delete the files a previous compile shipped that this one doesn't (plus
    any directories that leaves empty). Only paths recorded in the manifest
    are ever deleted, never anything outside `runtime_dir`, and never a .py
    file (a former `%run` script may now be a shipped local module)."""
    runtime_dir = Path(runtime_dir).resolve()
    manifest_path = runtime_dir / SHIPPED_DATA_MANIFEST
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(previous, list):
            previous = []
    except (OSError, ValueError):
        previous = []

    current = []
    for relative, source in (data_files or {}).items():
        target = runtime_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        current.append(Path(os.path.normpath(relative)).as_posix())

    for relative in previous:
        if not isinstance(relative, str) or relative in current:
            continue
        stale = (runtime_dir / relative).resolve()
        if runtime_dir not in stale.parents or stale.suffix == ".py" or not stale.is_file():
            continue
        stale.unlink()
        parent = stale.parent
        while parent != runtime_dir and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    if current:
        manifest_path.write_text(json.dumps(sorted(set(current)), indent=2) + "\n", encoding="utf-8")
    else:
        manifest_path.unlink(missing_ok=True)


_LOCAL_MODULES_PATH_PRELUDE = (
    "# Notebook-local modules ship beside this file (notebook-to-api)\n"
    "import os as _nb_os, sys as _nb_sys\n"
    "_nb_sys.path.insert(0, _nb_os.path.dirname(_nb_os.path.abspath(__file__)))\n\n"
)


# Data files the notebook reads at import time ship beside the runtime
# module; relative paths resolve against the working directory, so the
# import runs from there and the app's own working directory is restored
# afterwards.
_DATA_FILES_CHDIR_PRELUDE = (
    "# Import-time data files ship beside this file (notebook-to-api)\n"
    "import os as _nb_os2\n"
    "_nb_previous_cwd = _nb_os2.getcwd()\n"
    "_nb_os2.chdir(_nb_os2.path.dirname(_nb_os2.path.abspath(__file__)))\n\n"
)
_DATA_FILES_CHDIR_EPILOGUE = "_nb_os2.chdir(_nb_previous_cwd)\n"


def write_runtime_module(code_cells, output_dir, local_modules=None, data_files=None):

    runtime_path = Path(output_dir) / "runtime" / "notebook_module.py"

    runtime_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    rewritten_cells = _rewrite_request_time_reads(code_cells, data_files or {})
    reads_at_request_time = rewritten_cells != list(code_cells)
    combined_code = "\n\n".join(rewritten_cells)
    shipped_scripts = [p for p in _run_magic_scripts(code_cells) if p in (data_files or {})]
    if shipped_scripts:
        combined_code = _RUN_MAGIC_PATTERN.sub(
            lambda m: (
                f"_nb_run_script({_normalized_run_path(m.group('path'))!r})"
                if _normalized_run_path(m.group("path")) in shipped_scripts else m.group(0)
            ),
            combined_code,
        )
    extra_prelude = _env_magic_prelude(_env_magic_values(code_cells))
    if shipped_scripts:
        extra_prelude += _RUN_SCRIPT_HELPER
    if _uses_colab_userdata(code_cells):
        extra_prelude += _COLAB_USERDATA_SHIM
    if _uses_kaggle_secrets(code_cells):
        extra_prelude += _KAGGLE_SECRETS_SHIM
    if _uses_tqdm_notebook(code_cells):
        extra_prelude += _TQDM_NOTEBOOK_SHIM
    if local_modules:
        extra_prelude += _LOCAL_MODULES_PATH_PRELUDE
    if reads_at_request_time:
        extra_prelude += _DATA_PATH_HELPER
    if data_files:
        extra_prelude += _DATA_FILES_CHDIR_PRELUDE
    combined_code = _with_jupyter_prelude(combined_code, extra_prelude=extra_prelude)
    if data_files:
        combined_code = combined_code.rstrip("\n") + "\n\n" + _DATA_FILES_CHDIR_EPILOGUE
    if local_modules:
        _ship_local_modules(local_modules, runtime_path.parent)
    _sync_shipped_data_files(data_files, runtime_path.parent)

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

    The PEP 440 local version segment (everything from the first "+"
    onward, e.g. "2.1.0+cu121" for a CUDA-specific torch build,
    "0.1.0+dirty" or "0.1.0.dev3+g1a2b3c4" from a setuptools-scm/git-based
    build, or whatever an editable `pip install -e .` records) is stripped
    before pinning. PyPI rejects any upload whose version contains one --
    it exists specifically to distinguish a locally-modified build from
    the public release it's based on, never to be redistributed itself --
    so importlib.metadata.version() reporting one here means this exact
    "package_name==version" string can never resolve against PyPI.
    Confirmed reproduced: installing a package under a version string
    containing "+", then compiling a notebook that imports it, wrote that
    unresolvable pin straight into requirements.txt, and `pip install -r
    requirements.txt` -- and from there every `deploy`/`docker build` --
    failed outright with "No matching distribution found for
    <package>==<version>+<local>", even though the public version right
    before the "+" is very likely actually installable. This can affect
    any dependency in the pin list, not just an unusual one deliberately
    reproducing it: this tool's own compiling environment is exactly as
    likely to have a locally-built or editable-installed package as any
    other, and nothing before this caught it.
    """
    try:
        version = importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return package_name

    public_version = version.partition("+")[0]

    return f"{package_name}=={public_version}"


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


def _lines_inside_multiline_strings(cell):
    """1-indexed physical line numbers that fall strictly inside one of
    `cell`'s own multi-line string literals (a docstring, a template
    string, a block of embedded documentation) -- not the line the
    string itself opens on, since a directive-shaped comment can only
    ever match REQUIREMENT_DIRECTIVE_PATTERN/APT_REQUIREMENT_DIRECTIVE_
    PATTERN/EXCLUDE_DIRECTIVE_PATTERN below if the "#" is the very first
    non-whitespace character on its own line, which is impossible on a
    multi-line string's own opening line (that position is already
    occupied by the string's own opening quote, or by real code before
    it).

    Those three directive patterns are matched against a cell's raw
    source text directly (see their own docstrings for why -- a comment
    carries no meaning for `ast.parse` to preserve, so scanning the AST
    instead would simply never see it), with no awareness of whether the
    matched line is a real, live comment or merely lies inside a string
    literal's own text. Confirmed exploitable before this: a notebook
    author documenting this tool's own directive syntax inside a
    function's docstring -- an entirely ordinary way to explain a
    convention to teammates -- silently activated a real "requires"/
    "apt-requires"/"exclude" directive, corrupting requirements.txt or
    the generated Dockerfile (or silently dropping a real import from
    both) with no error anywhere, for a directive the author never
    intended to actually declare.

    Uses the standard library tokenizer to find each multi-line string's
    own line span, the same approach _lines_unsafe_for_magic_detection
    (backend/parser/notebook_parser.py) already takes for the identical
    "a magic-shaped line inside a string is just text, not code" problem
    -- but scoped to string spans alone, not also to bracket-continuation
    lines the way that function is: a "#" on a continuation line inside
    an open "("/"["/"{" is still a perfectly real Python comment (Python
    itself draws no distinction), so a directive placed there is not a
    false positive the way one inside a string is. Tolerant of an
    unparseable cell the same way that function is -- falls back to
    protecting nothing, since a genuinely malformed cell fails
    is_parseable_python before ever reaching any of this anyway.
    """
    unsafe_lines = set()

    try:

        for tok_type, _, (start_line, _), (end_line, _), _ in (
            tokenize.generate_tokens(io.StringIO(cell).readline)
        ):

            if (
                tok_type in (
                    tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)
                )
                and end_line > start_line
            ):
                unsafe_lines.update(range(start_line + 1, end_line + 1))

    except (tokenize.TokenError, SyntaxError, ValueError):
        return set()

    return unsafe_lines


# Recognizes a "# notebook-to-api: requires <spec>" comment directive
# anywhere in a code cell's raw source -- see
# _extract_explicit_requirements below for what it's for. Matched against
# each cell's raw text directly (not via the AST, unlike every other
# extraction step in this pipeline) since a comment carries no meaning
# for `ast.parse` to preserve in the first place; it's gone from the tree
# entirely. Requires the "#" to start the line (allowing leading
# whitespace, so it's usable indented inside a function body too) so an
# unrelated inline comment that merely happens to contain this phrase
# elsewhere in a line of code isn't matched by accident. A match whose
# own line falls inside a multi-line string literal (a docstring
# documenting this exact directive syntax, say) is discarded by
# _extract_explicit_requirements below via _lines_inside_multiline_strings
# -- see that function's own docstring for why.
REQUIREMENT_DIRECTIVE_PATTERN = re.compile(
    r"^\s*#\s*notebook-to-api:\s*requires\s+(?P<spec>\S.*)$",
    re.MULTILINE,
)


def _extract_explicit_requirements(code_cells):
    """Extra requirements.txt lines a notebook author declares explicitly
    via a "# notebook-to-api: requires <spec>" comment directive, one per
    line, anywhere in any code cell -- in the order first seen, with
    exact-duplicate lines removed.

    write_requirements (below) only ever pins whatever a notebook's own
    `import` statements resolve to (see distribution_name_for_import) --
    but that resolution depends entirely on what's importable in the
    environment doing the compiling. A dependency the notebook actually
    needs at runtime but that isn't importable there at all (one only
    ever imported dynamically via importlib rather than a top-level
    `import` a static scan can see, one that's only installed in the
    deploy target's own image and never the compiling machine, or simply
    a private package/VCS URL/extras spec this tool has no way to guess
    on its own) had no way to be added to requirements.txt at all.

    Each matched <spec> is written to requirements.txt exactly as given
    -- not resolved via distribution_name_for_import or re-pinned via
    _pinned_requirement, since a caller writing this directive is already
    stating precisely what belongs there, the same as a hand-written
    requirements.txt line would.

    Raises ValueError if two *different* specs name the same package (via
    _explicit_requirement_package_name below, normalized the identical
    way PyPI itself does -- see _normalize_distribution_name -- so
    "numpy"/"NumPy" and "python-dateutil"/"python_dateutil" are each
    recognized as the same package) -- e.g. one cell declaring
    "# notebook-to-api: requires numpy==1.24.0" and another
    "# notebook-to-api: requires numpy==1.26.0", commonly left behind
    after pinning a different version while iterating on a notebook.
    "seen"/exact-duplicate removal just above only catches the identical-
    line case; two distinct specs for the same package both survived it
    (and this function's own returned list) before this check existed,
    both then surviving resolve_requirements' own explicit-vs-auto-
    detected conflict resolution too (its own docstring explains that
    one) -- since resolve_requirements only ever drops an *auto-detected*
    import an explicit directive already names, it never had a reason to
    also dedupe explicit requirements against each other, that being this
    function's own job. Both lines landing side by side in
    requirements.txt is the identical "Double requirement given" pip
    failure resolve_requirements' own docstring already describes fixing
    for the auto-detected-vs-explicit case, just for two explicit
    directives instead -- confirmed exploitable before this check
    existed. Raised here (not in resolve_requirements, which has no way
    to tell an explicit spec's own conflicting sibling apart from an
    ordinary auto-detected/explicit collision it's already supposed to
    resolve) so the conflicting notebook fails this specific, actionable
    check -- rather than a raw pip error surfacing only much later, at
    `docker build` time, deep inside POST /api/deploy or the CLI's own
    `deploy`.
    """
    specs = []
    seen = set()
    spec_by_package_name = {}

    for cell in code_cells:

        unsafe_lines = _lines_inside_multiline_strings(cell)

        for match in REQUIREMENT_DIRECTIVE_PATTERN.finditer(cell):

            if cell.count("\n", 0, match.start()) + 1 in unsafe_lines:
                continue

            spec = match.group("spec").strip()

            if not spec or spec in seen:
                continue

            package_name = _explicit_requirement_package_name(spec)

            if package_name is not None:

                normalized_name = _normalize_distribution_name(package_name)
                conflicting_spec = spec_by_package_name.get(normalized_name)

                if conflicting_spec is not None:
                    raise ValueError(
                        "Conflicting '# notebook-to-api: requires' "
                        f"directives for '{package_name}': "
                        f"'{conflicting_spec}' and '{spec}' -- pip refuses "
                        "two requirements for the same package "
                        "('Double requirement given'). Remove or reconcile "
                        "one of them."
                    )

                spec_by_package_name[normalized_name] = spec

            seen.add(spec)
            specs.append(spec)

    # A notebook's own `!pip install ...` / `%pip install ...` cells say what
    # it needs -- including packages it never imports by name. An explicit
    # directive for the same package wins.
    for spec in _pip_install_specs(code_cells):
        package_name = _explicit_requirement_package_name(spec)
        if package_name is None or spec in seen:
            continue
        normalized_name = _normalize_distribution_name(package_name)
        if normalized_name in spec_by_package_name:
            continue
        spec_by_package_name[normalized_name] = spec
        seen.add(spec)
        specs.append(spec)

    return specs


_PIP_INSTALL_LINE_PATTERN = re.compile(
    # "pass  # !pip install x": an emptied block's filler (see
    # _fill_emptied_blocks, backend/parser/notebook_parser.py).
    r"^\s*(?:pass\s+)?#\s*[!%]\s*(?:python[\d.]*\s+-m\s+)?pip[\d.]*\s+install\s+(?P<args>.+?)\s*$",
    re.MULTILINE,
)

# pip options that take a value (the next token) rather than standing alone.
_PIP_OPTIONS_WITH_VALUE = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i",
    "--index-url", "--extra-index-url", "-f", "--find-links", "-t", "--target",
    "--prefix", "--root", "--platform", "--python-version", "--implementation",
    "--abi", "--only-binary", "--no-binary", "--progress-bar", "--timeout",
    "--retries", "--proxy", "--cert", "--trusted-host", "--src", "--upgrade-strategy",
})


# `pip install` / `apt-get install` written as lines of a `%%bash` / `%%sh`
# cell (whose whole body the parser comments out), not as `!` lines.
_SHELL_CELL_PIP_PATTERN = re.compile(
    r"^#\s*(?:python[\d.]*\s+-m\s+)?pip[\d.]*\s+install\s+(?P<args>.+?)\s*$",
    re.MULTILINE,
)
_SHELL_CELL_APT_PATTERN = re.compile(
    r"^#\s*(?:sudo\s+)?apt(?:-get)?\s+(?:-\S+\s+)*install\s+(?P<args>.+?)\s*$",
    re.MULTILINE,
)


def _shell_install_matches(cell, line_pattern, shell_cell_pattern):
    """`line_pattern`'s matches in `cell`, plus `shell_cell_pattern`'s when
    the cell is a shell cell (see _SHELL_CELL_PATTERN), in source order."""
    matches = list(line_pattern.finditer(cell))
    if _SHELL_CELL_PATTERN.match(cell.lstrip()):
        seen = {match.start() for match in matches}
        matches += [m for m in shell_cell_pattern.finditer(cell) if m.start() not in seen]
    return sorted(matches, key=lambda match: match.start())


def _pip_install_specs(code_cells):
    """The requirement specs named by `!pip install` / `%pip install` /
    `!python -m pip install` lines (the notebook parser turns each into a
    "# !pip install ..." comment), in first-seen order, one per package.

    Options (`-q`, `--upgrade`, `-r file`, `--index-url ...`) and local
    paths/URLs-without-a-name are skipped; a package named more than once
    keeps the first spec that carries a version constraint, never raising
    -- cells that install the same package twice are ordinary. Lines inside
    a multi-line string are ignored.
    """
    by_name = {}
    order = []

    for cell in code_cells:
        unsafe_lines = _lines_inside_multiline_strings(cell)
        for match in _shell_install_matches(cell, _PIP_INSTALL_LINE_PATTERN, _SHELL_CELL_PIP_PATTERN):
            if cell.count("\n", 0, match.start()) + 1 in unsafe_lines:
                continue
            try:
                tokens = shlex.split(match.group("args"), comments=True)
            except ValueError:
                continue

            skip_next = False
            for token in tokens:
                if skip_next:
                    skip_next = False
                    continue
                if token.startswith("-"):
                    if "=" not in token and token in _PIP_OPTIONS_WITH_VALUE:
                        skip_next = True
                    continue
                if token.startswith((".", "/", "~")) or token.endswith((".txt", ".whl", ".zip", ".tar.gz")):
                    continue
                package_name = _explicit_requirement_package_name(token)
                if package_name is None:
                    continue
                key = _normalize_distribution_name(package_name)
                has_version = any(op in token for op in ("==", ">=", "<=", "~=", "!=", ">", "<"))
                if key not in by_name:
                    by_name[key] = token
                    order.append(key)
                elif has_version and not any(
                    op in by_name[key] for op in ("==", ">=", "<=", "~=", "!=", ">", "<")
                ):
                    by_name[key] = token

    return [by_name[key] for key in order]


# Recognizes a "# notebook-to-api: apt-requires <package>" comment
# directive anywhere in a code cell's raw source -- see
# _extract_explicit_apt_packages below for what it's for. Same matching
# rules as REQUIREMENT_DIRECTIVE_PATTERN above (leading whitespace
# allowed, "#" must start the line, matched against raw cell text rather
# than the AST) for the identical reason: usable indented inside a
# function body, immune to an unrelated inline comment merely containing
# this phrase elsewhere in a line, and invisible to `ast.parse` in the
# first place. Also same string-literal-safety treatment: a match inside
# a multi-line string is discarded by _extract_explicit_apt_packages
# below via _lines_inside_multiline_strings.
APT_REQUIREMENT_DIRECTIVE_PATTERN = re.compile(
    r"^\s*#\s*notebook-to-api:\s*apt-requires\s+(?P<package>\S+)\s*$",
    re.MULTILINE,
)


def _extract_explicit_apt_packages(code_cells):
    """Debian/Ubuntu (apt) package names a notebook author declares
    explicitly via a "# notebook-to-api: apt-requires <package>" comment
    directive, one per line, anywhere in any code cell -- in the order
    first seen, with exact-duplicate lines removed.

    REQUIREMENT_DIRECTIVE_PATTERN's own "requires" directive (above)
    already lets a notebook author hand-declare an extra requirements.txt
    line this tool's own import-scanning can't infer on its own -- but a
    requirements.txt line is still just a PyPI package name; it has no
    way to express a *system* library or build tool a notebook's own
    dependency needs already present *inside the image* before `pip
    install` (or the notebook's own code) can succeed at all: `psycopg2`
    (not the `-binary` wheel many production deployments deliberately
    avoid) needs libpq-dev and a C compiler just to build; `mysqlclient`
    needs default-libmysqlclient-dev; `weasyprint`/`playwright` each need
    a handful of native shared libraries; `opencv-python`'s own wheel
    installs cleanly but segfaults the moment it's actually imported
    inside python:3.x-slim, which never ships libgl1 at all. Before this,
    a notebook needing any of these had exactly one path to a working
    image: hand-editing the generated Dockerfile after every single
    compile to add the missing `apt-get install`, since this tool's own
    generated one only ever ran `pip install` against a base image with
    nothing beyond Python itself.

    Each matched <package> is written into the generated Dockerfile's own
    `apt-get install` line exactly as given (see apt_install_content,
    backend/generator/docker_generator.py) -- including an explicit
    version pin ("libpq-dev=13.11-0+deb12u1"), which is valid apt syntax
    this tool has no reason to second-guess, the identical "copied
    through verbatim" treatment _extract_explicit_requirements' own
    directive already gets.

    Unlike _extract_explicit_requirements, raises nothing for two
    directives naming the same package with different version pins --
    apt-get itself simply installs whichever mention comes last, not the
    hard "Double requirement given" failure pip raises for the analogous
    case, so there is no equivalent failure here worth catching this
    early.
    """
    packages = []
    seen = set()

    for cell in code_cells:

        unsafe_lines = _lines_inside_multiline_strings(cell)

        for match in APT_REQUIREMENT_DIRECTIVE_PATTERN.finditer(cell):

            if cell.count("\n", 0, match.start()) + 1 in unsafe_lines:
                continue

            package = match.group("package").strip()

            if not package or package in seen:
                continue

            seen.add(package)
            packages.append(package)

    # `!apt-get install ...` lines in the notebook name the system libraries
    # it needed in Jupyter's environment.
    for package in _apt_install_packages(code_cells):
        if package not in seen:
            seen.add(package)
            packages.append(package)

    # ...and some imports need a system library the slim base image lacks.
    for package in _import_implied_apt_packages(code_cells):
        if package not in seen:
            seen.add(package)
            packages.append(package)

    return packages


# A Hugging Face Hub model id: "org/name", no path-like parts.
HUB_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*/[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_HUB_LOADER_NAMES = frozenset({"from_pretrained", "SentenceTransformer", "CrossEncoder"})
_HUB_ID_KEYWORDS = ("pretrained_model_name_or_path", "model_name_or_path", "model_name", "model")


def _hub_model_argument(call, name):
    """The literal model id a Hub loader call names, else None."""
    if name == "pipeline":
        nodes = call.args[1:2] + [kw.value for kw in call.keywords if kw.arg == "model"]
    else:
        nodes = call.args[:1] + [kw.value for kw in call.keywords if kw.arg in _HUB_ID_KEYWORDS]
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if HUB_MODEL_ID_PATTERN.match(value) and ".." not in value:
                return value
    return None


def hub_model_ids(code_cells):
    """Hugging Face Hub model ids (first-seen order, unique) the notebook
    loads by literal "org/name" id -- `AutoModel.from_pretrained(...)`,
    `pipeline("task", model=...)`, `SentenceTransformer(...)` -- anywhere,
    for the Dockerfile to prefetch at build time."""
    found = []
    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            _, name, _ = _call_label(node.func)
            if name not in _HUB_LOADER_NAMES and name != "pipeline":
                continue
            model = _hub_model_argument(node, name)
            if model and model not in found:
                found.append(model)
    return found


# spaCy pipeline packages ("en_core_web_sm", "xx_ent_wiki_sm") and NLTK
# data ids ("punkt", "averaged_perceptron_tagger_eng").
_SPACY_PIPELINE_PATTERN = re.compile(r"^[a-z]{2,3}_[a-z0-9]+_[a-z0-9]+_(sm|md|lg|trf)$")
# tiktoken encodings ("cl100k_base") and the model names it maps ("gpt-4o").
# gensim-data model names ("glove-wiki-gigaword-50", "word2vec-google-news-300").
_GENSIM_MODEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
# EasyOCR language codes ("en", "ch_sim").
_EASYOCR_LANG_PATTERN = re.compile(r"^[a-z]{2,3}(_[a-z]+)?$")
_TIKTOKEN_ENCODING_PATTERN = re.compile(r"^[a-z0-9_]+$")
_TIKTOKEN_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")
_NLTK_PACKAGE_PATTERN = re.compile(r"^[a-z][a-z0-9_.\-]*$")


def language_data_packages(code_cells):
    """{"spacy": [...], "nltk": [...]} (first-seen order, unique) for the
    spaCy pipelines a notebook loads by package name (`spacy.load(
    "en_core_web_sm")`) and the NLTK data it downloads (`nltk.download(
    "punkt")`, `nltk.download(["stopwords", "wordnet"])`), plus the
    tiktoken encodings it loads by name (`tiktoken.get_encoding(
    "cl100k_base")`, "tiktoken") or by model (`tiktoken.encoding_for_model(
    "gpt-4o")`, "tiktoken_models") and each literal language list an
    `easyocr.Reader(["en"])` loads models for ("easyocr") and the gensim-data
    models `gensim.downloader.load("glove-wiki-gigaword-50")` fetches
    ("gensim"), for the
    Dockerfile to install at build time (see language_data_content)."""
    found = {
        "spacy": [], "nltk": [], "tiktoken": [], "tiktoken_models": [], "easyocr": [],
        "gensim": [],
    }

    def add(kind, value, pattern):
        if isinstance(value, str) and pattern.match(value) and value not in found[kind]:
            found[kind].append(value)

    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            label, name, base = _call_label(node.func)
            if label is None:
                continue
            label, name, base = _resolve_import_alias(label, name, base, aliases)
            argument = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg in ("name", "info_or_id")), None
            )
            if label == "spacy.load" and isinstance(argument, ast.Constant):
                add("spacy", argument.value, _SPACY_PIPELINE_PATTERN)
            elif label == "downloader.load" and isinstance(argument, ast.Constant) and (
                "gensim.downloader" in aliases.values()
                or ast.unparse(node.func) == "gensim.downloader.load"
            ):
                add("gensim", argument.value, _GENSIM_MODEL_PATTERN)
            elif label == "easyocr.Reader":
                langs = node.args[0] if node.args else next(
                    (kw.value for kw in node.keywords if kw.arg == "lang_list"), None
                )
                codes = [
                    item.value for item in getattr(langs, "elts", [])
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
                if (
                    isinstance(langs, (ast.List, ast.Tuple)) and codes
                    and len(codes) == len(langs.elts)
                    and all(_EASYOCR_LANG_PATTERN.match(code) for code in codes)
                    and codes not in found["easyocr"]
                ):
                    found["easyocr"].append(codes)
            elif label == "tiktoken.get_encoding" and isinstance(argument, ast.Constant):
                add("tiktoken", argument.value, _TIKTOKEN_ENCODING_PATTERN)
            elif label == "tiktoken.encoding_for_model":
                model = node.args[0] if node.args else next(
                    (kw.value for kw in node.keywords if kw.arg == "model_name"), None
                )
                if isinstance(model, ast.Constant):
                    add("tiktoken_models", model.value, _TIKTOKEN_MODEL_PATTERN)
            elif label == "nltk.download" and argument is not None:
                values = argument.elts if isinstance(argument, (ast.List, ast.Tuple)) else [argument]
                for value in values:
                    if isinstance(value, ast.Constant):
                        add("nltk", value.value, _NLTK_PACKAGE_PATTERN)
    for package in _implied_nltk_packages(code_cells):
        add("nltk", package, _NLTK_PACKAGE_PATTERN)
    return found


# NLTK data a notebook uses without downloading it there (it was fetched
# once on the author's machine): `stopwords.words("english")`,
# `word_tokenize(...)`, `WordNetLemmatizer()`. The container has none of
# it, so each raised LookupError.
_NLTK_IMPLIED_DATA = {
    "word_tokenize": ("punkt", "punkt_tab"),
    "sent_tokenize": ("punkt", "punkt_tab"),
    "pos_tag": ("averaged_perceptron_tagger_eng",),
    "WordNetLemmatizer": ("wordnet", "omw-1.4"),
    "ne_chunk": ("maxent_ne_chunker_tab", "words"),
    "SentimentIntensityAnalyzer": ("vader_lexicon",),
}
_NLTK_CORPUS_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _implied_nltk_packages(code_cells):
    """NLTK data ids (first-seen order) that cells importing nltk need
    implicitly: `nltk.corpus` readers (`from nltk.corpus import stopwords`,
    `nltk.corpus.wordnet`) and the helpers in _NLTK_IMPLIED_DATA."""
    packages = []
    for cell in code_cells:
        if "nltk" not in cell:
            continue
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module == "nltk.corpus":
                names = [("corpus", alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("nltk"):
                names = [("helper", alias.name) for alias in node.names]
            elif (
                isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                and node.value.attr == "corpus" and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "nltk"
            ):
                names = [("corpus", node.attr)]
            elif (
                isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "nltk"
            ):
                names = [("helper", node.attr)]
            for kind, name in names:
                if kind == "corpus":
                    implied = (name,) if _NLTK_CORPUS_PATTERN.match(name) else ()
                    if name == "wordnet":
                        implied = ("wordnet", "omw-1.4")
                else:
                    implied = _NLTK_IMPLIED_DATA.get(name, ())
                for package in implied:
                    if package not in packages:
                        packages.append(package)
    return packages


# Literal values safe to repeat inside a Dockerfile `RUN python -c "..."`.
_SAFE_LITERAL_PATTERN = re.compile(r"^[A-Za-z0-9_.:/\-]+$")
_TORCH_HUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(:[A-Za-z0-9_.\-]+)?$")


def _safe_literal_source(node):
    """Python source for a literal argument that downloads the same
    weights at build time -- a str/bool/int constant, or a torchvision
    weights enum like `ResNet50_Weights.DEFAULT` (passed as its string
    name, which torchvision accepts) -- else None."""
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or (isinstance(value, int) and not isinstance(value, bool)):
            return repr(value)
        if isinstance(value, str) and _SAFE_LITERAL_PATTERN.match(value):
            return repr(value)
        return None
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        text = f"{node.value.id}.{node.attr}"
        return repr(text) if node.value.id.endswith("_Weights") else None
    return None


def _torch_call_source(call, prefix, kind):
    """`prefix(<literal args>)` repeating `call`'s arguments, or None when
    any argument isn't a safe literal or the call downloads nothing."""
    args = [_safe_literal_source(arg) for arg in call.args]
    keywords = {kw.arg: _safe_literal_source(kw.value) for kw in call.keywords if kw.arg}
    if None in args or None in keywords.values() or len(keywords) != len(call.keywords):
        return None
    if kind == "torchvision":
        downloads = keywords.get("weights") not in (None, "None") or keywords.get("pretrained") == "True"
        if args or not downloads:
            return None
    rendered = ", ".join(args + [f"{key}={value}" for key, value in keywords.items()])
    return f"{prefix}({rendered})"


def torch_weight_prefetches(code_cells):
    """Python one-liners (first-seen order, unique) that download the
    pretrained weights the notebook loads -- `torchvision.models.resnet50(
    weights="DEFAULT")` / `(pretrained=True)` and `torch.hub.load(
    "ultralytics/yolov5", "yolov5s")` -- for the Dockerfile to run at build
    time, so a container start doesn't fetch them again (or fail offline).
    Only calls whose arguments are all literals are repeated."""
    found = []
    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            dotted = ast.unparse(func)
            label, name, base = _call_label(func)
            source = None
            if dotted == "torch.hub.load" or (
                label is not None
                and _resolve_import_alias(label, name, base, aliases)[0] == "hub.load"
                and aliases.get(base or name, "").startswith("torch.hub")
            ):
                repo = node.args[0] if node.args else None
                if isinstance(repo, ast.Constant) and isinstance(repo.value, str) \
                        and _TORCH_HUB_REPO_PATTERN.match(repo.value):
                    source = _torch_call_source(node, "torch.hub.load", "hub")
                    source = source and f"import torch; {source}"
            else:
                module, _, model = dotted.rpartition(".")
                if not module and name in aliases:
                    module, _, model = aliases[name].rpartition(".")
                elif module in aliases:
                    module = aliases[module]
                if module == "torchvision.models" and model.isidentifier() and model.islower():
                    source = _torch_call_source(node, f"m.{model}", "torchvision")
                    source = source and f"import torchvision.models as m; {source}"
            if source and source not in found:
                found.append(source)
    return found


# System libraries python:3.x-slim lacks that an import needs at runtime:
# the wheel installs fine, then `import cv2` fails on libGL.so.1, lightgbm
# on libgomp.so.1, soundfile on libsndfile, pydub/whisper shell out to
# ffmpeg, pytesseract to tesseract. Jupyter's environment already had them.
IMPORT_APT_PACKAGES = {
    "cv2": ("libgl1", "libglib2.0-0"),
    "lightgbm": ("libgomp1",),
    "soundfile": ("libsndfile1",),
    "librosa": ("libsndfile1", "ffmpeg"),
    "pydub": ("ffmpeg",),
    "whisper": ("ffmpeg",),
    "moviepy": ("ffmpeg",),
    "pytesseract": ("tesseract-ocr",),
    "pdf2image": ("poppler-utils",),
    "pyzbar": ("libzbar0",),
    "magic": ("libmagic1",),
}


def _import_implied_apt_packages(code_cells):
    """apt packages (first-seen order, unique) that the notebook's imports
    need per IMPORT_APT_PACKAGES, skipping imports an "exclude" directive
    drops and imports of a local module of the same name."""
    excluded = set(_extract_excluded_imports(code_cells))
    local = set(_writefile_modules(code_cells))
    packages = []
    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.partition(".")[0]
                if top in excluded or top in local:
                    continue
                for package in IMPORT_APT_PACKAGES.get(top, ()):
                    if package not in packages:
                        packages.append(package)
    return packages


_APT_INSTALL_LINE_PATTERN = re.compile(
    r"^#\s*[!%]\s*(?:sudo\s+)?apt(?:-get)?\s+(?:-\S+\s+)*install\s+(?P<args>.+?)\s*$",
    re.MULTILINE,
)
# The Dockerfile writes these into one `apt-get install` line, so only plain
# Debian package names (optionally "=version") are accepted.
_APT_PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.\-]*(=[A-Za-z0-9.:~+\-]+)?$")
_SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", ">", ">>", "<", "&", "2>&1"})


def _apt_install_packages(code_cells):
    """Package names from `!apt-get install -y a b` / `!apt install a` /
    `!sudo apt-get install a` lines (the notebook parser leaves each as a
    "# !apt-get ..." comment), first-seen order, unique.

    Options are skipped and parsing stops at the first shell operator, so
    `apt-get install -y a && apt-get install b` yields `a` only -- and
    anything that is not a plain package name is dropped.
    """
    packages = []
    for cell in code_cells:
        unsafe_lines = _lines_inside_multiline_strings(cell)
        for match in _shell_install_matches(cell, _APT_INSTALL_LINE_PATTERN, _SHELL_CELL_APT_PATTERN):
            if cell.count("\n", 0, match.start()) + 1 in unsafe_lines:
                continue
            try:
                tokens = shlex.split(match.group("args"), comments=True)
            except ValueError:
                continue
            for token in tokens:
                if token in _SHELL_OPERATORS:
                    break
                if token.startswith("-") or not _APT_PACKAGE_PATTERN.match(token):
                    continue
                if token not in packages:
                    packages.append(token)
    return packages


# Recognizes a "# notebook-to-api: exclude <import-name>" comment
# directive -- see _extract_excluded_imports below for what it's for.
# Same matching rules as REQUIREMENT_DIRECTIVE_PATTERN above (leading
# whitespace allowed, "#" must start the line) for the identical reason:
# usable indented inside a function body, and immune to an unrelated
# inline comment merely containing this phrase elsewhere in a line. Also
# same string-literal-safety treatment: a match inside a multi-line
# string is discarded by _extract_excluded_imports below via
# _lines_inside_multiline_strings.
EXCLUDE_DIRECTIVE_PATTERN = re.compile(
    r"^\s*#\s*notebook-to-api:\s*exclude\s+(?P<name>\S+)\s*$",
    re.MULTILINE,
)


def _extract_excluded_imports(code_cells):
    """Import names a notebook author explicitly opts out of
    requirements.txt (and, via inspector.py's own identical use of this,
    every "dependencies" field derived from the same import scan) via a
    "# notebook-to-api: exclude <import-name>" comment directive, one per
    line, anywhere in any code cell.

    extract_third_party_imports (below) otherwise pins every third-party
    name any code cell's own `import` statement references, with no way
    to keep one out short of deleting the import (and whatever uses it)
    from the notebook entirely -- even for an import that's incidental to
    what actually gets compiled into an endpoint (e.g. a scratch cell
    doing `import pytest` to sanity-check a function inline, never
    imported by anything the generated app itself calls at runtime), or
    one already vendored/bundled into the deploy target's own image and
    never meant to be pip-installed there at all.

    `name` is matched exactly against an import's own top-level module
    name (the same name extract_third_party_imports/
    extract_imports_from_code already collect it under, before any
    distribution_name_for_import resolution) -- not a PyPI distribution
    name, since that's what a notebook's own `import` statement actually
    names, and what a caller writing this directive is looking at.
    Returns a set, the same membership-tested shape STANDARD_LIBS already
    is here, for the identical `imp not in ...` filter below.
    """
    excluded = set()

    for cell in code_cells:

        unsafe_lines = _lines_inside_multiline_strings(cell)

        for match in EXCLUDE_DIRECTIVE_PATTERN.finditer(cell):

            if cell.count("\n", 0, match.start()) + 1 in unsafe_lines:
                continue

            name = match.group("name").strip()

            if name:
                excluded.add(name)

    return excluded


# Recognizes a "# notebook-to-api: private" comment directive immediately
# above a function definition (blank lines in between are tolerated, the
# same way a real editor/formatter might leave one) -- see
# _extract_private_function_names below for what it's for. Unlike
# REQUIREMENT_DIRECTIVE_PATTERN/EXCLUDE_DIRECTIVE_PATTERN above, this one
# is positional: it only matches when directly followed by a "def"/"async
# def" line, since which function it applies to is the entire point.
#
# Also tolerates another "# notebook-to-api: ..." directive stacked
# between this one and the def, the same way TIMEOUT_DIRECTIVE_PATTERN/
# TAG_DIRECTIVE_PATTERN/CACHE_DIRECTIVE_PATTERN/RATE_LIMIT_DIRECTIVE_
# PATTERN below already do -- previously only a blank line was tolerated
# there, so "# notebook-to-api: private" stacked with any other directive
# (e.g. "# notebook-to-api: tag Admin" right above the same def) silently
# failed to match at all: the "# notebook-to-api: tag ..." line is
# neither blank nor a "def", so it broke this pattern's own strict
# "blank lines only" allowance. Confirmed exploitable: a function marked
# both private and tagged still compiled into a public endpoint, with no
# error or warning that the author's own "private" directive had been
# silently ignored -- exactly the kind of unexpectedly-exposed endpoint
# this directive exists to prevent in the first place.
PRIVATE_FUNCTION_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*private\s*$"
    r"(?:\n[ \t]*(?:#\s*notebook-to-api:[^\n]*)?)*"
    r"\n[ \t]*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


def _extract_private_function_names(code_cells):
    """Names of every function `code_cells` marks "# notebook-to-api:
    private" (immediately above its own `def`/`async def` line, see
    PRIVATE_FUNCTION_DIRECTIVE_PATTERN above), across all cells.

    _filter_functions_by_name's own docstring already spells out the gap
    this closes: "Every module-level function a notebook defines ...
    becomes a public API endpoint, with no previous way to opt any of
    them out" besides an external --only/--exclude flag a caller has to
    remember to pass on *every* compile/serve/watch/deploy invocation --
    renaming a helper doesn't help either, since extract_functions_from_code
    applies no leading-underscore (or any other) naming convention. A
    notebook author's own internal helper (data loading, validation,
    formatting, ...) had no way to declare, once, from inside the
    notebook itself, that it should never become an endpoint at all.

    Returns a set, the same membership-tested shape _extract_excluded_imports
    above already returns for an import name, for the identical `name in
    ...`/`name not in ...` filtering _drop_private_functions below (and
    every direct caller of this function) already needs.
    """
    private_names = set()

    for cell in code_cells:

        for match in PRIVATE_FUNCTION_DIRECTIVE_PATTERN.finditer(cell):

            private_names.add(match.group("name"))

    return private_names


def _drop_private_functions(functions, code_cells, only=None, exclude=None):
    """Drop every function `code_cells` marks private (see
    _extract_private_function_names above) from `functions` -- shared by
    every entry point that builds a functions list directly from
    code_cells (compile_notebook_to_api below, and app_preview_endpoint
    in routes/upload.py) so a private function is dropped identically
    everywhere, the same "can't drift" guarantee _filter_functions_by_name's
    own only/exclude handling already provides. Applied before
    _filter_functions_by_name itself, which has no access to code_cells
    (only to the already-extracted `functions` list) and so has no way to
    know which of them a notebook has marked private on its own.

    Returns (filtered_functions, adjusted_exclude): `exclude` has any
    already-private name filtered back out, so naming an already-private
    function via --exclude is the harmless no-op it actually is (the same
    "redundant is not an error" precedent this project's own sha256/tag
    filters already follow elsewhere) instead of _filter_functions_by_name's
    "not defined in this notebook" error, which would otherwise be
    misleading here -- the function *is* defined, just already excluded
    by directive.

    Raises ValueError -- the same type _filter_functions_by_name's own
    unknown-name error already raises -- if `only` explicitly names a
    private function: unlike `exclude` above, that's a direct
    contradiction of the notebook's own declared intent, not a redundant
    no-op, and deserves a clearer, more actionable error than the generic
    "unknown function name" a caller would otherwise get once the
    function is silently missing from `functions` altogether.
    """
    private_names = _extract_private_function_names(code_cells)

    if not private_names:
        return functions, exclude

    if only:

        conflicting = sorted(set(only) & private_names)

        if conflicting:
            raise ValueError(
                f"Function(s) {', '.join(conflicting)} are marked "
                '"# notebook-to-api: private" in the notebook and can\'t '
                "be exposed via only/--only. Remove the directive in the "
                "notebook to expose them."
            )

    if exclude:
        exclude = [name for name in exclude if name not in private_names]

    return (
        [func for func in functions if func["name"] not in private_names],
        exclude,
    )


# Recognizes a "# notebook-to-api: background" or "# notebook-to-api:
# sync" comment directive immediately above a function definition (blank
# lines in between are tolerated, same as PRIVATE_FUNCTION_DIRECTIVE_
# PATTERN above) -- see _extract_background_overrides below for what it's
# for. Positional, for the identical reason PRIVATE_FUNCTION_DIRECTIVE_
# PATTERN is: which function it applies to is the entire point. Also
# tolerates another "# notebook-to-api: ..." directive stacked in
# between (see PRIVATE_FUNCTION_DIRECTIVE_PATTERN's own comment above for
# the exact silent-failure this fixes here too).
BACKGROUND_OVERRIDE_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*(?P<mode>background|sync)\s*$"
    r"(?:\n[ \t]*(?:#\s*notebook-to-api:[^\n]*)?)*"
    r"\n[ \t]*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


def _extract_background_overrides(code_cells):
    """{function_name: True/False}, one entry per function `code_cells`
    marks "# notebook-to-api: background" (True) or "# notebook-to-api:
    sync" (False) immediately above its own `def`/`async def` line (see
    BACKGROUND_OVERRIDE_DIRECTIVE_PATTERN above), across all cells.

    generate_fastapi_code's own LONG_RUNNING_KEYWORDS (generator/
    api_generator.py) is the ONLY thing that has ever decided whether a
    notebook function compiles into a background/task_id endpoint or a
    synchronous one -- a plain substring match against the function's own
    name, with no way for a notebook author to correct it. Confirmed
    exploitable both directions: "regenerate_token" (fast) contains
    "generate" and wrongly becomes background; "run_batch_inference"
    (genuinely slow) matches none of LONG_RUNNING_KEYWORDS and wrongly
    stays synchronous, tying up a worker thread for its entire duration
    (see _run_background_task's own docstring, api_generator.py, on why
    that starves even unrelated synchronous endpoints like GET /health).
    This directive gives an author the same "an explicit directive beats
    an inferred guess" escape hatch REQUIREMENT_DIRECTIVE_PATTERN/
    EXCLUDE_DIRECTIVE_PATTERN/PRIVATE_FUNCTION_DIRECTIVE_PATTERN above
    already give for other compile-time inferences that can be wrong.

    Returns a plain dict (not two separate sets) since a caller -- see
    resolve_is_background, generator/api_generator.py -- needs to
    distinguish "no directive, fall back to the heuristic" from "directive
    says False", which a single membership-tested set (the shape
    _extract_private_function_names/_extract_excluded_imports above
    return) can't represent for the "sync" half on its own.

    Raises ValueError -- the same "two contradictory directives naming
    the same thing" treatment REQUIREMENT_DIRECTIVE_PATTERN's own
    conflicting-spec handling above already gives -- if a function is
    marked both "background" and "sync" (in the same cell or across
    different cells).
    """
    overrides = {}

    for cell in code_cells:

        for match in BACKGROUND_OVERRIDE_DIRECTIVE_PATTERN.finditer(cell):

            name = match.group("name")
            is_background = match.group("mode") == "background"

            if name in overrides and overrides[name] != is_background:
                raise ValueError(
                    "Conflicting '# notebook-to-api: background'/'# "
                    f"notebook-to-api: sync' directives for function "
                    f"'{name}'. Remove or reconcile one of them."
                )

            overrides[name] = is_background

    return overrides


# Recognizes a "# notebook-to-api: deprecated" comment directive
# immediately above a function definition (blank lines in between are
# tolerated, same as PRIVATE_FUNCTION_DIRECTIVE_PATTERN/BACKGROUND_
# OVERRIDE_DIRECTIVE_PATTERN above) -- see _extract_deprecated_functions
# below for what it's for. Positional, for the identical reason those two
# already are: which function it applies to is the entire point. An
# optional ": <reason>" suffix (e.g. "# notebook-to-api: deprecated: use
# train_v2 instead") is captured as the deprecation's own free-text
# reason, mirroring REQUIREMENT_DIRECTIVE_PATTERN's own "<spec>" capture
# for a directive that takes an argument -- "deprecated" alone (no colon)
# is equally valid, for a function whose author has no specific
# replacement or reason to give. Also tolerates another "# notebook-to-
# api: ..." directive stacked in between (see PRIVATE_FUNCTION_DIRECTIVE_
# PATTERN's own comment above for the exact silent-failure this fixes
# here too).
DEPRECATED_FUNCTION_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*deprecated(?:\s*:\s*(?P<reason>\S.*?))?\s*$"
    r"(?:\n[ \t]*(?:#\s*notebook-to-api:[^\n]*)?)*"
    r"\n[ \t]*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


# "# notebook-to-api: timeout <seconds>" directly above a function's own
# `def` (optionally above other "# notebook-to-api:" directives for the
# same function, with only blank lines between) -- that endpoint's own
# NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS. "0" means "no timeout for this
# one", even if a global timeout is set.
TIMEOUT_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*timeout\s+(?P<seconds>\d+)\s*$"
    r"(?:\n[ \t]*(?:#\s*notebook-to-api:[^\n]*)?)*"
    r"\n[ \t]*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


# "# notebook-to-api: rate-limit <N>" directly above a function's own def
# (optionally above other "# notebook-to-api:" directives) -- at most N
# calls per minute per API key to that one endpoint, on top of the global
# NOTEBOOK_API_RATE_LIMIT_PER_MINUTE.
TAG_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*tag\s+(?P<tag>[A-Za-z0-9][A-Za-z0-9 _-]{0,39}?)\s*$"
    r"(?:\n[ \t]*(?:#\s*notebook-to-api:[^\n]*)?)*"
    r"\n[ \t]*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


def _extract_tag_overrides(code_cells):
    """{function_name: tag} for every function marked
    "# notebook-to-api: tag <Name>" (see TAG_DIRECTIVE_PATTERN). Before
    this, an endpoint's OpenAPI tag -- how Swagger UI groups it, and the
    x-notebook-to-api-category SDKs/docs read -- could only come from
    guessing at its name ("train" -> Training, ...), so a notebook had no
    way to group e.g. score_model with its other inference endpoints.
    The tag is 1-40 letters, digits, spaces, '-' or '_', starting with a
    letter or digit; anything else is not matched, so the name-based
    default still applies. The last directive seen for a function wins.
    """
    overrides = {}
    for cell in code_cells:
        for match in TAG_DIRECTIVE_PATTERN.finditer(cell):
            overrides[match.group("name")] = match.group("tag").strip()
    return overrides


CACHE_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*cache\s+(?P<ttl>\d+)\s*$"
    r"(?:\n[ \t]*(?:#\s*notebook-to-api:[^\n]*)?)*"
    r"\n[ \t]*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


def _extract_cache_overrides(code_cells):
    """{function_name: ttl_seconds} for every function marked
    "# notebook-to-api: cache N" with N > 0 (see CACHE_DIRECTIVE_PATTERN).
    Before this, every call re-ran the notebook function even when an
    identical request had just been answered -- an expensive but
    deterministic lookup/inference paid full cost on every repeat.
    "cache 0" is ignored; the last directive seen for a function wins.
    """
    overrides = {}
    for cell in code_cells:
        for match in CACHE_DIRECTIVE_PATTERN.finditer(cell):
            ttl = int(match.group("ttl"))
            if ttl > 0:
                overrides[match.group("name")] = ttl
            else:
                overrides.pop(match.group("name"), None)
    return overrides


RATE_LIMIT_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*rate-limit\s+(?P<limit>\d+)\s*$"
    r"(?:\n[ \t]*(?:#\s*notebook-to-api:[^\n]*)?)*"
    r"\n[ \t]*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


def _extract_rate_limit_overrides(code_cells):
    """{function_name: calls_per_minute} for every function marked
    "# notebook-to-api: rate-limit N" with N > 0 (see
    RATE_LIMIT_DIRECTIVE_PATTERN). Before this, the only throttle was the
    global per-key NOTEBOOK_API_RATE_LIMIT_PER_MINUTE shared by every
    endpoint -- an expensive one (a model fit, a report) couldn't be held
    to a few calls a minute without throttling every cheap one just as
    hard. "rate-limit 0" is ignored rather than meaning "no calls ever".
    The last directive seen for a function wins.
    """
    overrides = {}
    for cell in code_cells:
        for match in RATE_LIMIT_DIRECTIVE_PATTERN.finditer(cell):
            limit = int(match.group("limit"))
            if limit > 0:
                overrides[match.group("name")] = limit
            else:
                overrides.pop(match.group("name"), None)
    return overrides


def _extract_timeout_overrides(code_cells):
    """{function_name: seconds} for every function `code_cells` marks
    "# notebook-to-api: timeout <seconds>" (see TIMEOUT_DIRECTIVE_PATTERN).
    Before this, NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS was one global bound
    for every synchronous endpoint -- a notebook with one legitimately slow
    function (a report, a model fit) and many fast ones had to choose
    between a limit loose enough for the slow one (useless for the rest)
    or one that 504s it. A function marked more than once keeps the last
    value seen, the same "last one wins" rule as the deprecated directive.
    """
    overrides = {}
    for cell in code_cells:
        for match in TIMEOUT_DIRECTIVE_PATTERN.finditer(cell):
            overrides[match.group("name")] = int(match.group("seconds"))
    return overrides


# File-reading calls whose first argument is a path: matched by the called
# name (`pd.read_csv` -> "read_csv"), or -- for the generic `load` -- only on
# a numpy/torch/joblib base.
_FILE_READ_CALLS = frozenset({
    "read_csv", "read_excel", "read_json", "read_parquet", "read_feather",
    "read_pickle", "read_table", "read_hdf", "loadtxt", "genfromtxt",
    "imread", "load_workbook", "open",
    "read_fwf", "read_orc", "read_xml", "read_html", "read_stata", "read_sas",
    "read_spss", "load_model", "fromfile", "read_text", "read_bytes",
})
_FILE_LOAD_BASES = frozenset({"np", "numpy", "torch", "joblib", "librosa"})
# Path-taking openers/loaders matched on the (alias-resolved) `base.name`
# label, since their bare names (`connect`, `File`, `read`) are too generic.
_FILE_READ_LABELS = frozenset({
    "sqlite3.connect", "h5py.File", "zipfile.ZipFile", "io.loadmat",
    "sf.read", "soundfile.read", "xr.open_dataset", "xarray.open_dataset",
    # `from safetensors.torch import load_file` resolves to torch.load_file
    "torch.load_file", "safetensors.load_file",
})
# Model loaders whose first argument (or model_path=/model_file=) is a local
# weights file: onnxruntime.InferenceSession("model.onnx"), YOLO("best.pt"),
# cv2.CascadeClassifier("face.xml"), cv2.dnn.readNetFromONNX("net.onnx").
_MODEL_FILE_CALLS = frozenset({
    "InferenceSession", "YOLO", "RTDETR", "CascadeClassifier", "readNet",
    "readNetFromONNX", "readNetFromTensorflow", "readNetFromCaffe",
    "readNetFromDarknet", "readNetFromTorch", "readNetFromTFLite",
})
# ...and ones that only read a file when given one by keyword:
# lgb.Booster(model_file=...), tf.lite.Interpreter(model_path=...).
_MODEL_FILE_KEYWORD_CALLS = frozenset({"Booster", "Interpreter"})
# gensim's KeyedVectors.load("vectors.kv") and friends.
_GENSIM_LOAD_BASES = frozenset({"KeyedVectors", "Word2Vec", "Doc2Vec", "FastText", "LdaModel"})
# Official Ultralytics weights YOLO() downloads itself when they're absent.
_AUTO_DOWNLOADED_YOLO = re.compile(r"^(yolo|rtdetr)[\w.\-]*\.pt$", re.IGNORECASE)
# ...and those whose second argument / `mode=` can make them write instead.
_MODE_CHECKED_LABELS = frozenset({"h5py.File", "zipfile.ZipFile"})
# Keyword names a read call's path can be passed as.
_PATH_KEYWORDS = (
    "filepath_or_buffer", "io", "path", "file", "fname", "fp",
    "database", "file_name", "filename_or_obj", "model_path", "model_file",
    "path_or_bytes",
)
# Values that look like relative paths but never name a file on disk.
_NON_FILE_PATHS = frozenset({":memory:", ""})


def _find_cells_with_error_outputs(notebook):
    """[{"cell", "line", "error", "message"}] for every code cell whose saved
    outputs include an error -- a cell that raised when the author last ran it
    (a NameError from a scratch cell, a failed read, an intentional demo
    exception). Compiling inlines every code cell into the app's runtime
    module, so a cell like that runs again when the app starts and fails the
    same way, taking every endpoint down. `cell` is the 1-based position among
    the notebook's code cells, the same numbering _find_import_time_hazards
    uses; `line` is the traceback's own line number within that cell when
    Jupyter recorded one, else null.
    """
    found = []
    code_cell_number = 0
    for cell in notebook.cells:
        if cell.cell_type != "code":
            continue
        code_cell_number += 1
        for output in cell.get("outputs", []):
            if output.get("output_type") != "error":
                continue
            line = None
            for traceback_line in output.get("traceback", []):
                # Jupyter renders "----> 3 foo()" style arrows with ANSI codes.
                match = re.search(r"-+>\s*(\d+)", re.sub(r"\x1b\[[0-9;]*m", "", traceback_line))
                if match:
                    line = int(match.group(1))
            found.append({
                "cell": code_cell_number,
                "line": line,
                "error": output.get("ename", "Error"),
                "message": output.get("evalue", ""),
            })
            break
    return found


def _call_label(func):
    """("pd.read_csv", "read_csv", "pd") for a Call's `func` node."""
    if isinstance(func, ast.Attribute):
        base = func.value.id if isinstance(func.value, ast.Name) else None
        return (f"{base}.{func.attr}" if base else func.attr), func.attr, base
    if isinstance(func, ast.Name):
        return func.id, func.id, None
    return None, None, None


def _import_aliases(tree):
    """{local name: dotted original} for the renamed imports in `tree`:
    `import time as t` -> {"t": "time"}, `from getpass import getpass as ask`
    -> {"ask": "getpass.getpass"}, `from time import sleep` ->
    {"sleep": "time.sleep"}. A later import of the same name wins."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _resolve_import_alias(label, name, base, aliases):
    """(label, name, base) with an imported alias replaced by what it
    names, so `ask(...)` after `from getpass import getpass as ask` reads as
    `getpass.getpass(...)` and `t.sleep(...)` after `import time as t` as
    `time.sleep(...)`. Unaliased calls come back unchanged."""
    if label is None or not aliases:
        return label, name, base
    if base is None and name in aliases and "." in aliases[name]:
        module, _, attr = aliases[name].rpartition(".")
        base = module.split(".")[-1]
        return f"{base}.{attr}", attr, base
    if base is not None and base in aliases:
        module = aliases[base]
        return f"{module.split('.')[-1]}.{name}", name, module.split(".")[-1]
    return label, name, base


def _is_main_guard(node):
    test = node.test if isinstance(node, ast.If) else None
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name) and test.left.id == "__name__"
    )


def _literal_path_value(node, constants=None):
    """The string a path expression built only from literals evaluates to:
    "x.csv", `os.path.join("data", "x.csv")`, `Path("data") / "x.csv"`,
    `Path("data", "x.csv")`, `f"data/x.csv"`, and names in `constants`
    (see _module_path_constants) inside any of these: `DATA_DIR / "x.csv"`,
    `f"{DATA_DIR}/x.csv"`. None for anything that depends on a runtime value."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return (constants or {}).get(node.id)
    if isinstance(node, ast.JoinedStr):
        pieces = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                pieces.append(value.value)
            elif (
                isinstance(value, ast.FormattedValue) and value.conversion == -1
                and value.format_spec is None
                and isinstance(value.value, ast.Name)
                and value.value.id in (constants or {})
            ):
                pieces.append(constants[value.value.id])
            else:
                return None
        return "".join(pieces)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _literal_path_value(node.left, constants), _literal_path_value(node.right, constants)
        if left is None or right is None:
            return None
        return right if right.startswith("/") else f"{left.rstrip('/')}/{right}"
    if isinstance(node, ast.Call) and not node.keywords and node.args:
        _, name, _ = _call_label(node.func)
        dotted = ast.unparse(node.func)
        if dotted in ("os.path.join", "path.join", "osp.join") or name in ("Path", "PurePath", "PosixPath"):
            parts = [_literal_path_value(arg, constants) for arg in node.args]
            if any(part is None for part in parts):
                return None
            joined = parts[0]
            for part in parts[1:]:
                joined = part if part.startswith("/") else f"{joined.rstrip('/')}/{part}"
            return joined
    return None


def _module_store_counts(tree):
    """{name: times it's bound at module level} for one cell, function and
    class bodies left out (they bind their own scope)."""
    counts = {}
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name] = counts.get(node.name, 0) + 1
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            counts[node.id] = counts.get(node.id, 0) + 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                counts[name] = counts.get(name, 0) + 1
        stack.extend(ast.iter_child_nodes(node))
    return counts


def _module_path_constants(trees):
    """{name: path string} for module-level names bound exactly once in the
    whole notebook, by a plain `NAME = <literal path expression>` (see
    _literal_path_value): `DATA_PATH = "data/sales.csv"`,
    `DATA_DIR = Path("data")`, `MODEL = DATA_DIR / "model.pkl"`. A name
    rebound anywhere at module level (a loop variable, a second assignment,
    an import) is left out, since its value at the read is unknown."""
    counts = {}
    for tree in trees:
        for name, count in _module_store_counts(tree).items():
            counts[name] = counts.get(name, 0) + count
    constants = {}
    for tree in trees:
        for node in tree.body:
            if not (
                isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ) or counts.get(node.targets[0].id) != 1:
                continue
            value = _literal_path_value(node.value, constants)
            if value is not None:
                constants[node.targets[0].id] = value
    return constants


def _is_relative_path(path):
    return bool(path) and not (
        path.startswith(("/", "~")) or "://" in path or (len(path) > 1 and path[1] == ":")
    )


def _relative_literal_path(call, constants=None):
    """The call's first-argument path when it's built only from literals
    (see _literal_path_value) and is a relative local path (not absolute,
    `~`, or a URL), else None."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in ("read_text", "read_bytes"):
        # Path("notes.txt").read_text(): the path is the receiver; any other
        # receiver's first argument is an encoding, not a path.
        receiver = func.value
        if isinstance(receiver, ast.Call):
            _, path_name, _ = _call_label(receiver.func)
            if path_name not in ("Path", "PurePath"):
                return None
        elif not isinstance(receiver, ast.BinOp):
            return None
        path = _literal_path_value(receiver, constants)
        if path is not None and path.startswith("./"):
            path = path[2:]
        return path if _is_relative_path(path) else None
    arg = call.args[0] if call.args else next(
        (kw.value for kw in call.keywords if kw.arg in _PATH_KEYWORDS),
        None,
    )
    path = _literal_path_value(arg, constants) if arg is not None else None
    if path is not None and path.startswith("./") and not isinstance(arg, (ast.Constant, ast.Name)):
        path = path[2:]
    return path if _is_relative_path(path) else None


# Directory listings whose files the notebook then reads: `os.listdir("data")`,
# `glob.glob("data/*.csv")`, `Path("imgs").rglob("*.png")`. A relative one
# found nothing (or raised FileNotFoundError) in the compiled app, which used
# to ship only files named outright. Each is reduced to a glob pattern
# relative to the notebook's directory.
_DIR_LISTING_PATTERNS = {"os.listdir": "{}/*", "os.scandir": "{}/*", "os.walk": "{}/**/*"}
_GLOB_CALL_LABELS = frozenset({"glob.glob", "glob.iglob"})


def _relative_listing_pattern(label, call, constants=None):
    """The relative glob pattern a top-level directory listing covers, else
    None (non-literal, absolute, climbing out with "..", or the notebook's
    whole directory)."""
    func = call.func
    if (
        isinstance(func, ast.Attribute) and func.attr in ("iterdir", "glob", "rglob")
        and isinstance(func.value, ast.Call)
    ):
        _, path_name, _ = _call_label(func.value.func)
        if path_name not in ("Path", "PurePath"):
            return None
        directory = _relative_literal_path(func.value, constants)
        if func.attr == "iterdir":
            pattern = "*"
        else:
            pattern = _relative_literal_path(call, constants)
            if pattern is None:
                return None
            if func.attr == "rglob":
                pattern = f"**/{pattern}"
        if directory is None:
            return None
        pattern = f"{directory.rstrip('/')}/{pattern}"
    elif label in _DIR_LISTING_PATTERNS:
        directory = _relative_literal_path(call, constants)
        if directory is None:
            return None
        pattern = _DIR_LISTING_PATTERNS[label].format(directory.rstrip("/"))
    elif label in _GLOB_CALL_LABELS:
        pattern = _relative_literal_path(call, constants)
    else:
        return None
    if not pattern:
        return None
    parts = Path(pattern).parts
    if ".." in parts or parts[0] in ("*", "**"):
        return None
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if not pattern or pattern.split("/", 1)[0] == ".":
        return None
    return pattern


def _expand_listing_pattern(directory, pattern):
    """{relative posix path: absolute Path} for the files `pattern` matches
    under `directory` (checkpoint copies left out)."""
    found = {}
    try:
        matches = sorted(directory.glob(pattern))
    except (ValueError, NotImplementedError):
        return found
    for match in matches:
        if ".ipynb_checkpoints" in match.parts or not match.is_file():
            continue
        try:
            relative = match.resolve().relative_to(directory)
        except ValueError:
            continue
        found[relative.as_posix()] = match.resolve()
    return found


def _directory_read_files(directory, path):
    """{relative posix path: absolute Path} for every file under `path`
    when a read names a directory beside the notebook (a Keras SavedModel
    passed to `load_model("saved_model")`, `pd.read_parquet("parts/")`),
    else {} -- such reads load the whole folder, so all of it ships."""
    relative = Path(path)
    if ".." in relative.parts or relative.is_absolute():
        return {}
    if not (directory / relative).is_dir():
        return {}
    return _expand_listing_pattern(directory, f"{relative.as_posix()}/**/*")


def _shipped_as_directory(path, shipped):
    """Whether `path` names a directory some of whose files are in
    `shipped` (so a read of it can point at the shipped copy)."""
    prefix = Path(path).as_posix().rstrip("/") + "/"
    return prefix != "./" and any(key.startswith(prefix) for key in shipped)


# Colab modules the shim below stands in for outside Colab.
_COLAB_SHIMMED_MODULES = frozenset({
    "google.colab.userdata", "google.colab.drive", "google.colab.files",
})

# `drive.mount("/content/drive")` needs a Colab runtime; outside one the
# shim's mount is a no-op, since Drive paths are already read from beside
# the notebook (see relocate_sandbox_paths, backend/parser).
# Colab keeps API keys in "Secrets" read with `userdata.get("NAME")`. Outside
# Colab that module doesn't exist, so the import failed on startup; this shim
# answers the same call from the environment instead (and raises Colab's
# SecretNotFoundError-style error when the variable is missing).
_COLAB_USERDATA_SHIM = """\
# google.colab.userdata from environment variables, drive.mount as a no-op (notebook-to-api)
import os as _nb_os5
import sys as _nb_sys5
import types as _nb_types5

if "google.colab" not in _nb_sys5.modules:
    try:
        import google.colab  # noqa: F401  (a real Colab runtime wins)
    except ImportError:
        class SecretNotFoundError(Exception):
            pass

        def _nb_userdata_get(name):
            value = _nb_os5.environ.get(name)
            if value is None:
                raise SecretNotFoundError(
                    f"Secret {name} does not exist: set the {name} environment variable"
                )
            return value

        _nb_userdata = _nb_types5.ModuleType("google.colab.userdata")
        _nb_userdata.get = _nb_userdata_get
        _nb_userdata.SecretNotFoundError = SecretNotFoundError
        def _nb_drive_mount(mountpoint, force_remount=False, timeout_ms=120000, readonly=False):
            print(
                f"notebook-to-api: drive.mount({mountpoint!r}) skipped outside Colab; "
                "Drive files are read from beside the app"
            )

        def _nb_drive_unmount(timeout_ms=None):
            pass

        def _nb_files_download(filename):
            print(
                f"notebook-to-api: files.download({filename!r}) skipped outside Colab; "
                "the file stays where the app wrote it"
            )

        def _nb_files_upload(*args, **kwargs):
            raise RuntimeError(
                "files.upload() needs Colab's browser upload dialog; put the file beside "
                "the notebook and read it by name instead"
            )

        _nb_files = _nb_types5.ModuleType("google.colab.files")
        _nb_files.download = _nb_files_download
        _nb_files.upload = _nb_files_upload
        _nb_drive = _nb_types5.ModuleType("google.colab.drive")
        _nb_drive.mount = _nb_drive_mount
        _nb_drive.flush_and_unmount = _nb_drive_unmount
        _nb_colab = _nb_types5.ModuleType("google.colab")
        _nb_colab.__path__ = []
        _nb_colab.userdata = _nb_userdata
        _nb_colab.drive = _nb_drive
        _nb_colab.files = _nb_files
        try:
            import google as _nb_google
        except ImportError:
            _nb_google = _nb_types5.ModuleType("google")
            _nb_google.__path__ = []
            _nb_sys5.modules["google"] = _nb_google
        _nb_google.colab = _nb_colab
        _nb_sys5.modules["google.colab"] = _nb_colab
        _nb_sys5.modules["google.colab.userdata"] = _nb_userdata
        _nb_sys5.modules["google.colab.drive"] = _nb_drive
        _nb_sys5.modules["google.colab.files"] = _nb_files

"""


# Kaggle notebooks read secrets with `UserSecretsClient().get_secret("NAME")`
# from the `kaggle_secrets` module, which exists only on Kaggle (and is not a
# PyPI package -- the compile used to list it in requirements.txt anyway).
_KAGGLE_SECRETS_SHIM = """\
# kaggle_secrets, answered from environment variables (notebook-to-api)
import os as _nb_os6
import sys as _nb_sys6
import types as _nb_types6

if "kaggle_secrets" not in _nb_sys6.modules:
    try:
        import kaggle_secrets  # noqa: F401  (a real Kaggle runtime wins)
    except ImportError:
        class BackendError(Exception):
            pass

        class UserSecretsClient:
            def get_secret(self, label):
                value = _nb_os6.environ.get(label)
                if value is None:
                    raise BackendError(
                        f"Secret {label} does not exist: set the {label} environment variable"
                    )
                return value

        _nb_kaggle = _nb_types6.ModuleType("kaggle_secrets")
        _nb_kaggle.UserSecretsClient = UserSecretsClient
        _nb_kaggle.BackendError = BackendError
        _nb_sys6.modules["kaggle_secrets"] = _nb_kaggle

"""


# `from tqdm.notebook import tqdm` draws its bar with ipywidgets, which the
# compiled app doesn't install: creating a bar raised ImportError ("IProgress
# not found") / AttributeError, so a top-level progress loop stopped the app
# starting and one inside an endpoint failed every call. Without ipywidgets
# the notebook flavour is swapped for tqdm's console bar.
_TQDM_NOTEBOOK_SHIM = """\
# tqdm.notebook -> console tqdm when ipywidgets is missing (notebook-to-api)
import sys as _nb_sys9
import types as _nb_types9

try:
    import ipywidgets  # noqa: F401  (a widget-capable environment keeps the real one)
except ImportError:
    try:
        import tqdm as _nb_tqdm9
        import tqdm.std as _nb_tqdm_std9
    except ImportError:
        pass
    else:
        _nb_tqdm_nb9 = _nb_types9.ModuleType("tqdm.notebook")
        _nb_tqdm_nb9.tqdm = _nb_tqdm_nb9.tqdm_notebook = _nb_tqdm_std9.tqdm
        _nb_tqdm_nb9.trange = _nb_tqdm_nb9.tnrange = _nb_tqdm_std9.trange
        _nb_sys9.modules["tqdm.notebook"] = _nb_tqdm_nb9
        _nb_tqdm9.notebook = _nb_tqdm_nb9
        _nb_tqdm9.tqdm_notebook = _nb_tqdm_std9.tqdm
        _nb_tqdm9.tnrange = _nb_tqdm_std9.trange

"""


def _uses_tqdm_notebook(code_cells):
    """True when a cell imports tqdm's notebook bar: `tqdm.notebook`, or
    `tqdm_notebook` / `tnrange` from tqdm."""
    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "tqdm.notebook" for alias in node.names
            ):
                return True
            if isinstance(node, ast.ImportFrom) and not node.level and (
                node.module == "tqdm.notebook"
                or (node.module == "tqdm" and any(
                    alias.name in ("notebook", "tqdm_notebook", "tnrange") for alias in node.names
                ))
            ):
                return True
    return False


def _uses_kaggle_secrets(code_cells):
    return "kaggle_secrets" in {
        name for cell in code_cells for name in extract_imports_from_code(cell)
    }


def _uses_colab_userdata(code_cells):
    """True when a cell imports a Colab module the shim stands in for
    (`userdata` or `drive`, see _COLAB_SHIMMED_MODULES)."""
    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level:
                if node.module == "google.colab" and any(
                    f"google.colab.{a.name}" in _COLAB_SHIMMED_MODULES for a in node.names
                ):
                    return True
                if node.module in _COLAB_SHIMMED_MODULES:
                    return True
            elif isinstance(node, ast.Import):
                if any(a.name in _COLAB_SHIMMED_MODULES for a in node.names):
                    return True
    return False


# Calls that serve or wait forever. Run at module level they are what `import`
# of the notebook is stuck on, so the compiled app never finishes starting:
# a Gradio/Dash/Tk demo's launch line left at the bottom of the notebook, a
# Flask/uvicorn dev server, or a long sleep.
_BLOCKING_CALL_NAMES = frozenset({
    "launch", "mainloop", "serve_forever", "run_forever", "run_server", "run_app",
    "run_polling", "start_polling", "notebook_login",
})
_BLOCKING_CALL_LABELS = frozenset({
    "uvicorn.run", "app.run", "hypercorn.run", "waitress.serve", "web.run_app",
    "asyncio.get_event_loop().run_forever",
})
_LONG_SLEEP_SECONDS = 60


# Interactive debugger entry points left in from debugging the notebook. They
# stop and wait on a debugger prompt over stdin, which the compiled app
# doesn't have: at module level the app never finishes starting, and inside
# an endpoint's function every request to it hangs (or dies with BdbQuit).
_DEBUGGER_CALL_LABELS = frozenset({
    "pdb.set_trace", "pdb.post_mortem", "pdb.pm", "ipdb.set_trace",
    "ipdb.post_mortem", "ipdb.pm", "pudb.set_trace", "debugger.set_trace",
    "IPython.embed", "embed.embed", "code.interact", "IPython.start_ipython",
})


def _is_debugger_call(label, name, base):
    return (name == "breakpoint" and base is None) or label in _DEBUGGER_CALL_LABELS


# Calls that end the process. Jupyter catches SystemExit and only warns, so a
# notebook's `if df.empty: sys.exit()` guard (or a stray `exit()`) did no
# harm there; run at module level when the compiled app imports the notebook,
# it stops the server before it serves anything.
_EXIT_CALL_LABELS = frozenset({"sys.exit", "os._exit", "os.abort"})


def _is_exit_call(label, name, base):
    return (name in ("exit", "quit") and base is None) or label in _EXIT_CALL_LABELS


def _raises_system_exit(node):
    """True for `raise SystemExit` / `raise SystemExit(...)`."""
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return isinstance(exc, ast.Name) and exc.id == "SystemExit"


def _call_hazard_kind(label, name, base):
    """"debugger_call" / "exit_call" for a resolved call label, else None."""
    if _is_debugger_call(label, name, base):
        return "debugger_call"
    if _is_exit_call(label, name, base):
        return "exit_call"
    return None


def _file_read_path(name, base, call, constants=None, probe=False):
    """The relative path a data-file read (`pd.read_csv(...)`, read-mode
    `open(...)`, `np.load(...)`, ...) reads, else None."""
    label = f"{base}.{name}" if base else name
    is_read = (
        name in _FILE_READ_CALLS
        or (name == "load" and base in _FILE_LOAD_BASES)
        or label in _FILE_READ_LABELS
        or (name == "loadmat" and base is None)  # scipy.io.loadmat(...)
        or name in _MODEL_FILE_CALLS
        or (name == "load" and base in _GENSIM_LOAD_BASES)
        or (
            name in _MODEL_FILE_KEYWORD_CALLS
            and any(kw.arg in ("model_file", "model_path") for kw in call.keywords)
        )
    )
    if not is_read:
        return None
    if (name == "open" and base is None) or label in _MODE_CHECKED_LABELS:
        mode = call.args[1] if len(call.args) > 1 else next(
            (kw.value for kw in call.keywords if kw.arg == "mode"), None
        )
        if (
            isinstance(mode, ast.Constant) and isinstance(mode.value, str)
            and set(mode.value) & set("wax+")
        ):
            return None
    if probe:  # only "is this a read call?"
        return True
    path = _relative_literal_path(call, constants)
    if name in ("YOLO", "RTDETR") and path and _AUTO_DOWNLOADED_YOLO.match(path):
        return None
    return None if path in _NON_FILE_PATHS or (path or "").startswith("file:") else path


def _path_arg_node(call):
    """The expression a read's path is passed as: `open(<node>)`,
    `pd.read_csv(filepath_or_buffer=<node>)`, `Path(<node>).read_text()`."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in ("read_text", "read_bytes"):
        receiver = func.value
        return receiver.args[0] if isinstance(receiver, ast.Call) and len(receiver.args) == 1 else None
    return call.args[0] if call.args else next(
        (kw.value for kw in call.keywords if kw.arg in _PATH_KEYWORDS),
        None,
    )


def _redirectable_path_constants(trees):
    """{name: its assigned string literal node} for module-level path
    constants (see _module_path_constants) written as a one-line string
    literal and used *only* as the path of data-file reads anywhere in the
    notebook: `LABELS = "labels.txt"` with every `LABELS` an `open(LABELS)`
    or `pd.read_csv(LABELS)`. Pointing such an assignment at the shipped copy
    changes nothing but where those reads look; a name also printed, written
    to, compared or passed elsewhere is left alone."""
    constants = _module_path_constants(trees)
    aliases = {}
    for tree in trees:
        aliases.update(_import_aliases(tree))
    read_args = set()
    for tree in trees:
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            label, name, base = _call_label(call.func)
            if label is None:
                continue
            _, name, base = _resolve_import_alias(label, name, base, aliases)
            arg = _path_arg_node(call)
            if isinstance(arg, ast.Name) and _file_read_path(name, base, call, constants):
                read_args.add(id(arg))
    other_uses = {
        node.id for tree in trees for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        and id(node) not in read_args
    }
    found = {}
    for tree in trees:
        for node in tree.body:
            if (
                isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in constants
                and node.targets[0].id not in other_uses
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                and node.value.lineno == node.value.end_lineno
            ):
                found[node.targets[0].id] = node.value
    return found


def _request_read_is_redirectable(call, redirectable):
    arg = _path_arg_node(call)
    return (
        isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        or isinstance(arg, ast.Name) and arg.id in redirectable
        or isinstance(arg, (ast.JoinedStr, ast.BinOp, ast.Call)) and arg.lineno == arg.end_lineno
    )


def _function_data_reads(tree, aliases, constants):
    """[(function name, call, path)] for relative data-file reads inside
    function bodies, each attributed to its outermost function."""
    found = []
    seen = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(func):
            if not isinstance(call, ast.Call) or id(call) in seen:
                continue
            seen.add(id(call))
            label, name, base = _call_label(call.func)
            if label is None:
                continue
            _, name, base = _resolve_import_alias(label, name, base, aliases)
            path = _file_read_path(name, base, call, constants)
            if path is None and _file_read_path(name, base, call, constants, probe=True):
                path = _dynamic_path_pattern(_path_arg_node(call), constants)
            if path is not None:
                found.append((func.name, call, path))
    return found


_PATH_JOIN_CALLS = ("os.path.join", "path.join", "osp.join")


def _path_pattern_piece(node, constants):
    """The glob piece a path expression contributes: literal text for
    strings and known path constants, "*" for a runtime value, joined
    through f-strings, `os.path.join(...)`, `Path(...)` and `/`. None when a
    literal itself contains glob characters (it can't be told apart)."""
    literal = _literal_path_value(node, constants)
    if literal is not None:
        return None if any(ch in literal for ch in "*?[]") else literal
    if isinstance(node, ast.JoinedStr):
        pieces = []
        for part in node.values:
            piece = _path_pattern_piece(part, constants) if isinstance(part, ast.Constant) else "*"
            if piece is None:
                return None
            pieces.append(piece)
        return "".join(pieces)
    parts = None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        parts = [node.left, node.right]
    elif isinstance(node, ast.Call) and not node.keywords and node.args:
        _, name, _ = _call_label(node.func)
        if ast.unparse(node.func) in _PATH_JOIN_CALLS or name in ("Path", "PurePath", "PosixPath"):
            parts = node.args
    if parts is None:
        return "*"
    pieces = [_path_pattern_piece(part, constants) for part in parts]
    if any(piece is None or piece.startswith("/") for piece in pieces[1:]) or pieces[0] is None:
        return None
    return "/".join(piece.rstrip("/") for piece in pieces[:-1]) + "/" + pieces[-1]


def _dynamic_path_pattern(node, constants=None):
    """A glob pattern for a read path built partly at runtime with a fixed
    leading folder -- f"data/{city}.csv", os.path.join("data", name),
    Path("data") / f"{x}.json" -> "data/*.csv", "data/*", "data/*.json" -- so
    a read that picks its file per request ships every candidate. None
    when the path is fully literal (handled elsewhere), absolute or
    climbing, or its first folder isn't literal."""
    if node is None or _literal_path_value(node, constants) is not None:
        return None
    if not isinstance(node, (ast.JoinedStr, ast.BinOp, ast.Call)):
        return None
    pattern = _path_pattern_piece(node, constants)
    if pattern is None:
        return None
    pattern = re.sub(r"\*+", "*", pattern)
    if pattern.startswith("./"):
        pattern = pattern[2:]
    head, slash, _ = pattern.partition("/")
    if not slash or not head or "*" in head or ".." in pattern.split("/"):
        return None
    return pattern if "*" in pattern and _is_relative_path(pattern) else None


def _path_pattern_matches(pattern, shipped):
    """The shipped relative paths an f-string read pattern can name."""
    return [path for path in shipped if fnmatch.fnmatchcase(path, pattern)]


# A read inside a function runs when an endpoint is called, long after the
# import-time chdir (see _DATA_FILES_CHDIR_PRELUDE) was undone, so a relative
# path resolves against wherever the app was launched (/app in the generated
# Dockerfile) and every call fails. Shipped files read through a plain string
# literal are pointed at their shipped copy instead.
_DATA_PATH_HELPER = """\
# Request-time data files ship beside this file (notebook-to-api)
import os as _nb_os7
_NB_DATA_DIR = _nb_os7.path.dirname(_nb_os7.path.abspath(__file__))


def _nb_data_path(relative):
    return _nb_os7.path.join(_NB_DATA_DIR, relative)

"""


def _rewrite_request_time_reads(code_cells, shipped):
    """`code_cells` with each function-scope read of a shipped file pointed
    at `_nb_data_path("<path>")`: the string literal it reads, or the
    one-line assignment of the path constant it reads through (see
    _redirectable_path_constants). Unparseable cells come back unchanged."""
    parsed = {}
    for index, cell in enumerate(code_cells):
        try:
            parsed[index] = ast.parse(cell)
        except SyntaxError:
            continue
    trees = list(parsed.values())
    constants = _module_path_constants(trees)
    redirectable = _redirectable_path_constants(trees)
    aliases = {}
    for tree in trees:
        aliases.update(_import_aliases(tree))
    targets = {index: [] for index in parsed}
    redirected = set()
    for index, tree in parsed.items():
        for _, call, path in _function_data_reads(tree, aliases, constants):
            if "*" in path:
                if not _path_pattern_matches(path, shipped):
                    continue
            elif path not in shipped and not _shipped_as_directory(path, shipped):
                continue
            node = _path_arg_node(call)
            dynamic = (ast.JoinedStr, ast.BinOp, ast.Call)
            if isinstance(node, (ast.Constant,) + dynamic) and node.lineno == node.end_lineno:
                targets[index].append(node)
            elif isinstance(node, ast.Name) and node.id in redirectable:
                redirected.add(node.id)
    for index, tree in parsed.items():
        for node in tree.body:
            if (
                isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in redirected
                and redirectable[node.targets[0].id] is node.value
            ):
                targets[index].append(node.value)

    cells = list(code_cells)
    for index, nodes in targets.items():
        if not nodes:
            continue
        lines = cells[index].splitlines(keepends=True)
        for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset), reverse=True):
            raw = lines[node.lineno - 1].encode("utf-8")
            if not isinstance(node, ast.Constant):
                source = raw[node.col_offset:node.end_col_offset].decode("utf-8")
                replacement = f"_nb_data_path({source})".encode("utf-8")
            else:
                replacement = f"_nb_data_path({node.value!r})".encode("utf-8")
            raw = raw[:node.col_offset] + replacement + raw[node.end_col_offset:]
            lines[node.lineno - 1] = raw.decode("utf-8")
        cells[index] = "".join(lines)
    return cells


def _find_function_call_hazards(tree, aliases, cell_number):
    """Debugger hazards inside function bodies, attributed to the outermost
    enclosing function (each call reported once). Exit calls there are not
    reported: the generated endpoints already turn SystemExit into a failed
    call."""
    hazards = []
    seen = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(func):
            if not isinstance(call, ast.Call) or id(call) in seen:
                continue
            seen.add(id(call))
            label, name, base = _call_label(call.func)
            if label is None:
                continue
            resolved_label, name, base = _resolve_import_alias(label, name, base, aliases)
            if _is_debugger_call(resolved_label, name, base):
                hazards.append({
                    "kind": "debugger_call", "call": label, "path": None,
                    "cell": cell_number, "line": call.lineno, "function": func.name,
                })
            elif _is_stdin_prompt(name, base) and name not in _local_names(func):
                # No terminal behind a request: input() raises EOFError (or
                # waits forever on an attached stdin) on every call.
                hazards.append({
                    "kind": "request_input", "call": label, "path": None,
                    "cell": cell_number, "line": call.lineno, "function": func.name,
                })
    return hazards


def _is_stdin_prompt(name, base):
    """`input(...)` or getpass's `getpass(...)` (alias-resolved)."""
    return (name == "input" and base is None) or (name == "getpass" and base in (None, "getpass"))


def _local_names(func):
    """Parameter names of `func`, which shadow a builtin like `input`."""
    args = func.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    names += [a.arg for a in (args.vararg, args.kwarg) if a]
    return set(names)


# Names only an IPython magic set: `files = !ls`, `out = %sx cmd`,
# `t = %timeit -o f()`, `%%capture out`, `%store -r model`. The parser
# comments those lines out (they aren't Python), so a later use of the name
# raises NameError in the compiled app.
_MAGIC_BINDING_PATTERNS = (
    re.compile(r"^#\s*(?P<names>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=\s*[!%]"),
    re.compile(r"^#\s*%%capture(?:\s+--?\w+)*\s+(?P<names>[A-Za-z_]\w*)\s*$"),
    re.compile(r"^#\s*%store\s+-r\s+(?P<names>[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*)\s*$"),
)


def _magic_variable_hazards(code_cells, trees):
    """"magic_variable" hazards for each name a commented-out magic line
    bound (see _MAGIC_BINDING_PATTERNS) that the notebook's Python reads
    but never assigns itself."""
    loaded, stored = set(), set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                (loaded if isinstance(node.ctx, ast.Load) else stored).add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stored.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                stored.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.arg):
                stored.add(node.arg)
    hazards = []
    for cell_number, cell in enumerate(code_cells, start=1):
        unsafe_lines = _lines_inside_multiline_strings(cell)
        for line_number, line in enumerate(cell.split("\n"), start=1):
            if line_number in unsafe_lines:
                continue
            text = line.strip()
            if text.startswith("pass  #"):
                text = text[len("pass  "):]
            for pattern in _MAGIC_BINDING_PATTERNS:
                match = pattern.match(text)
                if not match:
                    continue
                for name in re.split(r"[\s,]+", match.group("names").strip()):
                    if name in loaded and name not in stored:
                        hazards.append({
                            "kind": "magic_variable", "call": text.lstrip("# ").strip(),
                            "path": name, "cell": cell_number, "line": line_number,
                        })
                break
    return hazards


def call_hazard_detail(hazard):
    """(what, consequence) wording for a "debugger_call", "exit_call",
    "request_input" or "request_read" hazard."""
    if hazard["kind"] == "request_read":
        return (
            f"{hazard['call']}({hazard['path']!r}) inside {hazard['function']}() reads a file "
            "relative to the working directory, which the compiled app can't point at a "
            f"shipped copy{unshipped_reason_text(hazard)}",
            f"every call to {hazard['function']}() will fail",
        )
    call = hazard["call"] if hazard["call"].startswith("raise ") else f"{hazard['call']}()"
    if hazard["kind"] == "request_input":
        return (
            f"`{call}` inside {hazard['function']}() prompts on stdin, which a request "
            "never has -- take the value as a parameter instead",
            f"every call to {hazard['function']}() will fail or hang",
        )
    if hazard["kind"] == "exit_call":
        return f"`{call}` ends the process (Jupyter only warned and carried on)", "the app will exit on startup"
    what = f"`{call}` opens an interactive debugger on stdin, which the compiled app doesn't have"
    if hazard.get("function"):
        return what, f"every call to {hazard['function']}() will hang"
    return what, "the app will hang on startup"


# Hard-coded CUDA placement. It worked on the author's GPU machine; the
# compiled app's Docker image (python:*-slim) has no CUDA, so on a CPU-only
# host `model.cuda()` / `.to("cuda")` / `torch.device("cuda")` raise as the
# notebook is imported. Code behind a `torch.cuda.is_available()` check (or a
# try block) is left alone -- it already copes.
_GPU_AVAILABILITY_CHECKS = frozenset({"is_available", "device_count"})


def _is_cuda_literal(node):
    return (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.split(":")[0].lower() == "cuda"
    )


def _checks_gpu_availability(calls):
    return any(
        isinstance(call.func, ast.Attribute) and call.func.attr in _GPU_AVAILABILITY_CHECKS
        for call in calls
    )


def _is_gpu_call(label, name, call):
    """True for a call that puts something on a CUDA device unconditionally."""
    func = call.func
    if name == "cuda" and isinstance(func, ast.Attribute):
        return True
    if isinstance(func, ast.Attribute) and ast.unparse(func) == "torch.cuda.set_device":
        return True
    device = call.args[0] if call.args else None
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    if name == "to" and isinstance(func, ast.Attribute):
        return _is_cuda_literal(device) or _is_cuda_literal(keywords.get("device"))
    if label == "torch.device":
        return _is_cuda_literal(device)
    return _is_cuda_literal(keywords.get("map_location"))


def _is_blocking_call(label, name, call):
    if name in _BLOCKING_CALL_NAMES or label in _BLOCKING_CALL_LABELS:
        return True
    if label == "time.sleep" and call.args:
        arg = call.args[0]
        return (
            isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float))
            and arg.value >= _LONG_SLEEP_SECONDS
        )
    return False


# `!wget ...` style lines ran in Jupyter's shell and are only comments in the
# compiled app, so whatever they downloaded or unpacked (a dataset, a model
# checkpoint, a cloned repo) does not exist there -- the reads that follow
# fail on startup. Reported, never executed.
_SHELL_FETCH_COMMANDS = (
    "wget", "curl", "gdown", "kaggle", "unzip", "tar", "git", "gsutil", "aws",
    "huggingface-cli", "dvc", "7z", "gunzip",
)
_SHELL_FETCH_PATTERN = re.compile(
    r"^#[ \t]*!(?:[ \t]*sudo)?[ \t]*(?P<command>" + "|".join(_SHELL_FETCH_COMMANDS) + r")\b.*$",
    re.MULTILINE,
)
# The same commands as lines of a shell cell (`%%bash`, `%%sh`, `%%script
# bash`, `%%system`), whose whole body the parser comments out.
_SHELL_CELL_PATTERN = re.compile(r"^#[ \t]*%%(?:bash|sh|system|script[ \t]+(?:ba|z)?sh)\b")
_SHELL_CELL_FETCH_PATTERN = re.compile(
    r"^#[ \t]*(?:sudo[ \t]+)?(?P<command>" + "|".join(_SHELL_FETCH_COMMANDS) + r")\b.*$",
    re.MULTILINE,
)


def _colab_import(node):
    """"google.colab[.x]" when `node` imports Google Colab's module, else None."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in _COLAB_SHIMMED_MODULES:
                continue
            if alias.name == "google.colab" or alias.name.startswith("google.colab."):
                return alias.name
    elif isinstance(node, ast.ImportFrom) and not node.level:
        if node.module == "google.colab" and all(
            f"google.colab.{alias.name}" in _COLAB_SHIMMED_MODULES for alias in node.names
        ):
            return None  # the shim covers these (see _COLAB_USERDATA_SHIM)
        if node.module in _COLAB_SHIMMED_MODULES:
            return None
        if node.module == "google.colab" or (node.module or "").startswith("google.colab."):
            return node.module
        if node.module == "google" and any(alias.name == "colab" for alias in node.names):
            return "google.colab"
    return None


def _find_import_time_hazards(code_cells, notebook_path=None):
    """[{"kind", "call", "path", "cell", "line"}] for top-level statements
    that run when the compiled app imports the notebook and can't succeed
    there: `input()` (EOFError -- nothing is attached to stdin), and reads of
    a *relative* data file (`pd.read_csv("data.csv")`, `open("x.json")`,
    `np.load(...)`, ...). The compile copies no data files next to the app
    and the app runs from wherever it's launched, so such a notebook
    compiled fine and then crashed on startup -- every endpoint down -- far
    from where the notebook was authored. Reported, never rewritten.

    Only module-level code is scanned: function/class bodies run on demand
    and an `if __name__ == ...:` block never runs on import. Write-mode
    `open()` calls are ignored. `cell` is the 1-based position among
    `code_cells`, `line` the line within that cell.
    """
    hazards = []
    current_aliases = [{}]
    notebook_aliases = {}
    gpu_guard = [0]

    def visit(statements, cell_number):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.If) and _is_main_guard(node):
                visit(node.orelse, cell_number)
                continue
            guards_gpu = isinstance(node, ast.Try) or (
                isinstance(node, (ast.If, ast.While))
                and _checks_gpu_availability(_statement_calls(node))
            )
            gpu_guard[0] += guards_gpu
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    visit([child], cell_number)
            gpu_guard[0] -= guards_gpu
            statement_checks_gpu = _checks_gpu_availability(_statement_calls(node))
            if isinstance(node, ast.Raise) and _raises_system_exit(node):
                hazards.append({
                    "kind": "exit_call", "call": "raise SystemExit", "path": None,
                    "cell": cell_number, "line": node.lineno,
                })
            colab_module = _colab_import(node)
            if colab_module:
                hazards.append({
                    "kind": "colab_import", "call": f"import {colab_module}", "path": None,
                    "cell": cell_number, "line": node.lineno,
                })
            for call in _statement_calls(node):
                label, name, base = _call_label(call.func)
                if label is None:
                    continue
                # `label` stays as written (it is what gets reported); the
                # resolved form only drives which calls are recognised.
                resolved_label, name, base = _resolve_import_alias(
                    label, name, base, current_aliases[0]
                )
                if (
                    not gpu_guard[0] and not statement_checks_gpu
                    and _is_gpu_call(resolved_label, name, call)
                ):
                    hazards.append({
                        "kind": "gpu_call", "call": label, "path": None,
                        "cell": cell_number, "line": call.lineno,
                    })
                kind = _call_hazard_kind(resolved_label, name, base)
                origin = current_aliases[0].get((label.split(".")[0]), "")
                if resolved_label == "files.upload" and origin.startswith("google.colab"):
                    # The shim's files.upload() can only raise: there's no
                    # browser to upload from.
                    kind = "colab_upload"
                if kind:
                    hazards.append({
                        "kind": kind, "call": label, "path": None,
                        "cell": cell_number, "line": call.lineno,
                    })
                    continue
                if _is_blocking_call(resolved_label, name, call):
                    hazards.append({
                        "kind": "blocking_call", "call": label, "path": None,
                        "cell": cell_number, "line": call.lineno,
                    })
                    continue
                if _is_stdin_prompt(name, base):
                    hazards.append({
                        "kind": "input", "call": label, "path": None,
                        "cell": cell_number, "line": call.lineno,
                    })
                    continue
                pattern = _relative_listing_pattern(resolved_label, call, path_constants)
                if pattern is not None:
                    hazards.append({
                        "kind": "dir_read", "call": label, "path": pattern,
                        "cell": cell_number, "line": call.lineno,
                    })
                    continue
                path = _file_read_path(name, base, call, path_constants)
                if path is None and _file_read_path(name, base, call, path_constants, probe=True):
                    # A loop over f"data/{name}.csv" runs under the import-time
                    # chdir, so shipping every match is all it needs.
                    path = _dynamic_path_pattern(_path_arg_node(call), path_constants)
                if path is not None:
                    hazards.append({
                        "kind": "file_read", "call": label, "path": path,
                        "cell": cell_number, "line": call.lineno,
                    })

    parsed = []
    for cell_number, cell in enumerate(code_cells, start=1):
        try:
            parsed.append((cell_number, ast.parse(cell)))
        except SyntaxError:
            continue
    path_constants = _module_path_constants([tree for _, tree in parsed])
    redirectable_constants = _redirectable_path_constants([tree for _, tree in parsed])

    for cell_number, tree in parsed:
        current_aliases[0] = _import_aliases(tree)
        visit(tree.body, cell_number)
        # Functions run after every cell has, so imports from any earlier
        # cell are in scope for them.
        notebook_aliases.update(current_aliases[0])
        hazards.extend(_find_function_call_hazards(tree, notebook_aliases, cell_number))
        for function, call, path in _function_data_reads(tree, notebook_aliases, path_constants):
            hazards.append({
                "kind": "request_read", "call": _call_label(call.func)[0], "path": path,
                "cell": cell_number, "line": call.lineno, "function": function,
                "rewritable": _request_read_is_redirectable(call, redirectable_constants),
            })

    for cell_number, cell in enumerate(code_cells, start=1):
        unsafe_lines = _lines_inside_multiline_strings(cell)
        for match in _SHELL_FETCH_PATTERN.finditer(cell):
            line = cell.count("\n", 0, match.start()) + 1
            if line not in unsafe_lines:
                hazards.append({
                    "kind": "shell_command", "call": f"!{match.group('command')}", "path": None,
                    "cell": cell_number, "line": line,
                })
        if _SHELL_CELL_PATTERN.match(cell.lstrip()):
            for match in _SHELL_CELL_FETCH_PATTERN.finditer(cell):
                hazards.append({
                    "kind": "shell_command", "call": match.group("command"), "path": None,
                    "cell": cell_number, "line": cell.count("\n", 0, match.start()) + 1,
                })

    hazards.extend(_magic_variable_hazards(code_cells, [tree for _, tree in parsed]))

    if notebook_path:
        shipped = find_data_files(notebook_path, hazards=hazards)
        directory = Path(notebook_path).resolve().parent

        def path_shipped(path):
            if path in shipped:
                return True
            files = _directory_read_files(directory, path)
            return bool(files) and all(name in shipped for name in files)

        def fully_shipped(item):
            if item["kind"] in ("file_read", "request_read") and "*" in item["path"]:
                files = _expand_listing_pattern(directory, item["path"])
                redirected = item["kind"] == "file_read" or item["rewritable"]
                return redirected and bool(files) and all(f in shipped for f in files)
            if item["kind"] == "file_read":
                return path_shipped(item["path"])
            if item["kind"] == "request_read":
                return item["rewritable"] and path_shipped(item["path"])
            if item["kind"] == "dir_read":
                files = _expand_listing_pattern(directory, item["path"])
                return bool(files) and all(path in shipped for path in files)
            return False

        hazards = [item for item in hazards if not fully_shipped(item)]
        for item in hazards:
            reason = _unshipped_reason(item, directory, shipped)
            if reason:
                item["reason"] = reason

    hazards.sort(key=lambda item: (item["cell"], item["line"]))
    return hazards


# Files a compile will copy into the app so an import-time read of them
# works; past this total the files stay behind and the read is still
# reported as a hazard rather than bloating the image.
MAX_SHIPPED_DATA_BYTES = 50 * 1024 * 1024

# Raise (or lower) the cap for one compile without code changes -- a notebook
# whose model file is 120 MB otherwise had no way to ship it at all.
MAX_SHIPPED_DATA_ENV = "NOTEBOOK_TO_API_MAX_DATA_MB"


def max_shipped_data_bytes():
    """MAX_SHIPPED_DATA_BYTES, or NOTEBOOK_TO_API_MAX_DATA_MB megabytes when
    that's set to a positive number (an unusable value is ignored)."""
    raw = os.environ.get(MAX_SHIPPED_DATA_ENV, "").strip()
    try:
        megabytes = float(raw)
    except ValueError:
        return MAX_SHIPPED_DATA_BYTES
    return int(megabytes * 1024 * 1024) if megabytes > 0 else MAX_SHIPPED_DATA_BYTES


_UNSHIPPED_REASON_TEXT = {
    "missing": "it doesn't exist beside the notebook",
    "outside": "it's outside the notebook's directory",
    "directory": "it's an empty directory",
    "size_limit": (
        "shipping it would pass the data size limit "
        f"(raise it with {MAX_SHIPPED_DATA_ENV})"
    ),
    "no_matches": "nothing beside the notebook matches it",
    "not_redirectable": (
        "its path isn't a string literal, a read-only path constant, or a one-line "
        "f-string / os.path.join / Path expression with a literal leading folder, "
        "so it can't be pointed at the shipped copy"
    ),
}


def unshipped_reason_text(hazard):
    """" (<why>)" for a data hazard carrying a "reason", else ""."""
    text = _UNSHIPPED_REASON_TEXT.get(hazard.get("reason"))
    return f" ({text})" if text else ""


def _unshipped_reason(item, directory, shipped):
    """Why a data-file hazard's file didn't ship (see _UNSHIPPED_REASON_TEXT),
    or None for a hazard that isn't about a data file."""
    if item["kind"] == "dir_read":
        files = _expand_listing_pattern(directory, item["path"])
        return "size_limit" if files else "no_matches"
    if item["kind"] not in ("file_read", "request_read"):
        return None
    if "*" in item["path"]:
        files = _expand_listing_pattern(directory, item["path"])
        if not files:
            return "no_matches"
        return "not_redirectable" if all(f in shipped for f in files) else "size_limit"
    if item["kind"] == "request_read" and (
        item["path"] in shipped or _shipped_as_directory(item["path"], shipped)
    ):
        return "not_redirectable"
    relative = Path(item["path"])
    candidate = (directory / relative).resolve()
    if ".." in relative.parts or (candidate != directory and directory not in candidate.parents):
        return "outside"
    if candidate.is_dir():
        return "size_limit" if _directory_read_files(directory, item["path"]) else "directory"
    if not candidate.is_file():
        return "missing"
    return "size_limit"


def find_data_files(notebook_path, code_cells=None, hazards=None):
    """{relative path as written: absolute Path} for the relative data files
    the notebook reads at import time (`pd.read_csv("sales.csv")`,
    `open("config.json")`) that exist beside the notebook -- what the
    compile ships next to the runtime module. A read naming a directory
    (`load_model("saved_model")`) ships every file under it. Paths that
    climb out of the notebook's directory, empty directories, and anything past
    MAX_SHIPPED_DATA_BYTES in total are left out.
    """
    if not notebook_path:
        return {}
    if hazards is None:
        hazards = _find_import_time_hazards(code_cells or [])

    directory = Path(notebook_path).resolve().parent
    found = {}
    total = 0

    wanted = []
    for item in hazards:
        if item["kind"] in ("file_read", "request_read") and "*" in item["path"]:
            wanted += list(_expand_listing_pattern(directory, item["path"]))
        elif item["kind"] in ("file_read", "request_read"):
            wanted += list(_directory_read_files(directory, item["path"])) or [item["path"]]
    # `%run script.py` files are shipped too (see _run_magic_scripts).
    wanted += _run_magic_scripts(code_cells or [])
    # So are the files a top-level directory listing finds.
    for item in hazards:
        if item["kind"] == "dir_read":
            wanted += list(_expand_listing_pattern(directory, item["path"]))

    limit = max_shipped_data_bytes()
    for path in wanted:
        if path in found:
            continue
        relative = Path(path)
        if ".." in relative.parts or relative.is_absolute():
            continue
        candidate = (directory / relative).resolve()
        try:
            candidate.relative_to(directory)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        size = candidate.stat().st_size
        if total + size > limit:
            continue
        total += size
        found[path] = candidate

    return found


# `%run script.py` executes the file in the notebook's own namespace. The
# parser leaves it as a "# %run ..." comment, so the script's definitions
# never existed in the compiled app.
_RUN_MAGIC_PATTERN = re.compile(
    r"^# %run(?:[ \t]+-\w+)*[ \t]+(?P<path>\S+\.py)(?:[ \t]+.*)?$", re.MULTILINE,
)

_RUN_SCRIPT_HELPER = """\
# %run helper (notebook-to-api): run a shipped script in this module's namespace
def _nb_run_script(relative):
    import os as _os, runpy as _runpy
    _ns = _runpy.run_path(
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), relative),
        init_globals=dict(globals()),
    )
    globals().update(
        {k: v for k, v in _ns.items() if not (k.startswith("__") and k.endswith("__"))}
    )

"""


def _normalized_run_path(path):
    return path[2:] if path.startswith("./") else path


def _run_magic_scripts(code_cells):
    """Relative `.py` paths named by `%run` magics, first-seen order, unique;
    absolute paths, `..` paths and lines inside strings are skipped."""
    scripts = []
    for cell in code_cells:
        unsafe_lines = _lines_inside_multiline_strings(cell)
        for match in _RUN_MAGIC_PATTERN.finditer(cell):
            if cell.count("\n", 0, match.start()) + 1 in unsafe_lines:
                continue
            path = _normalized_run_path(match.group("path"))
            if path.startswith(("/", "~")) or ".." in Path(path).parts:
                continue
            if path not in scripts:
                scripts.append(path)
    return scripts


_ENV_MAGIC_PATTERN = re.compile(
    r"^[ \t]*#[ \t]*%(?:set_)?env[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:[ \t]*=[ \t]*|[ \t]+)(?P<value>\S.*?)[ \t]*$",
    re.MULTILINE,
)


def _env_magic_values(code_cells):
    """{NAME: value} for the `%env NAME=value` / `%env NAME value` /
    `%set_env` magics (the parser leaves each as a "# %env ..." comment), the
    last assignment winning. A bare `%env NAME` only queries a variable, and
    lines inside a multi-line string are text, not magics.

    The magic sets the variable in Jupyter's process; commented out, the
    compiled app never got it, so later os.environ reads failed.
    """
    values = {}
    for cell in code_cells:
        unsafe_lines = _lines_inside_multiline_strings(cell)
        for match in _ENV_MAGIC_PATTERN.finditer(cell):
            if cell.count("\n", 0, match.start()) + 1 in unsafe_lines:
                continue
            values[match.group("name")] = match.group("value")
    return values


def _env_magic_prelude(env_values):
    """Module-top code applying `env_values` with setdefault, so a value the
    deployment sets itself still wins."""
    if not env_values:
        return ""
    lines = ["# %env magics from the notebook (notebook-to-api); deployment values win\n",
             "import os as _nb_os4\n"]
    lines += [
        f"_nb_os4.environ.setdefault({name!r}, {value!r})\n"
        for name, value in env_values.items()
    ]
    return "".join(lines) + "\n"


_DOTENV_LOADERS = frozenset({"load_dotenv", "dotenv_values"})
_DOTENV_KEY_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def dotenv_files(code_cells, notebook_path):
    """[Path] of the .env file(s) the notebook loads with `load_dotenv()` /
    `dotenv_values()` -- the default ".env" beside the notebook, or a
    literal relative `dotenv_path`. The files themselves rightly never
    ship, but their variable names feed .env.example and
    docker-compose.yml (see _dotenv_keys)."""
    if not notebook_path:
        return []
    directory = Path(notebook_path).resolve().parent
    paths = []
    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            label, name, base = _call_label(node.func)
            if label is None:
                continue
            _, name, _ = _resolve_import_alias(label, name, base, aliases)
            if name not in _DOTENV_LOADERS:
                continue
            arg = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg == "dotenv_path"), None
            )
            if arg is None:
                paths.append(".env")
            elif isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and _is_relative_path(arg.value) and ".." not in Path(arg.value).parts:
                paths.append(arg.value)
    return [directory / relative for relative in dict.fromkeys(paths)]


def _dotenv_keys(code_cells, notebook_path):
    """The variable names (never the values) in the .env files the notebook
    loads (see dotenv_files)."""
    keys = []
    for path in dotenv_files(code_cells, notebook_path):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            match = _DOTENV_KEY_PATTERN.match(line)
            if match and match.group(1) not in keys:
                keys.append(match.group(1))
    return keys


def _find_notebook_env_vars(code_cells, notebook_path=None):
    """[{"name", "required"}] (sorted by name) for the environment variables
    the notebook reads by literal name: `os.environ["X"]`,
    `os.environ.get("X"[, default])` and `os.getenv("X"[, default])` (also
    via `from os import environ, getenv`), anywhere in the code. `required`
    is true for a subscript read, which raises KeyError when unset, and for
    a get/getenv with no default, which hands the notebook None.

    A notebook's API keys and connection strings live there, but nothing
    carried them into the deployment files: docker-compose.yml and
    .env.example named only the app's own NOTEBOOK_API_* variables, so the
    container started without them. The app's own NOTEBOOK_API_* names are
    left out.
    """
    found = {}

    def is_environ(node):
        return (
            (isinstance(node, ast.Attribute) and node.attr == "environ"
             and isinstance(node.value, ast.Name) and node.value.id == "os")
            or (isinstance(node, ast.Name) and node.id == "environ")
        )

    def record(name, required):
        if name.startswith("NOTEBOOK_API_") or not name.isidentifier():
            return
        found[name] = found.get(name, False) or required

    for cell in code_cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                and is_environ(node.value)
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
            ):
                record(node.slice.value, True)
            elif isinstance(node, ast.Call) and node.args:
                func = node.func
                is_get = (
                    isinstance(func, ast.Attribute) and func.attr == "get" and is_environ(func.value)
                )
                is_getenv = (
                    (isinstance(func, ast.Attribute) and func.attr == "getenv"
                     and isinstance(func.value, ast.Name) and func.value.id == "os")
                    or (isinstance(func, ast.Name) and func.id == "getenv")
                )
                first = node.args[0]
                if (
                    isinstance(func, ast.Attribute) and func.attr == "get_secret"
                    and isinstance(first, ast.Constant) and isinstance(first.value, str)
                ):
                    record(first.value, True)
                if (
                    isinstance(func, ast.Attribute) and func.attr == "get"
                    and isinstance(func.value, ast.Name) and func.value.id == "userdata"
                    and isinstance(first, ast.Constant) and isinstance(first.value, str)
                ):
                    record(first.value, True)
                if (is_get or is_getenv) and isinstance(first, ast.Constant) and isinstance(first.value, str):
                    default = node.args[1] if len(node.args) > 1 else next(
                        (kw.value for kw in node.keywords if kw.arg == "default"), None
                    )
                    has_default = default is not None and not (
                        isinstance(default, ast.Constant) and default.value is None
                    )
                    record(first.value, not has_default)

    # Names a loaded .env file supplies (required unless the code already
    # reads them with a default).
    for name in _dotenv_keys(code_cells, notebook_path):
        if not name.startswith("NOTEBOOK_API_"):
            found.setdefault(name, True)

    set_by_magic = _env_magic_values(code_cells)

    return [
        {"name": name, "required": found[name] and name not in set_by_magic}
        for name in sorted(found)
    ]


def read_notebook_env_vars(output_dir):
    """_find_notebook_env_vars of the runtime module a compile already wrote
    into `output_dir` ([] when there isn't one) -- for the places that
    regenerate deployment files later (deploy tagging an image) without the
    notebook's cells at hand."""
    try:
        source = (Path(output_dir) / "runtime" / "notebook_module.py").read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        metadata = json.loads(
            (Path(output_dir) / COMPILE_METADATA_FILENAME).read_text(encoding="utf-8")
        )
        notebook_path = metadata.get("source_notebook") if isinstance(metadata, dict) else None
    except (OSError, ValueError):
        notebook_path = None
    return _find_notebook_env_vars([source], notebook_path)


def _statement_calls(node):
    """Calls belonging to `node` itself, not to statements nested inside it
    (those are visited on their own) and not inside a lambda or
    comprehension-free nested def."""
    calls = []

    def walk(current):
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.stmt, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                calls.append(child)
            walk(child)

    walk(node)
    return calls


KNOWN_DIRECTIVE_NAMES = frozenset({
    "requires", "apt-requires", "exclude", "private", "background", "sync",
    "deprecated", "timeout", "rate-limit", "cache", "tag",
})

ANY_DIRECTIVE_PATTERN = re.compile(
    r"^[ \t]*#\s*notebook-to-api:\s*(?P<name>[A-Za-z][\w-]*)(?P<rest>[^\n]*)$",
    re.MULTILINE,
)

# What may follow a directive's name for its own pattern above to ever match
# it -- "cache abc" or "private now" is a *known* directive that's silently
# ignored just like a typo'd name is.
DIRECTIVE_ARGUMENT_SHAPES = {
    "cache": re.compile(r"\s+\d+\s*"),
    "rate-limit": re.compile(r"\s+\d+\s*"),
    "timeout": re.compile(r"\s+\d+\s*"),
    "tag": re.compile(r"\s+[A-Za-z0-9][A-Za-z0-9 _-]{0,39}?\s*"),
    "private": re.compile(r"\s*"),
    "background": re.compile(r"\s*"),
    "sync": re.compile(r"\s*"),
}


def _find_unrecognized_directives(code_cells):
    """[{"directive": name, "line": text}] for every
    "# notebook-to-api: <name> ..." comment whose <name> isn't a directive
    this tool implements. A typo ("cahce 60", "rate_limit 5") matches none
    of the per-directive patterns above, so it was silently ignored --
    compiling fine, with the quota/cache/tag the author meant never applied.
    Names are matched case-sensitively, exactly as the real directives are.
    A real directive whose argument is malformed ("cache abc", "timeout",
    "tag Bad!", "private now") is reported too: it matches no pattern
    above either, so it's ignored just the same.
    """
    found = []
    for cell in code_cells:
        for match in ANY_DIRECTIVE_PATTERN.finditer(cell):
            name = match.group("name")
            argument_shape = DIRECTIVE_ARGUMENT_SHAPES.get(name)
            if name in KNOWN_DIRECTIVE_NAMES and (
                argument_shape is None
                or argument_shape.fullmatch(match.group("rest"))
            ):
                continue
            found.append({
                "directive": name,
                "line": match.group(0).strip(),
            })
    return found


def _extract_deprecated_functions(code_cells):
    """{function_name: reason_or_None} for every function `code_cells`
    marks "# notebook-to-api: deprecated" (immediately above its own
    `def`/`async def` line, see DEPRECATED_FUNCTION_DIRECTIVE_PATTERN
    above), across all cells.

    Before this, a notebook author had no way to tell a caller "this
    endpoint still works, but shouldn't be used for new integrations
    going forward" -- the exact, extremely common real-world need behind
    OpenAPI's own standard "deprecated": true field, which every major
    Swagger UI/Redoc renders as a strikethrough with a warning, and many
    third-party client generators either skip or emit with a compiler
    warning of their own. The only existing escape hatches were
    PRIVATE_FUNCTION_DIRECTIVE_PATTERN (removes the endpoint entirely --
    a breaking change for every existing caller, not a soft warning) or
    silently leaving a stale endpoint's own docstring to explain the
    situation in prose no tooling can act on.

    Returns a plain dict (not a set) the same way
    _extract_background_overrides above does, for the identical reason:
    a caller (resolve_deprecation, generator/api_generator.py) needs each
    deprecated function's own optional reason text, not just whether it's
    deprecated at all -- a membership-tested set (the shape
    _extract_private_function_names/_extract_excluded_imports return)
    can't carry that. A function marked deprecated with no reason given
    gets None (not ""), the same "distinct from an empty string"
    convention this project's other docstring-adjacent free-text fields
    already follow, so a caller can tell "no reason given" apart from a
    reason that happens to be empty (which this pattern's own "\\S.*"
    reason capture can never actually produce in the first place, since
    it requires at least one non-whitespace character to match at all).

    A function marked more than once (the same "cell re-run with a
    tweaked reason" scenario BACKGROUND_OVERRIDE_DIRECTIVE_PATTERN's own
    docstring already describes for its own directive) simply keeps
    whichever reason was seen last, mirroring
    deduplicate_functions_by_name's own "last one wins" resolution for a
    function redefined outright -- unlike "background"/"sync", there's no
    contradictory *other* value "deprecated" could conflict with, so no
    error is raised here the way a genuine background/sync conflict is.
    """
    deprecated = {}

    for cell in code_cells:

        for match in DEPRECATED_FUNCTION_DIRECTIVE_PATTERN.finditer(cell):

            reason = match.group("reason")
            deprecated[match.group("name")] = reason.strip() if reason else None

    return deprecated


_WRITEFILE_LINE_PATTERN = re.compile(r"^#\s*%%(?:writefile|file)\s+(?:-a\s+)?(?P<target>\S+)\s*$")


def _writefile_modules(code_cells):
    """{module name: source text} for the `%%writefile name.py` cells.

    The notebook parser comments such a cell out ("# %%writefile ..." plus
    each body line behind "# "), so the module a later cell imports was
    never shipped. Only a top-level `name.py` is recognised; `-a` appends,
    a later write replaces an earlier one.
    """
    modules = {}

    for cell in code_cells:
        lines = cell.split("\n")
        match = _WRITEFILE_LINE_PATTERN.match(lines[0]) if lines else None
        if not match:
            continue
        target = match.group("target")
        if not target.endswith(".py") or "/" in target or "\\" in target:
            continue
        name = target[:-3]
        if not name.isidentifier():
            continue
        append = lines[0].split("%%", 1)[1].split()[1:2] == ["-a"]
        body = "\n".join(
            line[2:] if line.startswith("# ") else ("" if line.strip() in ("", "#") else line)
            for line in lines[1:]
        )
        modules[name] = (modules.get(name, "") + body) if append and name in modules else body

    return modules


def find_local_modules(notebook_path, import_names, code_cells=None):
    """{import name: Path | str} for each of `import_names` that is a module
    (`name.py`) or package (`name/__init__.py`) sitting next to the notebook
    (a Path), or that a `%%writefile name.py` cell in `code_cells` creates (its
    source text, which wins -- the notebook rewrites the file when it runs).

    Jupyter finds those through the notebook's own directory, so a notebook
    can `import helpers` freely -- but compiling listed `helpers` in
    requirements.txt as if it were a PyPI package (a failing `pip install`
    at best, a stranger's same-named package at worst) and never shipped the
    file, so the app died on import with ModuleNotFoundError.
    """
    found = {}

    if notebook_path:
        directory = Path(notebook_path).resolve().parent

        for name in import_names:
            if not str(name).isidentifier():
                continue
            if (directory / f"{name}.py").is_file():
                found[name] = directory / f"{name}.py"
            elif (directory / name / "__init__.py").is_file():
                found[name] = directory / name

    if code_cells:
        written = _writefile_modules(code_cells)
        for name in import_names:
            if name in written:
                found[name] = written[name]

    return found


def extract_third_party_imports(code_cells, notebook_path=None):
    """The raw, STANDARD_LIBS-filtered import names `code_cells` (already
    filtered to parseable cells, as compile_notebook_to_api's own
    `code_cells` already is) collect -- before any distribution-name
    resolution or version pinning, exactly the shape resolve_requirements
    (below) itself expects as its own "imports" argument.

    Factored out of compile_notebook_to_api, which used to assemble this
    same set/filter pass inline just before calling write_requirements --
    routes/upload.py's requirements-preview endpoint needs the identical
    "what does this notebook actually import" step to build a preview
    without writing anything to disk, and duplicating it inline a second
    time there would risk the exact kind of two-copies-silently-drift
    problem _third_party_dependencies' own docstring (backend/inspector.py)
    already describes happening once before, for a related but distinct
    computation.

    Also filters out anything _extract_excluded_imports (above) collects
    from the same `code_cells` -- applied here, in the one place both
    compile_notebook_to_api and the requirements-preview endpoint already
    share, rather than at each call site separately.
    """
    imports = set()

    for cell in code_cells:
        imports.update(extract_imports_from_code(cell))

    excluded = _extract_excluded_imports(code_cells)

    local = find_local_modules(notebook_path, imports, code_cells)

    # A %%writefile module's own imports live in commented-out text, so scan
    # its source for the dependencies it needs.
    written = _writefile_modules(code_cells)
    for source in written.values():
        imports.update(extract_imports_from_code(source))

    return [
        imp for imp in imports
        if imp not in STANDARD_LIBS and imp not in excluded and imp not in local
        and imp not in written and imp != "kaggle_secrets"
    ]


# Matches the leading package-name token of a requirement spec (PEP 508
# distribution name rules: letters/digits/"."/"_"/"-"), stopping at the
# first character that can only belong to a version specifier, extras
# bracket, or "@ <url>" direct-reference suffix -- see
# _explicit_requirement_package_name below for what this is for.
#
# The trailing lookahead is load-bearing, not cosmetic: without it, a bare
# VCS/URL spec with no "name @ " prefix at all (e.g.
# "git+https://example.com/pkg.git", one of the exact "no reliable way to
# name" examples this pattern's own docstring below already calls out)
# still matched -- "[A-Za-z0-9._-]*" has no reason to stop before the "+"
# any earlier than it does, so it happily captured "git" as though that
# were the actual package name. Confirmed: this pattern alone (with no
# lookahead) returned "git" for that exact input, silently
# mis-identifying an arbitrary, unrelated VCS spec as declaring a package
# literally named "git" -- the false "same package" match
# _explicit_requirement_package_name below exists to avoid, not manufacture.
# Anchoring the match to end only where PEP 508 actually allows a name to
# end (whitespace, "[", a version comparator, ";", "@", or end of string)
# makes this return None for that case instead, matching what the
# docstring already claims.
_REQUIREMENT_SPEC_NAME_PATTERN = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s|\[|==|!=|<=|>=|~=|===|<|>|=|;|@|$)"
)


def _explicit_requirement_package_name(spec):
    """The package name `spec` (one "# notebook-to-api: requires <spec>"
    line, or an auto-pinned "name==version" one) declares, or None if
    `spec` doesn't start with one at all (e.g. a bare VCS URL with no
    "#egg=name" or "name @ " prefix, which this tool has no reliable way
    to name).

    Used by resolve_requirements below to tell whether an explicit
    directive and an auto-detected import are naming the *same* package,
    even though their own spec strings never match exactly (a pin can
    carry a version/extras/URL an auto-detected "name==version" line
    never would).
    """
    match = _REQUIREMENT_SPEC_NAME_PATTERN.match(spec)

    return match.group(1) if match else None


# PEP 503's own normalization algorithm, verbatim: lowercase, then
# collapse any run of "-"/"_"/"." into a single "-". PyPI treats
# "scikit-learn"/"scikit_learn"/"Scikit.Learn"/... as the exact same
# project for this reason -- pip resolves all of them identically -- so
# any comparison here asking "do these two specs name the same
# distribution" must normalize this way too, not just lowercase (which
# only handles the case-insensitivity half of PyPI's own equivalence
# rule, not the separator half).
_PEP_503_SEPARATOR_RUN_PATTERN = re.compile(r"[-_.]+")


def _normalize_distribution_name(name):
    """`name` normalized the identical way PyPI itself normalizes a
    project name (PEP 503) -- used everywhere in this file that asks "do
    these two package names refer to the same PyPI distribution", so
    "python-dateutil" and "python_dateutil" (or "NumPy" and "numpy") are
    always recognized as the same package, exactly as pip itself would.
    """
    return _PEP_503_SEPARATOR_RUN_PATTERN.sub("-", name).lower()


def resolve_requirements(imports, explicit_requirements=None, excluded_imports=None):
    """The exact, sorted requirements.txt lines write_requirements (below)
    would write for `imports` (a notebook's own third-party imports, e.g.
    from extract_third_party_imports above) and `explicit_requirements`
    (see _extract_explicit_requirements) -- computed without writing
    anything to disk, so a caller can preview what a compile would
    produce there without actually running one.

    `excluded_imports` (see _extract_excluded_imports) raises ValueError
    when an explicit requirement names the same package a "# notebook-to-
    api: exclude" directive opts out of requirements.txt -- a direct
    contradiction of the notebook's own declared intent, the same
    "conflicting directives" treatment _extract_explicit_requirements'
    own docstring already gives two "requires" directives naming the
    same package, or _extract_background_overrides' own identical
    treatment for "background"/"sync" on the same function. Confirmed
    exploitable before this check existed: `extract_third_party_imports`
    already drops an excluded import from `imports` before it ever
    reaches this function, but a stale/leftover "# notebook-to-api:
    requires numpy==1.24.0" directive left behind after adding
    "# notebook-to-api: exclude numpy" (e.g. numpy already vendored into
    a custom base image, the exact use case _extract_excluded_imports'
    own docstring gives) still made it into requirements.txt unfiltered,
    silently overriding the author's own explicit opt-out and pip-
    installing the very package "exclude" was supposed to keep out.
    Resolved against `distribution_name_for_import` the same way
    `imports` itself already is just below, since "exclude" names a raw
    *import* name while "requires" names a PyPI *distribution* name, and
    the two frequently differ (see distribution_name_for_import's own
    docstring) -- an "exclude cv2" must still catch a "requires
    opencv-python==...", not just a literal "requires cv2==...".

    Factored out of write_requirements, which used to compute this same
    value immediately before writing it out; write_requirements below now
    just calls this and writes the result, so a preview built from this
    function can never drift from what an actual compile would produce.

    An auto-detected import whose own resolved distribution name matches
    an explicit requirement's own package name (see
    _explicit_requirement_package_name above) is dropped from the
    auto-pinned set entirely -- confirmed exploitable before this: a
    notebook importing a package directly (e.g. `import numpy`) while
    also declaring "# notebook-to-api: requires numpy==1.24.0" (to pin a
    specific version this tool's own auto-resolution wouldn't have
    chosen) got *both* lines written to requirements.txt side by side --
    the auto-detected "numpy==<installed-version>" and the explicit
    "numpy==1.24.0" -- two version-pinned lines for the same distribution,
    which pip treats as an unsatisfiable requirement and refuses outright
    (`pip install -r requirements.txt` failing with "Double requirement
    given"), breaking `deploy`'s own Docker build over exactly the kind
    of explicit override this directive exists to let a notebook author
    make. The explicit line always wins; nothing here validates that its
    own version/extras are otherwise sensible, the same "copied through
    verbatim, unvalidated" contract _extract_explicit_requirements'
    own docstring already establishes for it.
    """

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

    # Resolve each notebook import to the actual PyPI distribution name
    # that provides it before pinning -- see distribution_name_for_import's
    # own docstring for why this can't just pin the raw import name
    # directly. A set here (not a list) since two distinct import names
    # occasionally resolve to the same distribution (e.g. "attr" and
    # "attrs" are both provided by the "attrs" distribution), and this
    # must not write a duplicate requirements.txt line for it.
    distribution_names = sorted({
        distribution_name_for_import(dep) for dep in final_deps
    })

    explicit_requirements = explicit_requirements or []

    # Normalized via _normalize_distribution_name -- the identical PEP
    # 503 rule PyPI itself applies to a project name -- not just
    # lowercased: PyPI treats "numpy"/"NumPy" as the same project (case),
    # but also "python-dateutil"/"python_dateutil" and "zope.interface"/
    # "zope-interface" (separator runs). Confirmed exploitable with only
    # case-folding: a notebook `import dateutil` (auto-resolves to
    # "python-dateutil" via distribution_name_for_import) alongside its
    # own "# notebook-to-api: requires python_dateutil==2.9.0" -- a
    # perfectly ordinary, pip-valid way to spell that pin -- produced
    # *both* "python-dateutil==<auto>" and "python_dateutil==2.9.0" in
    # requirements.txt, the exact "Double requirement given" pip failure
    # this function's own docstring already describes fixing, just
    # reproduced through a separator spelling difference instead of a
    # case one.
    explicit_package_names = {
        _normalize_distribution_name(name)
        for name in (
            _explicit_requirement_package_name(spec)
            for spec in explicit_requirements
        )
        if name is not None
    }

    excluded_import_by_distribution_name = {
        _normalize_distribution_name(distribution_name_for_import(name)): name
        for name in (excluded_imports or ())
    }

    for spec in explicit_requirements:

        package_name = _explicit_requirement_package_name(spec)

        if package_name is None:
            continue

        excluded_import_name = excluded_import_by_distribution_name.get(
            _normalize_distribution_name(package_name)
        )

        if excluded_import_name is not None:
            raise ValueError(
                f"'# notebook-to-api: requires {spec}' conflicts with "
                f"'# notebook-to-api: exclude {excluded_import_name}' -- "
                f"both name the same package ('{package_name}'). Remove "
                "one of them."
            )

    # Drops any auto-detected distribution an explicit directive already
    # names -- see this function's own docstring above for the conflicting-
    # pin this closes. Still deduplicated only by exact matching text
    # against *other* explicit requirements below, not by which PyPI
    # distribution a line actually refers to: this can't tell that
    # "opencv-python==4.9.0.80" and "opencv-python-headless==4.9.0.80"
    # ultimately provide the same `cv2` import, so declaring one doesn't
    # suppress the other if the notebook also imports it directly -- only
    # an auto-detected import colliding with an *explicit* requirement is
    # resolved in the explicit one's favor.
    pinned_deps = [
        _pinned_requirement(dep) for dep in distribution_names
        if _normalize_distribution_name(dep) not in explicit_package_names
    ]

    return sorted(set(pinned_deps) | set(explicit_requirements))


def write_requirements(
    imports, output_dir, explicit_requirements=None, excluded_imports=None
):

    requirements_path = os.path.join(
        output_dir,
        "requirements.txt"
    )

    all_deps = resolve_requirements(imports, explicit_requirements, excluded_imports)

    with open(requirements_path, "w", encoding="utf-8") as f:
        for dep in all_deps:
            f.write(dep + "\n")

    print(
        f"requirements.txt generated with dependencies: {all_deps}"
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


# Every file a real compile actually writes into output_dir, relative to
# it -- "app.py" itself is appended separately in
# _generated_files_sha256 below, since its own filename is a caller-
# supplied output_path, not a fixed literal the way these eight are.
# Deliberately a fixed list, not a generic directory walk: an operator's
# own unrelated file dropped into output_dir by hand (or a later POST
# /api/export-openapi/export-sdk's own openapi.json/sdk/, which a
# compile never produces and clear_stale_export_artifacts already treats
# as not part of "this compile's own output") must never affect this
# hash -- only these specific compile-produced artifacts should.
_GENERATED_OUTPUT_RELATIVE_PATHS = (
    "requirements.txt",
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    "kubernetes.yaml",
    ".env.example",
    "README.md",
    os.path.join("runtime", "notebook_module.py"),
)


def _generated_files_sha256(output_dir, app_filename="app.py"):
    """A single SHA-256 summarizing the exact set of files a real compile
    writes into `output_dir` -- the same "one filename:sha256 pair per
    file, sorted, combined into one hash" technique GET /api/download's
    own "X-Bundle-SHA256" header and GET /api/generated?checksums=true's
    own "bundle_sha256" (both backend/routes/upload.py) already use for
    an entire compiled bundle, just applied here to compiler.py's own
    fixed, known-at-compile-time file set (see
    _GENERATED_OUTPUT_RELATIVE_PATHS above) instead of a live directory
    walk -- reused (not duplicated) by write_compile_metadata below to
    record a baseline at compile time, and by GET /api/generated
    (routes/upload.py) to recompute the same hash later and compare
    against it.

    A file this project's own compile pipeline hasn't written yet (or
    that's since been deleted by hand) is silently skipped rather than
    raising -- this must never be the reason a real compile fails, since
    write_compile_metadata's own caller has already committed to
    treating this compile as successful by the time it calls this (see
    write_compile_metadata's own docstring for the "no exception past
    this point" discipline compile_notebook_to_api already follows). A
    missing file still changes the resulting hash from a compile that
    did produce it, so a deleted artifact is still detected as a change,
    just not as a hard failure.

    `app_filename` defaults to "app.py" -- every real caller compiles to
    "<output_dir>/app.py" (see compile_notebook's own docstring); passed
    explicitly rather than hardcoded so a caller computing this against
    an unusual output_path can still get an accurate hash.
    """
    hasher = hashlib.sha256()

    relative_paths = sorted(_GENERATED_OUTPUT_RELATIVE_PATHS + (app_filename,))

    for relative_path in relative_paths:

        file_path = Path(output_dir) / relative_path

        if not file_path.is_file():
            continue

        hasher.update(
            f"{relative_path}:{hash_notebook_file(str(file_path))}\n".encode("utf-8")
        )

    return hasher.hexdigest()


def write_compile_metadata(
    notebook_path, output_dir, content_path=None, version_id=None,
    app_filename="app.py",
):
    """Record which notebook produced the app in `output_dir`, its
    content hash at that moment, when, and -- if this compile came from
    one of that notebook's own previously snapshotted versions rather
    than its current content -- which one.

    "generated_files_sha256" (added alongside this same docstring's
    original three fields, not a separate change) is
    _generated_files_sha256(output_dir, app_filename) above -- a
    baseline hash over the exact compile-produced files themselves
    (app.py, requirements.txt, Dockerfile, .dockerignore,
    docker-compose.yml, .env.example, README.md, the runtime module), taken at the
    very end of a successful compile, once every one of those has
    actually been written. Every hash/staleness check this dashboard
    already had
    compared the *source notebook's* own content against what's
    currently compiled (see "source_notebook_sha256" below) -- but
    nothing recorded whether the compiled *output itself* still matches
    what that compile actually produced. A generated/app.py hand-edited
    directly on the server after compiling (to patch something in a
    hurry, say) previously left every metadata-driven consumer -- GET
    /api/notebooks, GET /api/generated, POST /api/deploy's own staleness
    check -- confidently reporting the compile as fully up to date with
    its source notebook, with no way to tell the *served* code had
    silently diverged from what that notebook actually compiles to.
    `app_filename` is threaded through from compile_notebook_to_api's
    own `output_path` (its basename) rather than assumed, so this stays
    accurate even for a caller compiling to something other than the
    "<output_dir>/app.py" every documented caller already uses.

    "compiled_version_id" (None for an ordinary compile of a notebook's
    own current content, the overwhelmingly common case) is the same
    "version_id" POST /api/compile's own response and compile-history
    entries already report for a version-pinned compile (see POST
    /api/compile's own docstring, backend/routes/upload.py) -- but until
    now that was only ever visible in that one request/response or a
    compile-history entry, never persisted alongside the compile it
    actually describes. An operator investigating "what is actually
    running right now" -- after the fact, possibly a long time later, or
    from a different process entirely (GET /api/notebooks, GET
    /api/generated, ...) -- had no way to tell a version-pinned compile
    apart from an ordinary one short of cross-referencing compile
    history by "source_notebook_sha256" and hoping nothing else ever
    produced the same hash.

    `content_path` (optional) is the file whose *bytes* actually got
    compiled and should be hashed -- `notebook_path` is always what gets
    recorded as "source_notebook" and stays the real, currently-uploaded
    notebook's own path. They differ only when compiling one of a
    notebook's own previously snapshotted versions (see POST /api/compile's
    own "version_id", backend/routes/upload.py): "source_notebook" still
    names the real notebook (so every "currently_compiled"/staleness check
    already keyed on that path -- _currently_compiled_notebook_metadata
    and friends -- keeps recognizing it), while the hash reflects the
    version's own content, not the notebook's current one. That mismatch
    is exactly the correct, honest outcome: the notebook's current content
    genuinely differs from what's actually served, so
    "notebook_changed_since_compile" correctly reports true. Defaults to
    `notebook_path` -- every existing caller compiling a notebook's own
    current content keeps hashing exactly what it already did.

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
        "source_notebook_sha256": hash_notebook_file(content_path or notebook_path),
        "compiled_at": (
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        ),
        "compiled_version_id": version_id,
        "generated_files_sha256": _generated_files_sha256(output_dir, app_filename),
    }

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def update_compile_metadata_source_notebook(output_dir, new_source_notebook_path):
    """Update the "source_notebook" field recorded in `output_dir`'s
    .compile_metadata.json to `new_source_notebook_path`, leaving its
    "source_notebook_sha256" and "compiled_at" fields untouched.

    Used by PATCH /api/notebooks/{filename} (backend/routes/upload.py)
    when the notebook being renamed is the one that produced the app
    currently in output_dir. Renaming a file on disk doesn't change its
    bytes, so the recorded sha256 is still accurate -- but
    write_compile_metadata's "source_notebook" field is an absolute path
    baked in at compile time, and every "currently_compiled"/staleness
    check this dashboard makes
    (_currently_compiled_notebook_metadata/_currently_compiled_notebook_is_stale
    in routes/upload.py) resolves that exact path. Left untouched by a
    rename, it would keep pointing at a path that no longer exists,
    silently and permanently orphaning the currently-compiled app from
    its own source notebook -- indistinguishable, from the API's
    perspective, from that notebook having been deleted outright.

    A no-op (returns False) if output_dir has no .compile_metadata.json
    yet, or if it's missing/unreadable/corrupt -- nothing to update in
    that case, mirroring _currently_compiled_notebook_metadata's own
    best-effort handling of the same file. Callers are expected to hold
    COMPILE_LOCK, the same way every other read/write of this file
    already does.
    """
    metadata_path = os.path.join(output_dir, COMPILE_METADATA_FILENAME)

    if not os.path.isfile(metadata_path):
        return False

    try:

        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    except (OSError, ValueError):
        return False

    metadata["source_notebook"] = os.path.abspath(new_source_notebook_path)

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return True


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


def _filter_functions_by_name(functions, only, exclude):
    """Restrict `functions` (already deduplicated by name) to the ones a
    caller actually wants compiled into endpoints, via at most one of
    `only` or `exclude` (each an iterable of function names, or falsy for
    "no filtering" -- the default).

    Every module-level function a notebook defines (barring a *args/
    **kwargs one -- see extract_functions_from_code) becomes a public API
    endpoint, with no previous way to opt any of them out: a notebook's
    own internal helper functions (data loading, validation, formatting,
    ...) were compiled into endpoints exactly like the functions actually
    meant to be part of the API's public surface. Renaming a helper
    doesn't help either -- extract_functions_from_code applies no
    leading-underscore (or any other) naming convention, so it's
    extracted and exposed identically to every other top-level function.

    `only` keeps just the named functions; `exclude` keeps every function
    except the named ones. Either way, this only changes what
    generate_fastapi_code (below) turns into endpoints -- write_runtime_module
    still writes *every* cell's code, filtered or not, so an excluded
    helper function stays fully callable from whichever exposed function
    actually calls it; it just doesn't get its own endpoint.

    Raises ValueError naming the unrecognized function(s) if `only`/
    `exclude` names one this notebook doesn't actually define -- the same
    "fail loudly on a name that doesn't do what the caller thinks"
    precedent ReservedFunctionNameError already sets for a different kind
    of name problem (generator/api_generator.py), rather than silently
    compiling as if a typo'd name had simply never been requested at all.
    """
    if only and exclude:
        raise ValueError(
            "only and exclude can't both be given -- choose one."
        )

    if not only and not exclude:
        return functions

    known_names = {func["name"] for func in functions}
    requested_names = set(only) if only else set(exclude)

    unknown_names = sorted(requested_names - known_names)

    if unknown_names:
        raise ValueError(
            f"{'only' if only else 'exclude'} names function(s) not defined "
            f"in this notebook: {', '.join(unknown_names)}. Available: "
            f"{', '.join(sorted(known_names)) or '(none)'}"
        )

    if only:
        return [func for func in functions if func["name"] in requested_names]

    return [func for func in functions if func["name"] not in requested_names]


def compile_notebook_to_api(
    notebook_path,
    output_path,
    only=None,
    exclude=None,
    source_notebook_path=None,
    version_id=None,
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

        # Computed here -- and let it raise (a ValueError, for two
        # conflicting "# notebook-to-api: requires" directives naming the
        # same package -- see its own docstring) -- before writing
        # anything to output_dir at all, the same "validate the notebook's
        # own content before any write" reasoning generate_fastapi_code's
        # own ReservedFunctionNameError check below already established.
        # Reused at the write_requirements call further down instead of
        # calling this a second time there, so a conflict is caught this
        # early rather than only surfacing after write_runtime_module has
        # already overwritten a previous successful compile's own runtime
        # module with this failing notebook's code -- the identical
        # inconsistent-output_dir failure mode that comment already
        # documents fixing for generate_fastapi_code's own checks.
        explicit_requirements = _extract_explicit_requirements(code_cells)

        # Computed here, alongside explicit_requirements above, for the
        # identical reason: available before generate_dockerfile is ever
        # called further down, regardless of which branch of this
        # function's own control flow reaches it.
        apt_packages = _extract_explicit_apt_packages(code_cells)

        excluded_imports = _extract_excluded_imports(code_cells)

        # resolve_requirements' own return value is discarded here -- this
        # call exists purely so its own "exclude"/"requires" conflict
        # check (see its docstring) raises before write_runtime_module
        # below writes anything at all, the identical "validate before
        # any write" reasoning explicit_requirements' own extraction just
        # above already follows for a conflict between two "requires"
        # directives. write_requirements further down recomputes the same
        # result to actually write it -- imports can change between here
        # and there in no way that matters (code_cells is already fixed),
        # so recomputing costs nothing but a second, cheap pass over
        # already-parsed cells.
        local_modules = find_local_modules(
            source_notebook_path or notebook_path,
            {
                imp for cell in code_cells for imp in extract_imports_from_code(cell)
                if imp not in STANDARD_LIBS
            },
            code_cells,
        )
        resolve_requirements(
            extract_third_party_imports(code_cells, source_notebook_path or notebook_path),
            explicit_requirements=explicit_requirements,
            excluded_imports=excluded_imports,
        )

        # Computed here for the same reason: available before
        # generate_fastapi_code/generate_readme are called further down,
        # regardless of which branch of this function's own control flow
        # reaches either.
        background_overrides = _extract_background_overrides(code_cells)

        # Computed here for the identical reason background_overrides
        # just above is: available before generate_fastapi_code is
        # called further down, regardless of which branch of this
        # function's own control flow reaches it.
        deprecated_overrides = _extract_deprecated_functions(code_cells)

        functions = []

        for cell in code_cells:

            funcs = extract_functions_from_code(cell)

            functions.extend(funcs)

        functions = deduplicate_functions_by_name(functions)

        # Dropped before _filter_functions_by_name's own only/exclude
        # handling, so a function the notebook itself marks
        # "# notebook-to-api: private" never becomes an endpoint
        # regardless of --only/--exclude -- see _drop_private_functions'
        # own docstring for why this needs code_cells (not just the
        # already-extracted `functions` list) and can't simply live
        # inside _filter_functions_by_name itself.
        functions, exclude = _drop_private_functions(
            functions, code_cells, only, exclude
        )

        # Applied before generate_fastapi_code, and before the reserved-
        # name-collision check it performs, so an --only/--exclude typo is
        # reported as its own clear error rather than silently changing
        # which (if any) reserved-name collision generate_fastapi_code
        # happens to hit first.
        names_before_selection = {func["name"] for func in functions}
        functions = _filter_functions_by_name(functions, only, exclude)

        # Deprecated functions left out of this compile whose own sunset
        # date has arrived (e.g. `compile --drop-past-sunset`) -- kept as
        # 410 Gone tombstones rather than vanishing into a bare 404; see
        # generate_fastapi_code's own "retired_endpoints".
        kept_names = {func["name"] for func in functions}
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        retired_endpoints = {}
        for name in sorted(names_before_selection - kept_names):
            if name not in deprecated_overrides:
                continue
            sunset = _deprecation_sunset_date(deprecated_overrides[name])
            if sunset and sunset <= today:
                retired_endpoints[name] = deprecated_overrides[name]

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
        # Baked into the generated app itself as SOURCE_NOTEBOOK_SHA256,
        # returned by its own GET /info -- see generate_fastapi_code's own
        # docstring for why. Hashed from notebook_path (the exact content
        # actually being compiled here -- a version snapshot's own path
        # when this compile is pinned to one, not necessarily the
        # notebook's current content) so a running deployed container can
        # always be traced back to precisely what produced it, the same
        # content identity every other sha256 already tracked in this
        # project (deploy/compile history, GET /api/notebooks?sha256=,
        # GET /api/notebooks/duplicates) already uses.
        api_code = generate_fastapi_code(
            functions, package_name,
            source_notebook_sha256=hash_notebook_file(notebook_path),
            notebook_to_api_version=NOTEBOOK_TO_API_VERSION,
            background_overrides=background_overrides,
            deprecated_overrides=deprecated_overrides,
            retired_endpoints=retired_endpoints,
            timeout_overrides=_extract_timeout_overrides(code_cells),
            rate_limit_overrides=_extract_rate_limit_overrides(code_cells),
            cache_overrides=_extract_cache_overrides(code_cells),
            tag_overrides=_extract_tag_overrides(code_cells),
        )

        # generate_fastapi_code succeeding means this compile is now
        # guaranteed to go through -- from here on the only failures left
        # are I/O errors, not this notebook's own content -- so it's now
        # safe to clear out any openapi.json/openapi.yaml/sdk/ left over
        # from a previous compile's export (see its own docstring for why
        # this can't just be left alone).
        clear_stale_export_artifacts(output_dir)

        data_files = find_data_files(source_notebook_path or notebook_path, code_cells)
        if local_modules or data_files:
            write_runtime_module(
                code_cells, output_dir, local_modules, data_files=data_files
            )
        else:
            write_runtime_module(code_cells, output_dir)

        write_requirements(
            extract_third_party_imports(code_cells, source_notebook_path or notebook_path),
            output_dir,
            explicit_requirements=explicit_requirements,
            excluded_imports=excluded_imports,
        )

        write_generated_api(
            api_code,
            output_path
        )

        # From here on, app.py (and its runtime module) already reflect
        # *this* compile's notebook -- if anything below raises
        # (generate_dockerfile/generate_dockerignore, or write_compile_metadata
        # itself), app.py has already moved on to the new notebook while
        # .compile_metadata.json, left untouched, would still describe
        # whichever notebook the *previous* successful compile actually
        # wrote it for. Confirmed reproduced: recompiling a working "add"
        # app with a notebook exposing "multiply" while generate_dockerfile
        # was made to raise (standing in for a real disk/permission
        # failure) left app.py and its runtime module already serving
        # multiply, while .compile_metadata.json's "source_notebook" still
        # pointed at the notebook that produced the *previous* compile --
        # silently wrong, not merely stale: every metadata-driven consumer
        # (GET /api/notebooks' "currently_compiled"/"compiled_at"/
        # "notebook_changed_since_compile", GET /api/generated's
        # "source_notebook_filename"/"source_notebook_exists") would
        # confidently report the wrong notebook as the one actually being
        # served, with nothing to indicate the mismatch. Removing
        # .compile_metadata.json here instead turns that into a correctly-
        # reported "unknown" state -- _currently_compiled_notebook_metadata
        # (routes/upload.py) already treats a missing metadata file exactly
        # this way (returns (None, None, None)), the same graceful
        # degradation it already applies when nothing has ever been
        # compiled at all. This also cleans up a metadata file
        # write_compile_metadata itself might have left partially written
        # (a mid-json.dump I/O failure), not just one from the two steps
        # before it.
        try:

            dockerfile_path = os.path.join(
                output_dir,
                "Dockerfile"
            )

            generate_dockerfile(
                dockerfile_path, package_name, compiling_python_version(),
                apt_packages=apt_packages,
                hub_models=hub_model_ids(code_cells),
                language_data=language_data_packages(code_cells),
                torch_weights=torch_weight_prefetches(code_cells),
            )

            dockerignore_path = os.path.join(
                output_dir,
                ".dockerignore"
            )

            generate_dockerignore(dockerignore_path)

            docker_compose_path = os.path.join(
                output_dir,
                "docker-compose.yml"
            )

            notebook_env_vars = _find_notebook_env_vars(code_cells, notebook_path)

            generate_docker_compose(
                docker_compose_path, package_name, GENERATED_APP_ENV_VARS,
                notebook_env_vars=notebook_env_vars,
            )

            env_example_path = os.path.join(
                output_dir,
                ".env.example"
            )

            generate_env_example(
                env_example_path, GENERATED_APP_ENV_VARS,
                notebook_env_vars=notebook_env_vars,
            )

            kubernetes_manifest_path = os.path.join(
                output_dir,
                "kubernetes.yaml"
            )

            generate_kubernetes_manifest(
                kubernetes_manifest_path, package_name, GENERATED_APP_ENV_VARS,
                notebook_env_vars=notebook_env_vars,
            )

            readme_path = os.path.join(
                output_dir,
                "README.md"
            )

            generate_readme(
                readme_path, package_name, functions, GENERATED_APP_ENV_VARS,
                background_overrides=background_overrides,
                deprecated_overrides=deprecated_overrides,
                timeout_overrides=_extract_timeout_overrides(code_cells),
                rate_limit_overrides=_extract_rate_limit_overrides(code_cells),
                cache_overrides=_extract_cache_overrides(code_cells),
                tag_overrides=_extract_tag_overrides(code_cells),
            )

            write_compile_metadata(
                source_notebook_path or notebook_path,
                output_dir,
                content_path=notebook_path,
                version_id=version_id,
                app_filename=os.path.basename(output_path),
            )

        except Exception:

            metadata_path = os.path.join(output_dir, COMPILE_METADATA_FILENAME)

            if os.path.isfile(metadata_path):
                os.remove(metadata_path)

            raise

        print(
            f"Successfully generated FastAPI app at: {output_path}"
        )


def compile_notebook(
    notebook_path,
    output_dir,
    only=None,
    exclude=None,
    source_notebook_path=None,
    version_id=None,
):
    """
    Convenient wrapper for CLI.
    Generates the FastAPI app at <output_dir>/app.py.

    `only`/`exclude` are passed straight through to
    compile_notebook_to_api -- see _filter_functions_by_name's own
    docstring for what they do and why. `source_notebook_path`/
    `version_id` are passed straight through too -- see
    write_compile_metadata's own docstring.
    """

    output_path = os.path.join(
        output_dir,
        "app.py"
    )

    compile_notebook_to_api(
        notebook_path,
        output_path,
        only=only,
        exclude=exclude,
        source_notebook_path=source_notebook_path,
        version_id=version_id,
    )