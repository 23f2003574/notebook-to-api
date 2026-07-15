import argparse
import os
import subprocess
import sys
from pathlib import Path

# Import the compiler function
from backend.compiler import compile_notebook
# Import inspector for analysis
from backend.inspector import inspect_notebook
from backend.serve import serve_notebook
from backend.observability.deployment_governance_doctor import (
    run_deployment_governance_doctor,
)
# export_openapi_schema is imported lazily (see below) because it imports
# generated/app.py at module load time, which re-executes a previously
# compiled notebook's top-level code as a side effect (stray stdout output).
# Importing it eagerly here would leak that output into every CLI
# invocation, including unrelated commands like `governance doctor --json`.


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

    # governance command group
    governance_parser = subparsers.add_parser(
        "governance", help="Inspect and manage deployment governance capabilities."
    )
    governance_subparsers = governance_parser.add_subparsers(
        dest="governance_command", required=True
    )

    doctor_parser = governance_subparsers.add_parser(
        "doctor",
        help="Inspect deployment governance persistence health.",
        description=(
            "Inspect deployment governance persistence health.\n\n"
            "By default, performs lightweight diagnostics. Use --deep to "
            "verify the integrity of every persisted governance trace.\n\n"
            "--deep persists its result as a durable audit-history record; "
            "running without --deep only reads existing audit history and "
            "does not create a new record.\n\n"
            "Exit codes: 0 healthy, 1 unhealthy, 2 diagnostics could not "
            "be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    doctor_parser.add_argument(
        "--deep",
        action="store_true",
        help="Perform a full persisted-record integrity audit.",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    doctor_parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        dest="batch_size",
        help="Number of persisted records read per integrity-audit batch. Default: 500.",
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
        from backend.exporters.openapi_exporter import export_openapi_schema
        export_openapi_schema(args.output)
    elif args.command == "serve":
        serve_notebook(args.notebook, args.output)
    elif args.command == "governance":
        if args.governance_command == "doctor":
            if args.batch_size <= 0:
                parser.error("--batch-size must be greater than zero")
            exit_code = run_deployment_governance_doctor(
                deep=args.deep,
                json_output=args.json_output,
                integrity_audit_batch_size=args.batch_size,
            )
            sys.exit(exit_code)
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
