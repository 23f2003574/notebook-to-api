"""Dashboard compile/deploy honour resolve_versions and require_pinned, per request."""
import importlib
import sys
import threading

import nbformat
import pytest
from fastapi.testclient import TestClient

KEY = "k" * 20
SRC = "import tabulate\ndef f(a: int) -> int:\n    return a\n"


@pytest.fixture
def client(monkeypatch, tmp_path):
    for name, sub in (("UPLOAD_DIR", "up"), ("GENERATED_DIR", "gen"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setenv(f"NOTEBOOK_API_{name}", str(tmp_path / sub))
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY)
    saved = sys.modules.get("backend.dashboard")
    sys.modules.pop("backend.dashboard", None)
    mod = importlib.import_module("backend.dashboard")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(SRC))
    with TestClient(mod.app) as c:
        c.headers.update({"X-API-Key": KEY})
        c.post("/api/upload", files={"file": ("p.ipynb", nbformat.writes(nb).encode())})
        yield c
    if saved is not None:
        sys.modules["backend.dashboard"] = saved


def _compile(client, **extra):
    return client.post("/api/compile", json={"notebook_path": "p.ipynb", "per_notebook": True, **extra})


def test_default_compile_reports_unpinned_without_failing(client):
    r = _compile(client)
    assert r.status_code == 200
    assert r.json()["dependency_pins"]["unpinned"] == ["tabulate"]
    assert "resolution" not in r.json()["dependency_pins"]


def test_resolve_versions_pins(client, monkeypatch):
    monkeypatch.setattr("backend.compiler.pip_dry_run_resolver", lambda reqs: {"tabulate": "0.9.0"})
    r = _compile(client, resolve_versions=True, require_pinned=True)
    assert r.status_code == 200
    pins = r.json()["dependency_pins"]
    assert "tabulate==0.9.0" in pins["pinned"] and pins["unpinned"] == []
    assert pins["resolution"]["status"] == "resolved"


def test_require_pinned_rejects_unpinned(client):
    r = _compile(client, require_pinned=True)
    assert r.status_code == 422 and "tabulate" in r.json()["detail"]


def test_offline_resolution_failure_is_diagnosed(client, monkeypatch):
    def offline(reqs):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr("backend.compiler.pip_dry_run_resolver", offline)
    ok = _compile(client, resolve_versions=True)
    assert ok.status_code == 200 and ok.json()["dependency_pins"]["resolution"]["status"] == "unresolved"
    bad = _compile(client, resolve_versions=True, require_pinned=True)
    assert bad.status_code == 422 and "network unreachable" in bad.json()["detail"]


def test_non_boolean_flags_rejected(client):
    assert _compile(client, resolve_versions="yes").status_code == 400


def test_resolution_state_does_not_leak_between_requests(client, monkeypatch):
    monkeypatch.setattr("backend.compiler.pip_dry_run_resolver", lambda reqs: {"tabulate": "0.9.0"})
    assert _compile(client, resolve_versions=True).status_code == 200
    plain = _compile(client)
    assert plain.json()["dependency_pins"]["unpinned"] == ["tabulate"]

    results = []

    def run(flag):
        results.append((flag, _compile(client, resolve_versions=flag).json()["dependency_pins"]["unpinned"]))

    threads = [threading.Thread(target=run, args=(i % 2 == 0,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    # compiles are serialized per notebook, but each request still gets only its own setting
    assert all((not unpinned) == flag for flag, unpinned in results), results


def test_deploy_require_pinned_refuses_unpinned_app(client):
    assert _compile(client).status_code == 200
    r = client.post("/api/deploy", json={"notebook": "p.ipynb", "require_pinned": True, "dry_run": True})
    assert r.status_code == 422 and "Nothing was built" in r.json()["detail"]
