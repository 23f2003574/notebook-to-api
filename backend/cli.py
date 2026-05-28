import argparse
import os
import subprocess
from pathlib import Path

# Import the compiler function
from backend.compiler import compile_notebook


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

    # deploy command (compile + docker build)
    deploy_parser = subparsers.add_parser("deploy", help="Compile notebook and build Docker image.")
    deploy_parser.add_argument("notebook", help="Path to the notebook file.")
    deploy_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory for the generated code."
    )
    deploy_parser.add_argument(
        "--tag",
        default="notebook-api",
        help="Docker image tag to use when building."
    )

    args = parser.parse_args()

    if args.command == "compile":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        compile_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
        print("\nCompilation finished. FastAPI app is ready in", output_dir)
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
