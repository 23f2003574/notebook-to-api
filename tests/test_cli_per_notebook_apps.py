"""remote-compile/remote-export/remote-deploy can target a notebook's own app on the dashboard."""
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).seen.append((self.path, json.loads(body or b"{}")))
        payload = json.dumps({"status": "success", "schema": {}, "content": "x", "language": "python",
                              "functions": [], "endpoints": [], "dependencies": [], "message": "ok",
                              "image": "x:latest", "dry_run": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def dashboard():
    _Handler.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _cli(url, *args):
    return subprocess.run([sys.executable, "-m", "backend.cli", *args, "--dashboard-url", url],
                          capture_output=True, text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)})


def test_remote_compile_per_notebook_flag_is_sent(dashboard):
    assert _cli(dashboard, "remote-compile", "a.ipynb", "--per-notebook").returncode == 0
    assert _Handler.seen[-1] == ("/api/compile", {"notebook_path": "a.ipynb", "per_notebook": True})
    assert _cli(dashboard, "remote-compile", "a.ipynb").returncode == 0
    assert "per_notebook" not in _Handler.seen[-1][1]


@pytest.mark.parametrize("sub,path", [("openapi", "/api/export-openapi"), ("sdk", "/api/export-sdk")])
def test_remote_export_targets_the_named_notebook(dashboard, sub, path):
    assert _cli(dashboard, "remote-export", sub, "--notebook", "a.ipynb").returncode == 0
    assert _Handler.seen[-1][0] == path and _Handler.seen[-1][1]["notebook"] == "a.ipynb"
    assert _cli(dashboard, "remote-export", sub).returncode == 0
    assert "notebook" not in _Handler.seen[-1][1]


def test_remote_deploy_targets_the_named_notebook(dashboard):
    _cli(dashboard, "remote-deploy", "--notebook", "a.ipynb", "--dry-run")
    assert _Handler.seen[-1][0] == "/api/deploy" and _Handler.seen[-1][1]["notebook"] == "a.ipynb"
