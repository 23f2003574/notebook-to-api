import nbformat
import os
import sys

# Ensure backend directory is in sys.path for robust imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from backend.parser.ast_parser import extract_functions_from_code
except ImportError:
    from ast_parser import extract_functions_from_code


def load_notebook(notebook_path):
    with open(notebook_path, "r", encoding="utf-8") as f:
        notebook = nbformat.read(f, as_version=4)

    return notebook


def extract_code_cells(notebook):
    code_cells = []

    for cell in notebook.cells:
        if cell.cell_type == "code":
            code_cells.append(cell.source)

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

