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
# Everything the dashboard keeps lives under /data, so one mounted volume makes a restart or a
# replaced container lose nothing: uploaded notebooks (with their versions, data files and history)
# and the compiled apps, besides the audit database.
UPLOAD_DIR = "/data/uploads"
GENERATED_DIR = "/data/generated"
GENERATED_APPS_DIR = "/data/generated_apps"
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
    && mkdir -p /data/governance {UPLOAD_DIR} {GENERATED_DIR} {GENERATED_APPS_DIR} \\
    && chown -R appuser:appuser /app /data
ENV NOTEBOOK2API_ENVIRONMENT=production \\
    NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND=sqlite \\
    NOTEBOOK2API_GOVERNANCE_DATABASE_PATH={AUDIT_DB} \\
    NOTEBOOK_API_UPLOAD_DIR={UPLOAD_DIR} \\
    NOTEBOOK_API_GENERATED_DIR={GENERATED_DIR} \\
    NOTEBOOK_API_GENERATED_APPS_DIR={GENERATED_APPS_DIR} \\
    NOTEBOOK_API_DASHBOARD_HOST=0.0.0.0 \\
    NOTEBOOK_API_MAX_NOTEBOOKS=1000 \\
    NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES=5368709120 \\
    NOTEBOOK_API_MIN_FREE_DISK_BYTES=536870912 \\
    XDG_CACHE_HOME=/tmp/cache MPLCONFIGDIR=/tmp/matplotlib
# Notebooks, versions, generated apps and the audit database all live here: mount a volume (the
# compose and Kubernetes files do). In production the dashboard refuses to start when the audit
# database is not on durable storage, and /api/health reports whether the notebook storage is.
# The root filesystem can be read-only: only /data and /tmp are written.
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
      NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT: "1"
      NOTEBOOK_API_SOFT_DELETE: ${{NOTEBOOK_API_SOFT_DELETE:-true}}
      NOTEBOOK_API_TRASH_RETENTION_DAYS: ${{NOTEBOOK_API_TRASH_RETENTION_DAYS:-30}}
      NOTEBOOK_API_MAX_NOTEBOOKS: ${{NOTEBOOK_API_MAX_NOTEBOOKS:-1000}}
      NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES: ${{NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES:-5368709120}}
      NOTEBOOK_API_MIN_FREE_DISK_BYTES: ${{NOTEBOOK_API_MIN_FREE_DISK_BYTES:-536870912}}
    volumes:
      - dashboard-data:/data
    read_only: true          # only the /data volume and /tmp are writable
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
volumes:
  dashboard-data:
"""

KUBERNETES = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: notebook2api-dashboard-data
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 5Gi
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
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000          # makes the mounted volume writable for the non-root user
        seccompProfile: {{type: RuntimeDefault}}
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
            - name: NOTEBOOK_API_UPLOAD_DIR
              value: {UPLOAD_DIR}
            - name: NOTEBOOK_API_GENERATED_DIR
              value: {GENERATED_DIR}
            - name: NOTEBOOK_API_GENERATED_APPS_DIR
              value: {GENERATED_APPS_DIR}
            - name: NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT
              value: "1"
            - name: NOTEBOOK_API_MAX_NOTEBOOKS
              value: "1000"
            - name: NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES
              value: "5368709120"
            - name: NOTEBOOK_API_MIN_FREE_DISK_BYTES
              value: "536870912"
          # The probes run the image's own health check, which sends the dashboard key from the
          # container's environment (an httpGet probe cannot carry a Secret value).
          startupProbe:
            exec: {{command: ["python", "-m", "backend.dashboard_healthcheck"]}}
            periodSeconds: 5
            failureThreshold: 24
          readinessProbe:
            exec: {{command: ["python", "-m", "backend.dashboard_healthcheck"]}}
            periodSeconds: 10
            timeoutSeconds: 5
          livenessProbe:
            exec: {{command: ["python", "-m", "backend.dashboard_healthcheck"]}}
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          resources:
            requests: {{cpu: 250m, memory: 512Mi}}
            limits: {{memory: 2Gi}}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true      # only /data (the volume) and /tmp are writable
            capabilities: {{drop: ["ALL"]}}
          volumeMounts:
            - name: data
              mountPath: /data
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: notebook2api-dashboard-data
        - name: tmp
          emptyDir: {{sizeLimit: 1Gi}}   # scratch space only: nothing durable lives here
"""

ENV_EXAMPLE = """# Required: the dashboard refuses to listen on a non-loopback address without a key. Use at least
# 16 random characters (production refuses weaker keys), e.g. `openssl rand -hex 24`.
NOTEBOOK_API_DASHBOARD_KEY=change-me

# Optional: make every delete (API and CLI) move the notebook to the trash, where `trash restore`
# can bring it back until it expires; the UI always uses the trash.
# NOTEBOOK_API_SOFT_DELETE=true  (the default: deletes go to the trash; false deletes for good)
# NOTEBOOK_API_TRASH_RETENTION_DAYS=30

# Container deployments bound storage by default (0 means unlimited): at most this many notebooks,
# this many bytes of notebooks plus versions, and uploads are refused with HTTP 507 once the volume
# has less than the reserve free.
# NOTEBOOK_API_MAX_NOTEBOOKS=1000
# NOTEBOOK_API_MAX_TOTAL_STORAGE_BYTES=5368709120
# NOTEBOOK_API_MIN_FREE_DISK_BYTES=536870912
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
- **Everything lives under `/data`**: uploaded notebooks with their versions and data files, the
  compiled apps and the audit database. Replacing the container (a rebuild, an upgrade, `docker compose up
  --build`) loses nothing as long as the same volume is mounted. `GET /api/health` reports
  `notebook_storage.durable`, and the Prometheus gauge `notebook_to_api_notebook_storage_durable` mirrors it.
  The root filesystem is read-only in the compose and Kubernetes files; only `/data` and `/tmp` are writable.
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
