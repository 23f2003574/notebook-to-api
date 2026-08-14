import nbformat
import os
import re
import sys

# Ensure backend directory is in sys.path for robust imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from backend.parser.ast_parser import extract_functions_from_code
except ImportError:
    from ast_parser import extract_functions_from_code

# IPython line magics (%foo), cell magics (%%foo) and shell escapes (!foo)
# are not valid Python syntax and are not preceded by executed code, so any
# line starting with them (ignoring leading whitespace) can be safely
# commented out.
_MAGIC_LINE_RE = re.compile(r"^(\s*)(%{1,2}|!)(?!=)")

# IPython's "dynamic object introspection" syntax -- ``obj?``/``obj??`` for
# an object's docstring/source, or the equivalent prefix form ``?obj``/
# ``??obj`` -- is just as common in real notebooks (typed while exploring,
# then left in a cell) and just as invalid as Python syntax, but wasn't
# covered by _MAGIC_LINE_RE above. `?` never appears in valid Python syntax
# outside of a string literal, so a line that consists of *only* an
# attribute-chain expression (optionally called) plus a leading/trailing
# "?"/"??" is unambiguously an introspection query, not code -- as opposed
# to matching any line merely containing a "?" (which would wrongly also
# match, and corrupt, plain code like `msg = "wait?"`).
_INTROSPECTION_PREFIX_RE = re.compile(
    r"^(\s*)\?{1,2}\s*[A-Za-z_][A-Za-z0-9_.]*(\(\))?\s*$"
)
_INTROSPECTION_SUFFIX_RE = re.compile(
    r"^(\s*)[A-Za-z_][A-Za-z0-9_.]*(\(\))?\s*\?{1,2}\s*$"
)


def strip_magic_commands(source):
    """Comment out IPython magics, shell escapes, and object-introspection
    queries in notebook source.

    Real-world notebooks routinely contain lines like ``%matplotlib inline``,
    ``%%time``, ``!pip install pandas``, or ``train_model?`` (inline help/
    source lookup, via IPython's ``?``/``??`` operator). None of these are
    valid Python, so feeding a cell's raw source straight into ``ast.parse``
    (or writing it verbatim into the generated runtime module) blows up on
    almost any notebook exported from Jupyter -- and since ``ast.parse``
    parses a cell as a single unit, *any one* such line anywhere in the cell
    fails the whole cell, silently dropping every function it defines along
    with it (see is_parseable_python in ast_parser.py). Commenting the
    offending lines out instead keeps line numbers stable and preserves the
    rest of the cell as executable Python.
    """
    cleaned_lines = []

    for line in source.split("\n"):
        match = (
            _MAGIC_LINE_RE.match(line)
            or _INTROSPECTION_PREFIX_RE.match(line)
            or _INTROSPECTION_SUFFIX_RE.match(line)
        )

        if match:
            indent = match.group(1)
            cleaned_lines.append(f"{indent}# {line.strip()}")
        else:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def load_notebook(notebook_path):
    with open(notebook_path, "r", encoding="utf-8") as f:
        notebook = nbformat.read(f, as_version=4)

    return notebook


def extract_code_cells(notebook):
    code_cells = []

    for cell in notebook.cells:
        if cell.cell_type == "code":
            code_cells.append(strip_magic_commands(cell.source))

    return code_cells


def extract_functions_from_notebook(notebook_path):
    notebook = load_notebook(notebook_path)
    code_cells = extract_code_cells(notebook)
    
    all_functions = []
    for code in code_cells:
        funcs = extract_functions_from_code(code)
        all_functions.extend(funcs)
    return all_functions


if __name__ == "__main__":
    # Resolve the path to sample.ipynb relative to this script to make it run from anywhere
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sample_path = os.path.join(script_dir, "../../notebooks/sample.ipynb")

    print(f"Loading notebook from: {os.path.abspath(sample_path)}")
    notebook = load_notebook(sample_path)
    code_cells = extract_code_cells(notebook)

    for idx, code in enumerate(code_cells):
        print(f"\n--- CODE CELL {idx + 1} ---\n")
        print(code)

    print("\n--- EXTRACTED FUNCTIONS ---")
    funcs = extract_functions_from_notebook(sample_path)
    for func in funcs:
        print(func)

