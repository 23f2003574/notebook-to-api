"""Unmatched API paths are a plain 404 whether or not the frontend is built (any Python version)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dashboard import mount_frontend_static_files


@pytest.fixture
def served(tmp_path):
    (tmp_path / "index.html").write_text("<html>shell</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.js").write_text("console.log(1)")
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    mount_frontend_static_files(app, tmp_path)
    return TestClient(app)


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"])
@pytest.mark.parametrize("path", [
    "/api/nope", "/api/notebooks/x.ipynb/versions//etc/passwd", "/api/notebooks/x/versions/%2Fetc%2Fpasswd",
    "/governance/nope", "/api", "/governance",
])
def test_unmatched_api_paths_are_404_for_every_method(served, method, path):
    resp = served.request(method, path)
    assert resp.status_code == 404, (method, path, resp.status_code)
    assert "shell" not in resp.text


def test_the_frontend_and_real_routes_still_work(served):
    assert served.get("/api/ping").json() == {"ok": True}
    assert "shell" in served.get("/").text
    assert served.get("/assets/a.js").status_code == 200
    assert served.post("/").status_code == 405  # the shell itself is still read-only


def test_an_absolute_version_id_is_rejected_with_the_frontend_built(tmp_path, monkeypatch):
    import backend.dashboard as dashboard

    (tmp_path / "index.html").write_text("<html>shell</html>")
    saved = getattr(dashboard.app.state, "frontend_dist", None)
    mount_frontend_static_files(dashboard.app, tmp_path)
    try:
        resp = TestClient(dashboard.app).delete("/api/notebooks/x.ipynb/versions/%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404) and "root:" not in resp.text
    finally:
        dashboard.app.router.routes[:] = [r for r in dashboard.app.router.routes if getattr(r, "name", "") != "frontend"]
        dashboard.app.state.frontend_dist = saved
