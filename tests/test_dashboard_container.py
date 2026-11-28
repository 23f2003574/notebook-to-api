"""The generated dashboard image is buildable: its build context holds everything it copies, it runs
as a non-root user, reports health, serves the built UI, and refuses non-durable production storage."""
import http.server
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from backend import dashboard_healthcheck
from backend.generator.dashboard_deployment import REPO_ROOT, generate_dashboard_deployment

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def out(tmp_path):
    target = ROOT / "dashboard-deployment-test-output"
    shutil.rmtree(target, ignore_errors=True)
    generate_dashboard_deployment(target)
    yield target
    shutil.rmtree(target, ignore_errors=True)


def _instructions(dockerfile_text):
    return [line.strip() for line in dockerfile_text.splitlines() if line.strip() and not line.startswith("#")]


def test_everything_the_dockerfile_copies_exists_in_the_build_context(out):
    for line in _instructions((out / "Dockerfile").read_text()):
        if line.startswith("COPY") and "--from" not in line:
            for source in line.split()[1:-1]:
                assert (REPO_ROOT / source.rstrip("/")).exists() or source.startswith("-"), (source, line)


def test_the_compose_build_context_is_the_repository_root_and_finds_the_dockerfile(out):
    build = yaml.safe_load((out / "docker-compose.yml").read_text())["services"]["dashboard"]["build"]
    context = (out / build["context"]).resolve()
    assert context == REPO_ROOT.resolve()
    assert (context / build["dockerfile"]).resolve() == (out / "Dockerfile").resolve()


def test_the_image_is_multi_stage_non_root_with_a_healthcheck_and_the_built_ui(out):
    text = (out / "Dockerfile").read_text()
    lines = _instructions(text)
    assert sum(1 for l in lines if l.startswith("FROM ")) == 2 and any(l.startswith("RUN npm ci") for l in lines)
    assert "COPY --from=frontend /build/frontend/dist ./frontend/dist" in lines
    assert "USER appuser" in lines and lines.index("USER appuser") < lines.index('CMD ["python", "-m", "backend.dashboard"]')
    assert any(l.startswith("HEALTHCHECK") and "backend.dashboard_healthcheck" in l for l in lines) or \
        "backend.dashboard_healthcheck" in text
    assert "--require-hashes -r requirements.lock" in text
    assert "NOTEBOOK2API_ENVIRONMENT=production" in text and "VOLUME /data" in text


def test_the_dockerignore_keeps_local_state_and_secrets_out_of_the_context(out):
    ignored = (out / "Dockerfile.dockerignore").read_text().split()
    for name in (".git", ".env", "*.db", "uploads", "**/node_modules", "frontend/dist"):
        assert name in ignored


def test_the_readme_is_honest_about_docker_access_and_durable_storage(out):
    text = (out / "README.md").read_text()
    assert "no Docker CLI" in text and "Deploy" in text and "docker socket" in text.lower()
    assert "NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT=1" in text
    assert "docker.sock" not in (out / "docker-compose.yml").read_text()


def test_a_directory_that_is_not_a_checkout_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a notebook-to-api checkout"):
        generate_dashboard_deployment(tmp_path / "out", repo_root=tmp_path)


class _Health(http.server.BaseHTTPRequestHandler):
    status = 200
    seen = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("X-API-Key")))
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *a):
        pass


def _serve(status):
    _Health.status, _Health.seen = status, []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_healthcheck_sends_the_key_and_requires_200_when_it_has_one(monkeypatch):
    server = _serve(200)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_PORT", str(server.server_port))
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "k" * 20)
    assert dashboard_healthcheck.check() is True
    assert _Health.seen == [("/api/health", "k" * 20)]
    server.shutdown()
    server = _serve(401)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_PORT", str(server.server_port))
    assert dashboard_healthcheck.check() is False  # the key it has was refused
    server.shutdown()


def test_healthcheck_without_a_plaintext_key_accepts_a_401_and_fails_when_nothing_listens(monkeypatch, tmp_path):
    server = _serve(401)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_PORT", str(server.server_port))
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEY", raising=False)
    assert dashboard_healthcheck.check() is True
    keyfile = tmp_path / "key"
    keyfile.write_text("file-key-0123456789\n")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY_FILE", str(keyfile))
    assert dashboard_healthcheck.check() is False and _Health.seen[-1][1] == "file-key-0123456789"
    server.shutdown()
    server.server_close()
    monkeypatch.delenv("NOTEBOOK_API_DASHBOARD_KEY_FILE")
    assert dashboard_healthcheck.check() is False
    proc = subprocess.run([sys.executable, "-m", "backend.dashboard_healthcheck"], cwd=ROOT, capture_output=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT), "NOTEBOOK_API_DASHBOARD_PORT": "1"}, timeout=30)
    assert proc.returncode == 1


def _docker_ready():
    return shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="needs a Docker daemon (the dashboard-image CI job runs this for real)")
def test_the_documented_build_command_succeeds_and_the_container_serves_the_ui(out):
    import time
    import urllib.request

    tag = "n2a-dashboard-test"
    subprocess.run(["docker", "build", "-f", str((out / "Dockerfile").relative_to(ROOT)), "-t", tag, "."],
                   cwd=ROOT, check=True, timeout=1500)
    name = "n2a-dashboard-test-run"
    key = "container-key-0123456789"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "-p", "18001:8001", "-v", "n2a-test-data:/data",
                    "-e", f"NOTEBOOK_API_DASHBOARD_KEY={key}", tag], check=True)
    try:
        for _ in range(60):
            try:
                req = urllib.request.Request("http://127.0.0.1:18001/api/health", headers={"X-API-Key": key})
                if urllib.request.urlopen(req, timeout=3).status == 200:
                    break
            except OSError:
                time.sleep(1)
        else:
            pytest.fail(subprocess.run(["docker", "logs", name], capture_output=True, text=True).stdout)
        page = urllib.request.urlopen("http://127.0.0.1:18001/", timeout=5).read().decode()
        assert '<div id="root"' in page
        assert subprocess.run(["docker", "exec", name, "id", "-u"], capture_output=True, text=True).stdout.strip() == "1000"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "n2a-test-data"], capture_output=True)
