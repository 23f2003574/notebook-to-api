import argparse
import os
import sys
import pathlib

# Ensure project root is in sys.path for proper imports
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


from pathlib import Path
from backend.parser.notebook_parser import load_notebook, extract_code_cells
from backend.parser.ast_parser import extract_functions_from_code, extract_imports_from_code
from backend.generator.api_generator import generate_fastapi_code, write_generated_api
from backend.generator.docker_generator import generate_dockerfile


def write_runtime_module(code_cells):
    runtime_path = Path("generated/runtime/notebook_module.py")

    runtime_path.parent.mkdir(parents=True, exist_ok=True)

    combined_code = "\n\n".join(code_cells)

    with open(runtime_path, "w", encoding="utf-8") as f:
        f.write(combined_code)

    print("Runtime module generated.")


def write_requirements(imports):
    requirements_path = "generated/requirements.txt"

    core_dependencies = [
        "fastapi",
        "uvicorn",
        "pydantic"
    ]

    final_deps = sorted(set(list(imports) + core_dependencies))

    with open(requirements_path, "w", encoding="utf-8") as f:
        for dep in final_deps:
            f.write(dep + "\n")

    print(f"requirements.txt generated with dependencies: {final_deps}")


def compile_notebook_to_api(notebook_path, output_path):
    print(f"Starting compilation for: {notebook_path}")
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    notebook = load_notebook(notebook_path)
    code_cells = extract_code_cells(notebook)
    # Extract functions from all code cells
    functions = []
    for cell in code_cells:
        funcs = extract_functions_from_code(cell)
        functions.extend(funcs)
    
    write_runtime_module(code_cells)
    
    all_code = "\n\n".join(code_cells)
    imports = extract_imports_from_code(all_code)
    write_requirements(imports)
    
    api_code = generate_fastapi_code(functions)
    write_generated_api(api_code, output_path)
    generate_dockerfile("generated/")
    
    print(f"Successfully generated FastAPI app at: {output_path}")



def compile_notebook(notebook_path, output_dir):
    """Convenient wrapper for CLI.
    Generates the FastAPI app at <output_dir>/app.py.
    """
    output_path = os.path.join(output_dir, "app.py")
    compile_notebook_to_api(notebook_path, output_path)
