"""The CLI lists per-notebook apps and passes --notebook to the generated-file and download commands."""
import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import zipfile
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _zip_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("app.py", "x = 1\n")
    return buffer.getvalue()


class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []
    apps = {"status": "success", "total_count": 0, "apps": []}
    fail = None

    def _answer(self):
        url = urllib.parse.urlsplit(self.path)
        type(self).seen.append((self.command, url.path, dict(urllib.parse.parse_qsl(url.query))))
        if type(self).fail:
            status, body, ctype = type(self).fail, json.dumps({"detail": "boom from the dashboard"}).encode(), "application/json"
        elif url.path == "/api/apps":
            status, body, ctype = 200, json.dumps(type(self).apps).encode(), "application/json"
        elif url.path == "/api/download":
            status, body, ctype = 200, _zip_bytes(), "application/zip"
        elif url.path.startswith("/api/generated/"):
            status, body, ctype = 200, json.dumps({"filename": url.path.rsplit("/", 1)[1], "content": "x = 1\n", "size_bytes": 6}).encode(), "application/json"
        else:
            status, body, ctype = 200, json.dumps({"status": "success", "files": [], "total_count": 0, "filename": "", "removed": [], "dry_run": False}).encode(), "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_DELETE = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def dashboard():
    _Handler.seen, _Handler.fail = [], None
    _Handler.apps = {"status": "success", "total_count": 0, "apps": []}
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _cli(url, *args):
    return subprocess.run([sys.executable, "-m", "backend.cli", *args, "--dashboard-url", url], capture_output=True, text=True, cwd=ROOT,
                          env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"})


def test_apps_lists_each_app_with_its_state(dashboard):
    _Handler.apps = {"status": "success", "total_count": 2, "apps": [
        {"app_id": "a_1", "notebook": "a.ipynb", "status": "compiled", "compiled_at": "2026-11-20T00:00:00+00:00", "notebook_exists": True},
        {"app_id": "x_2", "notebook": None, "status": "incomplete", "compiled_at": None, "notebook_exists": False},
    ]}
    done = _cli(dashboard, "apps")
    assert done.returncode == 0, done.stderr
    assert "2 per-notebook app(s)" in done.stdout and "a.ipynb  compiled" in done.stdout
    assert "(unknown notebook, x_2)" in done.stdout and "no longer uploaded" in done.stdout
    assert _Handler.seen == [("GET", "/api/apps", {})]


def test_apps_json_and_empty_and_failure(dashboard):
    assert "No per-notebook apps" in _cli(dashboard, "apps").stdout
    assert json.loads(_cli(dashboard, "apps", "--json").stdout)["total_count"] == 0
    _Handler.fail = 500
    failed = _cli(dashboard, "apps")
    assert failed.returncode == 1 and "boom from the dashboard" in failed.stderr and "500" in failed.stderr


@pytest.mark.parametrize("args,path", [
    (("generated", "list"), "/api/generated"),
    (("generated", "show", "app.py"), "/api/generated/app.py"),
    (("generated", "delete", "--yes"), "/api/generated"),
    (("remote-files", "list"), "/api/generated"),
    (("remote-files", "get", "app.py"), "/api/generated/app.py"),
    (("remote-files", "delete", "--yes"), "/api/generated"),
])
def test_generated_file_commands_target_the_chosen_notebook(dashboard, args, path):
    done = _cli(dashboard, *args, "--notebook", "my_model.ipynb")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _Handler.seen[-1][1] == path and _Handler.seen[-1][2]["notebook"] == "my_model.ipynb"


def test_remote_build_downloads_that_notebooks_app(dashboard, tmp_path):
    out = tmp_path / "app.zip"
    done = _cli(dashboard, "remote-build", "--notebook", "my_model.ipynb", "--output", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    assert _Handler.seen[-1] == ("GET", "/api/download", {"notebook": "my_model.ipynb"})
    assert zipfile.ZipFile(out).namelist() == ["app.py"]


@pytest.mark.parametrize("args", [("generated", "list"), ("generated", "show", "app.py"), ("remote-files", "list"), ("remote-build",)])
def test_legacy_commands_send_no_notebook(dashboard, args, tmp_path):
    extra = ("--output", str(tmp_path / "legacy.zip")) if args == ("remote-build",) else ()
    done = _cli(dashboard, *args, *extra)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "notebook" not in _Handler.seen[-1][2]


@pytest.mark.parametrize("bad", ["../x.ipynb", "a/b.ipynb", "model", "", "a\\b.ipynb"])
def test_invalid_notebook_identifiers_fail_before_any_request(dashboard, bad):
    done = _cli(dashboard, "generated", "list", "--notebook", bad)
    assert done.returncode == 1 and "--notebook must be an uploaded notebook's file name" in done.stderr
    assert _Handler.seen == []


def test_a_dashboard_error_for_an_unknown_notebook_is_reported_with_its_detail(dashboard):
    _Handler.fail = 404
    done = _cli(dashboard, "generated", "list", "--notebook", "missing.ipynb")
    assert done.returncode == 1 and "boom from the dashboard" in done.stderr + done.stdout
