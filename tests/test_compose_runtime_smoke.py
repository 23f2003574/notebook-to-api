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
