"""Start a generated docker-compose project for real and hit /health.

Static tests only read the compose file; this builds the image and runs the
service the way `docker compose up` does for a user. Skipped without a Docker
daemon (CI's docker-smoke job runs it).
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HEALTH_TIMEOUT_SECONDS = 120


def _docker_compose_available():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0 and (
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    )


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not _docker_compose_available(), reason="needs a running Docker daemon with compose")
def test_generated_compose_project_serves_health(tmp_path):
    out = tmp_path / "sample_api"
    subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(ROOT / "notebooks" / "sample.ipynb"), "--output", str(out)],
        cwd=ROOT, check=True, capture_output=True, timeout=300,
    )
    port = _free_port()
    project = "n2a-smoke-" + uuid.uuid4().hex[:8]
    compose = ["docker", "compose", "-p", project, "-f", str(out / "docker-compose.yml")]
    env = {**os.environ, "PORT": str(port), "NOTEBOOK_API_KEY": "smoke-test-only-key-" + uuid.uuid4().hex}
    try:
        subprocess.run(compose + ["up", "-d", "--build"], cwd=out, env=env, check=True, capture_output=True, timeout=900)
        deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
        status = None
        while time.monotonic() < deadline and status != 200:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                    status = response.status
            except OSError:
                time.sleep(1)
        assert status == 200, subprocess.run(compose + ["logs"], cwd=out, env=env, capture_output=True, text=True).stdout
    finally:
        subprocess.run(compose + ["down", "--volumes", "--rmi", "local", "--remove-orphans"], cwd=out, env=env, capture_output=True, timeout=300)


def _docker_available():
    return bool(shutil.which("docker")) and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="needs a running Docker daemon")
def test_plain_docker_run_keeps_background_tasks_across_a_restart(tmp_path):
    """No Compose, no Kubernetes: just the generated image and a volume."""
    import json

    import nbformat

    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def train_model(n: int = 1):\n    return {'n': n}\n"))
    nbformat.write(nb, tmp_path / "nb.ipynb")
    out = tmp_path / "persist_api"
    subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(tmp_path / "nb.ipynb"), "--output", str(out)],
        cwd=ROOT, check=True, capture_output=True, timeout=300,
    )
    suffix = uuid.uuid4().hex[:8]
    image, name, volume = f"n2a-persist-{suffix}", f"n2a-persist-{suffix}", f"n2a-persist-vol-{suffix}"
    key = "smoke-test-only-key-" + uuid.uuid4().hex
    port = _free_port()

    def call(path, method="GET", body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def wait_healthy():
        deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                return call("/health")
            except OSError:
                time.sleep(1)
        raise AssertionError(subprocess.run(["docker", "logs", name], capture_output=True, text=True).stdout)

    try:
        subprocess.run(["docker", "build", "-t", image, str(out)], check=True, capture_output=True, timeout=900)
        subprocess.run(
            ["docker", "run", "-d", "--name", name, "--read-only", "--tmpfs", "/tmp", "-v", f"{volume}:/data", "-e", f"NOTEBOOK_API_KEY={key}",
             "-p", f"127.0.0.1:{port}:8000", image],
            check=True, capture_output=True, timeout=120,
        )
        wait_healthy()
        task_id = call("/train_model", "POST", {"n": 3})["task_id"]
        deadline = time.monotonic() + 30
        while call(f"/tasks/{task_id}")["status"] not in ("completed", "failed") and time.monotonic() < deadline:
            time.sleep(0.5)
        subprocess.run(["docker", "restart", name], check=True, capture_output=True, timeout=120)
        wait_healthy()
        restored = call(f"/tasks/{task_id}")
        assert restored["status"] == "completed" and restored["result"] == {"n": 3}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=120)
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=120)
        subprocess.run(["docker", "rmi", "-f", image], capture_output=True, timeout=120)
