import argparse
import os
import subprocess
from pathlib import Path

# Import the compiler function
from backend.compiler import compile_notebook
# Import inspector for analysis
from backend.inspector import inspect_notebook
from backend.exporters.openapi_exporter import export_openapi_schema
from backend.serve import serve_notebook


def main():
    parser = argparse.ArgumentParser(
        prog="notebook-to-api",
        description="Compile Jupyter notebooks into FastAPI services and optionally build Docker images."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # compile command
    compile_parser = subparsers.add_parser("compile", help="Compile a notebook to FastAPI app.")
    compile_parser.add_argument("notebook", help="Path to the notebook file.")
    compile_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where the FastAPI app and assets will be written."
    )

    # inspect command (show analysis report)
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a notebook and display analysis report.")
    inspect_parser.add_argument("notebook", help="Path to the notebook file.")
    inspect_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where compilation artifacts would be placed (used to list generated files)."
    )

    # openapi export command
    openapi_parser = subparsers.add_parser(
        "export-openapi", help="Export OpenAPI schema from generated FastAPI app."
    )
    openapi_parser.add_argument(
        "--output",
        default="generated/openapi.json",
        help="Path to write the OpenAPI JSON file."
    )

    # serve command (live notebook server)
    serve_parser = subparsers.add_parser("serve", help="Serve notebook as live API with hot recompilation.")
    serve_parser.add_argument("notebook", help="Path to the notebook file.")
    serve_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where the FastAPI app will be written."
    )

    args = parser.parse_args()

    if args.command == "compile":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        compile_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
        print("\nCompilation finished. FastAPI app is ready in", output_dir)
    elif args.command == "inspect":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        inspect_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
    elif args.command == "export-openapi":
        export_openapi_schema(args.output)
    elif args.command == "serve":
        serve_notebook(args.notebook, args.output)
    elif args.command == "deploy":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        compile_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
        # Build Docker image
        dockerfile_path = output_dir / "Dockerfile"
        if not dockerfile_path.is_file():
            raise FileNotFoundError(f"Dockerfile not found at {dockerfile_path}. Ensure the compiler generated it.")
        print(f"Building Docker image '{args.tag}' from {output_dir} …")
        subprocess.run(["docker", "build", "-t", args.tag, "."], cwd=str(output_dir), check=True)
        print(f"Docker image '{args.tag}' built successfully.")

if __name__ == "__main__":
    main()
