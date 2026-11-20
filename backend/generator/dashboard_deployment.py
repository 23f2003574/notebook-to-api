"""Deployment files for the dashboard itself, with a durable governance audit trail.

The governance audit chain is written by the dashboard process (compile and deploy outcomes), so
that process needs a persistent volume. These files never contain credentials: the dashboard key
is read from the environment (compose) or an existing Secret (Kubernetes)."""
from __future__ import annotations

from pathlib import Path

AUDIT_DB = "/data/governance/audit.db"

DOCKERFILE = f"""FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
ENV NOTEBOOK2API_ENVIRONMENT=production \\
    NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND=sqlite \\
    NOTEBOOK2API_GOVERNANCE_DATABASE_PATH={AUDIT_DB} \\
    NOTEBOOK_API_DASHBOARD_HOST=0.0.0.0
RUN mkdir -p /data/governance
# The audit database lives here: mount a volume (the compose and Kubernetes files do).
VOLUME /data
EXPOSE 8001
CMD ["python", "-m", "backend.dashboard"]
"""

COMPOSE = f"""services:
  dashboard:
    build: .
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

ENV_EXAMPLE = """# Required: the dashboard refuses to listen on a non-loopback address without a key.
NOTEBOOK_API_DASHBOARD_KEY=change-me
"""


def generate_dashboard_deployment(output_dir: str | Path) -> list[str]:
    """Write the dashboard's Dockerfile, compose, Kubernetes and .env.example files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "Dockerfile": DOCKERFILE,
        "docker-compose.yml": COMPOSE,
        "kubernetes.yaml": KUBERNETES,
        ".env.example": ENV_EXAMPLE,
    }
    for name, text in files.items():
        (out / name).write_text(text)
    return list(files)
