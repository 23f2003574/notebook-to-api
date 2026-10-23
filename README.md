# Notebook to API Compiler

A tool that compiles a Jupyter notebook into a FastAPI microservice, automatically handling:
- Extraction of code cells and functions
- Dependency detection and `requirements.txt` generation
- Generation of a Dockerfile for containerized deployment
- Creation of a runtime module to execute notebook code

## Setup
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m backend.cli doctor   # checks Python, Docker and required packages
```
The compiled app imports the notebook's own libraries (pandas, scikit-learn, ...). To run it locally -- `serve`, `compile --smoke-test`, `export-openapi`, `export-sdk` -- install them into the same environment first: `.venv/bin/pip install -r generated/requirements.txt`. Docker builds install them for you.

## Usage
```bash
# Compile a notebook (--output is a directory, not a file)
.venv/bin/python -m backend.cli compile notebooks/sample.ipynb --output generated
```
This will generate:
- `generated/app.py` – FastAPI app
- `generated/runtime/notebook_module.py` – Runtime module containing notebook code
- `generated/requirements.txt` – Dependencies (including detected imports)
- `generated/Dockerfile` – Dockerfile for container build
- `generated/docker-compose.yml`, `generated/kubernetes.yaml`, `generated/.env.example` and `generated/.dockerignore` – deployment helpers
- `generated/README.md` – docs for the generated service (endpoints, environment variables)

Every generated endpoint needs an `X-API-Key` header. It defaults to `notebook-to-api-dev-key` for local development; set `NOTEBOOK_API_KEY` before exposing the service.

## Building the Docker Image
Make sure Docker is installed on your system.
```bash
docker build -t notebook-api generated/
```
Run the container:
```bash
docker run -p 8000:8000 notebook-api
```
Visit `http://localhost:8000/docs` to explore the generated API.

## Deployment Governance Persistence Diagnostics
Inspect the active governance persistence backend:
```bash
.venv/bin/python -m backend.cli governance doctor
```
Perform a full persisted-record integrity audit:
```bash
.venv/bin/python -m backend.cli governance doctor --deep
```
Emit machine-readable diagnostics:
```bash
.venv/bin/python -m backend.cli governance doctor --deep --json
```
Exit codes:
- `0` persistence healthy
- `1` diagnostics completed but persistence unhealthy
- `2` diagnostics could not be completed

## Next Steps
- One-command deployment already exists: `.venv/bin/python -m backend.cli deploy <notebook.ipynb>` compiles and builds the Docker image (see `deploy --help`).
- Add more advanced type handling.
