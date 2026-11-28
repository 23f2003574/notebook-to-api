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
  - Opt-in resources for `kubernetes.yaml`, chosen with environment variables when you compile: `NOTEBOOK_API_K8S_REPLICAS` (1-50), `NOTEBOOK_API_K8S_PDB=1` (a PodDisruptionBudget; needs 2+ replicas), `NOTEBOOK_API_K8S_INGRESS_HOST` plus `NOTEBOOK_API_K8S_INGRESS_TLS_SECRET` (a TLS-only Ingress; optional `NOTEBOOK_API_K8S_INGRESS_CLASS`), and `NOTEBOOK_API_K8S_PVC_SIZE` (a PersistentVolumeClaim for the task store, single replica only; optional `NOTEBOOK_API_K8S_STORAGE_CLASS`).
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
.venv/bin/python -m pytest -n auto --dist loadgroup   # parallel, as CI runs it (shared-resource files stay serial)
.venv/bin/python -m pytest tests/test_compiler.py -k dockerfile
```

## Dashboard (optional)
A web dashboard for uploading, inspecting and deploying notebooks. `./start-dashboard.sh` starts the API (`python -m backend.dashboard`, default port 8001, override with `NOTEBOOK_API_DASHBOARD_PORT`) and the frontend dev server (`npm run dev` in `frontend/`, port 5173, needs Node.js and `npm install` once).

The dashboard API can upload, delete and compile notebooks and manage secrets and API keys, so it is secured by default:

- It binds to `127.0.0.1` (`NOTEBOOK_API_DASHBOARD_HOST`), reachable only from the same machine.
- Several clients can each have their own key: `NOTEBOOK_API_DASHBOARD_KEYS=ci:<key>,ops:sha256:<digest>` (or one `name:key` per line in the file `NOTEBOOK_API_DASHBOARD_KEYS_FILE`). A key is a secret or the `sha256:` digest of one; remove an entry to rotate it out. The key's name is the actor on governance audit records. In production (`NOTEBOOK2API_ENVIRONMENT=production`) on a non-loopback host, plaintext keys must be at least 16 characters. The single `NOTEBOOK_API_DASHBOARD_KEY` below keeps working as the key named `default`.
- `NOTEBOOK_API_DASHBOARD_KEY`, when set, requires every request to send it as the `X-API-Key` header (the frontend asks for it on the page and keeps it for the browser tab only).
- Without a key, only requests from this machine are served, whichever way the dashboard is launched (`python -m backend.dashboard`, `uvicorn backend.dashboard:app`, a container); any other peer gets `401`. Every route, including `/api/health`, then requires the `X-API-Key` header when a key is set.
- Binding to any non-loopback host (for example `0.0.0.0`) **without** `NOTEBOOK_API_DASHBOARD_KEY` is refused at startup with an error. To expose the dashboard on the network, set both, for example `NOTEBOOK_API_DASHBOARD_HOST=0.0.0.0 NOTEBOOK_API_DASHBOARD_KEY=<secret> ./start-dashboard.sh`, and serve it over HTTPS (`NOTEBOOK_API_DASHBOARD_SSL_KEYFILE`/`NOTEBOOK_API_DASHBOARD_SSL_CERTFILE` or a TLS-terminating proxy).

## Trash (soft delete)
Deleting is soft by default: the dashboard moves a notebook, with its saved versions, data files and compiled app, to a trash instead of removing it. This is the default for every delete (API, `delete`, `delete-batch`, `resolve-duplicates`); pass `--permanent` (API: `permanent=true`) to erase for good, or set `NOTEBOOK_API_SOFT_DELETE=false` on the dashboard to opt out. From the CLI use `delete <notebook>`, then `trash list`, `trash restore <id>` and `trash purge [--everything]`. Entries expire after `NOTEBOOK_API_TRASH_RETENTION_DAYS` (default 30); listing never deletes anything, and expired entries are removed by `trash purge` and, automatically, by a sweep the dashboard runs at startup and then every `NOTEBOOK_API_TRASH_PURGE_INTERVAL_SECONDS` (default 3600; `0` turns it off). Each purge that removes something is recorded in the governance audit trail.

## Building the Docker Image
Make sure Docker is installed on your system.
```bash
docker build -t notebook-api generated/
```
Run the container:
```bash
docker run -p 8000:8000 notebook-api
```

The Dockerfile starts from `python:<version>-slim`, a tag that moves. For reproducible builds pin the
exact base image by digest: `compile notebook.ipynb --base-image python@sha256:<64 hex digest>` (or set
`NOTEBOOK_API_BASE_IMAGE`). With `--require-pinned` the compile is refused unless the base image is
digest-pinned and every requirement is version-pinned.
Visit `http://localhost:8000/docs` to explore the generated API.

### Hash-locked builds and the SBOM

`compile --resolve-versions --require-hashes` writes `requirements.txt` as a complete lock (every direct and
transitive package with its SHA-256 hashes), makes the Dockerfile install it with `pip install --require-hashes`,
and writes `sbom.cdx.json` beside it: a CycloneDX 1.5 SBOM of exactly those packages (name, version, purl,
hashes). It is deterministic, so equal locks give equal bytes; feed it to any CycloneDX-aware scanner
(for example `grype sbom:sbom.cdx.json`).

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

## Dashboard storage limits

The generated dashboard container bounds its own storage by default: `NOTEBOOK_API_MAX_NOTEBOOKS=1000`, `NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES=5368709120` (notebooks plus their versions) and `NOTEBOOK_API_MIN_FREE_DISK_BYTES=536870912`. Uploads and archive imports are refused with HTTP 507 once the volume has less than that reserve free. Override any of them in the compose or Kubernetes environment; `0` means unlimited, which is also the default for bare local runs. `GET /api/config` and `GET /api/notebooks/storage` report the limits and current usage.
