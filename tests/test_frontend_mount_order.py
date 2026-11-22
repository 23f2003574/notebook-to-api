"""Routers added after the frontend's catch-all mount must stay reachable."""
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend import dashboard


def _app_with_frontend(tmp_path):
    (tmp_path / "index.html").write_text("<html id='root'></html>")
    app = FastAPI()
    dashboard.mount_frontend_static_files(app, tmp_path)
    router = APIRouter()
    router.add_api_route("/v2/thing", lambda: {"ok": True}, methods=["GET", "POST"])
    app.include_router(router)
    return app


def test_late_router_is_shadowed_by_the_frontend_mount_until_it_is_moved(tmp_path):
    app = _app_with_frontend(tmp_path)
    assert TestClient(app).post("/v2/thing").status_code != 200  # the defect: the static mount answers first
    dashboard.keep_frontend_mount_last(app)
    client = TestClient(app)
    assert client.post("/v2/thing").json() == {"ok": True}
    assert client.get("/v2/thing").json() == {"ok": True}
    assert "root" in client.get("/index.html").text  # the frontend still serves its own files


def test_moving_is_idempotent_and_safe_without_a_frontend():
    app = FastAPI()
    dashboard.keep_frontend_mount_last(app)
    dashboard.keep_frontend_mount_last(app)
    assert [r.path for r in app.router.routes if getattr(r, "name", None) == "frontend"] == []


def test_backend_app_keeps_v2_routes_reachable_with_a_built_frontend(tmp_path, monkeypatch):
    import importlib
    import sys

    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setattr(dashboard, "FRONTEND_DIST_DIR", tmp_path)
    saved = {k: sys.modules.pop(k) for k in ("backend.app",) if k in sys.modules}
    try:
        dashboard.mount_frontend_static_files(dashboard.app, tmp_path)
        module = importlib.import_module("backend.app")
        client = TestClient(module.app)
        assert client.get("/v2/security/dashboard").status_code == 200
    finally:
        dashboard.app.router.routes[:] = [r for r in dashboard.app.router.routes if getattr(r, "name", None) != "frontend"]
        if hasattr(dashboard.app.state, "frontend_dist"):
            del dashboard.app.state.frontend_dist
        sys.modules.pop("backend.app", None)
        sys.modules.update(saved)
