"""A browser cannot send X-API-Key when loading the bundled frontend, so its
files load without the key while every API route still requires it."""
import pytest
from fastapi.testclient import TestClient

from backend import dashboard


@pytest.fixture
def client(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "k")
    routes = list(dashboard.app.router.routes)
    previous = getattr(dashboard.app.state, "frontend_dist", None)
    dashboard.mount_frontend_static_files(dashboard.app, dist)
    yield TestClient(dashboard.app)
    dashboard.app.router.routes[:] = routes
    dashboard.app.state.frontend_dist = previous


def test_frontend_files_load_without_the_key(client):
    assert client.get("/index.html").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.head("/assets/app.js").status_code == 200


def test_api_routes_still_need_the_key(client):
    assert client.get("/api/notebooks").status_code == 401
    assert client.get("/health").status_code == 401
    assert client.get("/api/notebooks", headers={"X-API-Key": "k"}).status_code == 200


def test_only_get_and_existing_files_are_public(client):
    assert client.post("/assets/app.js").status_code == 401
    assert client.get("/assets/missing.js").status_code == 401
    assert client.get("/assets/../../secret.txt").status_code == 401
    assert client.get("/%2e%2e/secret.txt").status_code == 401
