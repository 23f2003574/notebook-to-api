"""Deployment files for the dashboard itself: a buildable, self-contained image with a durable
governance audit trail.

The image is built from the repository checkout (the build context is the repository root, which
holds backend/, requirements.txt and frontend/): a multi-stage build compiles the React frontend,
then a slim Python image carries the backend, the built UI and a non-root user. Nothing here ever
contains a credential: the dashboard key comes from the environment (compose) or an existing
Secret (Kubernetes)."""
from __future__ import annotations

import os
from pathlib import Path

AUDIT_DB = "/data/governance/audit.db"
REPO_ROOT = Path(__file__).resolve().parents[2]

DOCKERFILE = f"""# Build from the repository root (the build context):
#   docker build -f {{dockerfile}} -t notebook2api-dashboard .
# or use the generated docker-compose.yml, which sets the context for you.

FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# The UI talks to the dashboard it is served from (same origin): no URL to bake in.
ARG VITE_APP_URL=
ENV VITE_APP_URL=$VITE_APP_URL
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt
COPY backend ./backend
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN useradd --create-home --uid 1000 appuser \\
    && mkdir -p /data/governance \\
    && chown -R appuser:appuser /app /data
ENV NOTEBOOK2API_ENVIRONMENT=production \\
    NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND=sqlite \\
    NOTEBOOK2API_GOVERNANCE_DATABASE_PATH={AUDIT_DB} \\
    NOTEBOOK_API_DASHBOARD_HOST=0.0.0.0
# The audit database lives here: mount a volume (the compose and Kubernetes files do). In
# production the dashboard refuses to start when this directory is not on durable storage.
VOLUME /data
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \\
    CMD ["python", "-m", "backend.dashboard_healthcheck"]
CMD ["python", "-m", "backend.dashboard"]
"""

DOCKERIGNORE = """# Keeps the build context small and free of local state and secrets.
.git
.github
.venv
venv
**/__pycache__
**/*.pyc
**/node_modules
frontend/dist
frontend/test-results
frontend/playwright-report
tests
uploads
generated
generated_apps
data
*.db
.env
.env.*
"""

COMPOSE = f"""services:
  dashboard:
    build:
      context: {{context}}
      dockerfile: {{dockerfile}}
    image: notebook2api-dashboard:latest
    ports:
      - "8001:8001"
    environment:
      NOTEBOOK_API_DASHBOARD_KEY: ${{NOTEBOOK_API_DASHBOARD_KEY:?set NOTEBOOK_API_DASHBOARD_KEY in .env}}
      NOTEBOOK2API_ENVIRONMENT: production
      NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND: sqlite
      NOTEBOOK2API_GOVERNANCE_DATABASE_PATH: {AUDIT_DB}
      NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT: "1"
    volumes:
      - governance-data:/data
    restart: unless-stopped
volumes:
  governance-data:
"""

KUBERNETES = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: notebook2api-dashboard-data
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notebook2api-dashboard
spec:
  replicas: 1          # SQLite is a single-writer store on a ReadWriteOnce volume
  strategy:
    type: Recreate     # never two pods on the same audit database during a rollout
  selector:
    matchLabels: {{app: notebook2api-dashboard}}
  template:
    metadata:
      labels: {{app: notebook2api-dashboard}}
    spec:
      containers:
        - name: dashboard
          image: notebook2api-dashboard:latest
          ports:
            - containerPort: 8001
          env:
            - name: NOTEBOOK_API_DASHBOARD_KEY
              valueFrom:
                secretKeyRef: {{name: notebook2api-dashboard, key: dashboard-key}}
            - name: NOTEBOOK2API_ENVIRONMENT
              value: production
            - name: NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND
              value: sqlite
            - name: NOTEBOOK2API_GOVERNANCE_DATABASE_PATH
              value: {AUDIT_DB}
            - name: NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT
              value: "1"
          volumeMounts:
            - name: data
              mountPath: /data
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: notebook2api-dashboard-data
"""

ENV_EXAMPLE = """# Required: the dashboard refuses to listen on a non-loopback address without a key. Use at least
# 16 random characters (production refuses weaker keys), e.g. `openssl rand -hex 24`.
NOTEBOOK_API_DASHBOARD_KEY=change-me
"""

README = """# Dashboard container

Build and run, from the repository root (the build context):

```bash
docker build -f {dockerfile} -t notebook2api-dashboard .
docker run --rm -p 8001:8001 -v n2a-data:/data -e NOTEBOOK_API_DASHBOARD_KEY=<a 16+ character key> notebook2api-dashboard
```

or `cp .env.example .env`, set the key, and `docker compose -f {compose} up --build`.

- The image serves the dashboard UI and API on port 8001 as a non-root user, and reports health through
  `python -m backend.dashboard_healthcheck` (GET /api/health).
- **Audit storage must be durable.** The image runs with `NOTEBOOK2API_ENVIRONMENT=production`, so the
  dashboard refuses to start when the governance audit database is on the container's disposable layer.
  Mount a volume at `/data` (the compose and Kubernetes files do) and assert it with
  `NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT=1`.
- **Docker access.** The image has no Docker CLI and no access to a Docker daemon, so compiling,
  inspecting and managing notebooks work, but the dashboard's *Deploy* (building an image) cannot run
  inside it. Build images from a machine with Docker: `notebook-to-api remote-build` downloads the
  compiled app from this dashboard, then `docker build` it. Giving the container the host's Docker socket
  would hand it control of the host, so this is deliberately not configured; do that only if you accept
  that trade-off and secure the dashboard accordingly.
"""


def _posix(path: str) -> str:
    return Path(path).as_posix()


def generate_dashboard_deployment(output_dir: str | Path, repo_root: str | Path | None = None) -> list[str]:
    """Write the dashboard's Dockerfile, Dockerfile.dockerignore, compose, Kubernetes, .env.example
    and README files. The build context is the repository root, so the compose file points at it
    relative to `output_dir`. Raises ValueError when `repo_root` lacks the sources the image needs
    (the dashboard image cannot be built from an installed package alone)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    missing = [name for name in ("backend", "requirements.txt", "constraints.txt", "frontend/package-lock.json")
               if not (root / name).exists()]
    if missing:
        raise ValueError(
            f"{root} is not a notebook-to-api checkout (missing {', '.join(missing)}); the dashboard image "
            "is built from the repository's backend/ and frontend/ sources"
        )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    dockerfile = _posix(os.path.relpath((out / "Dockerfile").resolve(), root.resolve()))
    compose = _posix(os.path.relpath((out / "docker-compose.yml").resolve(), root.resolve()))
    context = _posix(os.path.relpath(root.resolve(), out.resolve()))
    files = {
        "Dockerfile": DOCKERFILE.replace("{dockerfile}", dockerfile),
        "Dockerfile.dockerignore": DOCKERIGNORE,
        "docker-compose.yml": COMPOSE.replace("{context}", context).replace("{dockerfile}", dockerfile),
        "kubernetes.yaml": KUBERNETES,
        ".env.example": ENV_EXAMPLE,
        "README.md": README.replace("{dockerfile}", dockerfile).replace("{compose}", compose),
    }
    for name, text in files.items():
        (out / name).write_text(text)
    return list(files)
