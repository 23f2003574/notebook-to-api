# Notebook to API Compiler

A tool that compiles a Jupyter notebook into a FastAPI microservice, automatically handling:
- Extraction of code cells and functions
- Dependency detection and `requirements.txt` generation
- Generation of a Dockerfile for containerized deployment
- Creation of a runtime module to execute notebook code

## Setup
Requires Python 3.11 or newer (CI runs the test suite on 3.11 and 3.13) and, for `deploy`, Docker.
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # use requirements-dev.txt to also run the tests
.venv/bin/python -m backend.cli doctor   # checks Python, Docker and required packages
```
The compiled app imports the notebook's own libraries (pandas, scikit-learn, ...). To run it locally -- `serve`, `compile --smoke-test`, `export-openapi`, `export-sdk` -- install them into the same environment first: `.venv/bin/pip install -r generated/requirements.txt`. Docker builds install them for you.

Optionally, `.venv/bin/pip install .` installs a `notebook-to-api` command, so `notebook-to-api compile notebook.ipynb` works from any directory.

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

## Run Locally (no Docker)
```bash
# the sample imports pandas and numpy: install the notebook's own libraries first
.venv/bin/python -m backend.cli compile notebooks/sample.ipynb --output generated
.venv/bin/pip install -r generated/requirements.txt
# compiles the notebook and serves it, recompiling when the notebook changes
.venv/bin/python -m backend.cli serve notebooks/sample.ipynb --port 8000
# in another terminal:
curl -X POST http://localhost:8000/add -H "X-API-Key: notebook-to-api-dev-key" -H "Content-Type: application/json" -d '{"a": 2, "b": 3}'
# {"result":5}
```

## Running the Tests
After installing `requirements-dev.txt`, run the same command CI runs from the repository root (`pytest.ini` already puts the repo on the import path). The suite is large; pass a file or `-k` expression to run a part of it:
```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_compiler.py -k dockerfile
```

## Dashboard (optional)
A web dashboard for uploading, inspecting and deploying notebooks. `./start-dashboard.sh` starts the API (`python -m backend.dashboard`, default port 8001, override with `NOTEBOOK_API_DASHBOARD_PORT`) and the frontend dev server (`npm run dev` in `frontend/`, port 5173, needs Node.js and `npm install` once).

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
