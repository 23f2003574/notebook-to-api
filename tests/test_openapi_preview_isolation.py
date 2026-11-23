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


def test_builds_are_limited_to_a_few_at_a_time_and_overflow_is_a_503(tmp_path, monkeypatch):
    import threading

    from backend.exporters import openapi_exporter as exporter

    monkeypatch.setenv("NOTEBOOK_API_OPENAPI_MAX_CONCURRENT", "1")
    pkg = _app(tmp_path, "import time\ntime.sleep(3)\nclass app:\n    @staticmethod\n    def openapi():\n        return {}\n")
    first = threading.Thread(target=lambda: exporter.isolated_openapi_schema(pkg, "pkg", timeout=20))
    first.start()
    time.sleep(1)
    with pytest.raises(exporter.OpenAPIBuildBusy):
        exporter.isolated_openapi_schema(pkg, "pkg", timeout=0.5)
    first.join()


def test_a_busy_dashboard_answers_503_with_retry_after(tmp_path, monkeypatch):
    from backend.exporters import openapi_exporter as exporter

    def busy(*args, **kwargs):
        raise exporter.OpenAPIBuildBusy("2 OpenAPI builds are already running; try again shortly")

    monkeypatch.setattr(exporter, "isolated_openapi_schema", busy)
    c = _client(CP)
    assert c.post("/api/upload", files={"file": ("a.ipynb", _nb("def f(a: int) -> int:\n    return a\n"))}).status_code == 200
    r = c.post("/api/openapi-preview", json={"notebook_path": "a.ipynb"})
    assert r.status_code == 503 and r.headers["retry-after"] == "5"


def test_child_gets_cpu_and_memory_limits(tmp_path, monkeypatch):
    from backend.exporters.openapi_exporter import isolated_openapi_schema

    monkeypatch.setenv("NOTEBOOK_API_OPENAPI_CPU_LIMIT_SECONDS", "2")
    pkg = _app(tmp_path, "while True:\n    pass\n")
    started = time.time()
    with pytest.raises(RuntimeError):
        isolated_openapi_schema(pkg, "pkg", timeout=30)
    assert time.time() - started < 15  # the CPU limit ended it long before the 30 s timeout
    monkeypatch.setenv("NOTEBOOK_API_OPENAPI_CPU_LIMIT_SECONDS", "60")
    monkeypatch.setenv("NOTEBOOK_API_OPENAPI_MEMORY_LIMIT_MB", "300")
    (tmp_path / "m").mkdir()
    hog = _app(tmp_path / "m", "x = bytearray(1024 * 1024 * 1024)\n")
    with pytest.raises(RuntimeError, match="failed to import"):
        isolated_openapi_schema(hog, "pkg", timeout=30)
