import keyword
import os
import sys
import pathlib

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
    generate_dockerfile
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


def write_requirements(imports, output_dir):

    requirements_path = os.path.join(
        output_dir,
        "requirements.txt"
    )

    core_dependencies = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "watchdog"
    ]

    final_deps = sorted(
        set(list(imports) + core_dependencies)
    )

    with open(requirements_path, "w", encoding="utf-8") as f:
        for dep in final_deps:
            f.write(dep + "\n")

    print(
        f"requirements.txt generated with dependencies: {final_deps}"
    )


def compile_notebook_to_api(
    notebook_path,
    output_path
):

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

    api_code = generate_fastapi_code(functions, package_name)

    write_generated_api(
        api_code,
        output_path
    )

    dockerfile_path = os.path.join(
        output_dir,
        "Dockerfile"
    )

    generate_dockerfile(dockerfile_path, package_name)

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