import argparse
import os
import sys

# Ensure project root is in sys.path for proper imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from backend.parser.notebook_parser import load_notebook, extract_code_cells, extract_functions_from_notebook
from backend.generator.api_generator import generate_fastapi_code, write_generated_api


def write_runtime_module(code_cells):
    runtime_path = Path("generated/runtime/notebook_module.py")

    runtime_path.parent.mkdir(parents=True, exist_ok=True)

    combined_code = "\n\n".join(code_cells)

    with open(runtime_path, "w", encoding="utf-8") as f:
        f.write(combined_code)

    print("Runtime module generated.")


def compile_notebook_to_api(notebook_path, output_path="generated/app.py"):
    print(f"Starting compilation for: {notebook_path}")
    
    # 1. Load notebook and write runtime module
    notebook = load_notebook(notebook_path)
    code_cells = extract_code_cells(notebook)
    write_runtime_module(code_cells)

    # 2. Extract functions via AST from the notebook cells
    functions = extract_functions_from_notebook(notebook_path)
    print(f"Extracted {len(functions)} functions: {functions}")
    
    # 3. Generate FastAPI code
    generated_code = generate_fastapi_code(functions)
    
    # 4. Write to the output path
    write_generated_api(generated_code, output_path)
    print("Compilation completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile Jupyter notebook to FastAPI app")
    parser.add_argument(
        "--notebook", 
        type=str, 
        default="notebooks/sample.ipynb", 
        help="Path to the input Jupyter notebook"
    )
    parser.add_argument(
        "--output", 
        type=str, 
        default="generated/app.py", 
        help="Path to write the generated FastAPI app"
    )
    
    args = parser.parse_args()
    
    compile_notebook_to_api(args.notebook, args.output)
