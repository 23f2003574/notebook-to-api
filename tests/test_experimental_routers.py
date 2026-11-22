"""Experimental routers are opt-in; plugin endpoints cannot read server files or import arbitrary modules."""
import importlib
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.plugins import plugin_security as ps
from backend.plugins.plugin_loader import router as loader_router
from backend.plugins.plugin_packaging import router as packaging_router

KEY = "k" * 20


def _dashboard(monkeypatch, tmp_path, experimental):
    for name, sub in (("UPLOAD_DIR", "up"), ("GENERATED_DIR", "gen"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setenv(f"NOTEBOOK_API_{name}", str(tmp_path / sub))
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", KEY)
    if experimental:
        monkeypatch.setenv("NOTEBOOK2API_EXPERIMENTAL_ROUTERS", "1")
    else:
        monkeypatch.delenv("NOTEBOOK2API_EXPERIMENTAL_ROUTERS", raising=False)
    sys.modules.pop("backend.dashboard", None)
    return importlib.import_module("backend.dashboard")


@pytest.fixture(autouse=True)
def _restore_dashboard():
    saved = sys.modules.get("backend.dashboard")
    yield
    if saved is not None:
        sys.modules["backend.dashboard"] = saved


def test_experimental_routers_absent_by_default(monkeypatch, tmp_path):
    mod = _dashboard(monkeypatch, tmp_path, False)
    c = TestClient(mod.app)
    h = {"X-API-Key": KEY}
    for path in ("/plugins", "/cluster/workers", "/pipelines", "/security/roles"):
        assert c.get(path, headers=h).status_code in (404, 405) and c.get(path, headers=h).status_code != 200, path
    assert c.get("/plugins", headers=h).status_code == 404
    assert c.get("/api/health", headers=h).status_code == 200
    assert c.get("/governance/audit/verify", headers=h).status_code == 200


def test_opt_in_restores_experimental_routers(monkeypatch, tmp_path):
    mod = _dashboard(monkeypatch, tmp_path, True)
    c = TestClient(mod.app)
    assert c.get("/plugins", headers={"X-API-Key": KEY}).status_code == 200
    assert c.get("/cluster/workers", headers={"X-API-Key": KEY}).status_code == 200


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(loader_router)
    app.include_router(packaging_router)
    return TestClient(app)


def _manifest(entry="this", **extra):
    return {"name": "x", "version": "1.0.0", "entry_point": entry, "description": "d", "author": "a", **extra}


def test_unpermitted_import_rejected(client, monkeypatch):
    monkeypatch.setenv("NOTEBOOK2API_PLUGIN_MODULE_PREFIXES", "allowed_pkg")
    r = client.post("/plugins/load", json=_manifest("this"))
    assert r.status_code == 403 and "this" not in sys.modules
    assert client.post("/plugins/load", json=_manifest("allowed_pkg_evil")).status_code == 403


def test_imports_denied_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("NOTEBOOK2API_PLUGIN_MODULE_PREFIXES", raising=False)
    assert client.post("/plugins/load", json=_manifest("os")).status_code == 403


def test_package_denied_without_roots(client, monkeypatch, tmp_path):
    monkeypatch.delenv("NOTEBOOK2API_PLUGIN_ROOTS", raising=False)
    (tmp_path / "secret.txt").write_text("s")
    assert client.post("/plugins/package", json=_manifest("x", source_dir=str(tmp_path))).status_code == 403


def test_package_confined_to_roots(client, monkeypatch, tmp_path):
    root = tmp_path / "root"
    (root / "plug").mkdir(parents=True)
    (root / "plug" / "a.py").write_text("x=1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    monkeypatch.setenv("NOTEBOOK2API_PLUGIN_ROOTS", str(root))
    assert client.post("/plugins/package", json=_manifest("x", source_dir=str(root / "plug"))).status_code == 201
    assert client.post("/plugins/package", json=_manifest("x", source_dir=str(outside))).status_code == 403
    assert client.post("/plugins/package", json=_manifest("x", source_dir=str(root / ".." / "outside"))).status_code == 403
    (root / "link").symlink_to(outside)
    assert client.post("/plugins/package", json=_manifest("x", source_dir=str(root / "link"))).status_code == 403
    sibling = tmp_path / "root2"
    sibling.mkdir()
    assert client.post("/plugins/package", json=_manifest("x", source_dir=str(sibling))).status_code == 403


def test_helpers_raise_http_exceptions():
    with pytest.raises(HTTPException):
        ps.check_entry_point("os", {})
    with pytest.raises(HTTPException):
        ps.resolve_source_dir("/", {})
