"""OpenAPI preview/export must never execute uploaded notebook code in the dashboard process."""
import os
import time

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.exporters.openapi_exporter import isolated_openapi_schema
from backend.observability.deployment_governance_audit import reset_audit_service
from backend.routes import upload

CP, AD = "compile-key-0123456789", "admin-key-0123456789"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS_FILE", "NOTEBOOK2API_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(upload, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(upload, "GENERATED_APPS_DIR", str(tmp_path / "apps"))
    (tmp_path / "up").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS", f"ci:{CP}:compile,boss:{AD}:admin")
    monkeypatch.setenv("N2A_POC_SECRET_TOKEN", "dashboard-only-secret")
    reset_audit_service()
    yield
    reset_audit_service()


def _client(key):
    c = TestClient(dashboard.app)
    c.headers.update({"X-API-Key": key})
    return c


def _nb(code):
    return nbformat.writes(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(code)])).encode()


def _hostile(tmp_path):
    marker = tmp_path / "marker.txt"
    code = (f"import os\nopen({str(marker)!r}, 'w').write(str(os.getpid()) + ':' + os.environ.get('N2A_POC_SECRET_TOKEN', 'absent'))\n"
            "def f(a: int) -> int:\n    return a\n")
    return marker, code


def test_preview_runs_notebook_code_in_a_child_without_secrets(tmp_path):
    marker, code = _hostile(tmp_path)
    c = _client(CP)
    assert c.post("/api/upload", files={"file": ("a.ipynb", _nb(code))}).status_code == 200
    r = c.post("/api/openapi-preview", json={"notebook_path": "a.ipynb"})
    assert r.status_code == 200 and "/f" in r.json()["schema"]["paths"]
    pid, secret = marker.read_text().split(":", 1)
    assert int(pid) != os.getpid()
    assert secret == "absent"


def test_export_openapi_does_not_import_the_app_in_the_dashboard(tmp_path):
    marker, code = _hostile(tmp_path)
    c = _client(AD)
    assert c.post("/api/upload", files={"file": ("a.ipynb", _nb(code))}).status_code == 200
    assert c.post("/api/compile", json={"notebook_path": "a.ipynb"}).status_code == 200
    marker.unlink(missing_ok=True)
    r = c.post("/api/export-openapi", json={})
    assert r.status_code == 200, r.text
    pid, secret = marker.read_text().split(":", 1)
    assert int(pid) != os.getpid() and secret == "absent"


def test_failing_notebook_is_a_controlled_error(tmp_path):
    c = _client(CP)
    assert c.post("/api/upload", files={"file": ("a.ipynb", _nb("raise RuntimeError('boom')\ndef f(a: int) -> int:\n    return a\n"))}).status_code == 200
    r = c.post("/api/openapi-preview", json={"notebook_path": "a.ipynb"})
    assert r.status_code == 422 and "dashboard-only-secret" not in r.text


def _app(tmp_path, body):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "app.py").write_text(body)
    return pkg


def test_timeout_kills_and_reaps_the_child(tmp_path):
    pid_file = tmp_path / "pid"
    pkg = _app(tmp_path, f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    started = time.time()
    with pytest.raises(RuntimeError, match="did not finish"):
        isolated_openapi_schema(pkg, "pkg", timeout=1)
    assert time.time() - started < 15
    with pytest.raises(OSError):
        os.kill(int(pid_file.read_text()), 0)


def test_oversized_schema_is_refused(tmp_path):
    pkg = _app(tmp_path, "class app:\n    @staticmethod\n    def openapi():\n        return {'x': 'y' * 5000}\n")
    with pytest.raises(RuntimeError, match="larger than"):
        isolated_openapi_schema(pkg, "pkg", max_bytes=1000)
    assert isolated_openapi_schema(pkg, "pkg")["x"].startswith("yyy")
