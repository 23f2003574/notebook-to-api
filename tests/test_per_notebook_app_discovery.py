"""Per-notebook apps are visible in notebook status, inspection, health and a listing, and can be cleared."""
import json

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)


def _nb(fn, extra=""):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(f"def {fn}(x: int) -> int:\n    return x{extra}\n"))
    return nbformat.writes(nb).encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _up(name, fn, extra="", overwrite=False):
    r = client.post("/api/upload", params={"overwrite": str(overwrite).lower()},
                    files={"file": (name, _nb(fn, extra), "application/json")})
    assert r.status_code == 200, r.text


def _compile(name):
    assert client.post("/api/compile", json={"notebook_path": name, "per_notebook": True}).status_code == 200


def test_listing_reports_every_app_and_nothing_for_legacy_output(env):
    _up("a.ipynb", "fa")
    _up("b.ipynb", "fb")
    assert client.get("/api/apps").json() == {"status": "success", "apps": [], "total_count": 0}
    _compile("a.ipynb")
    _compile("b.ipynb")
    body = client.get("/api/apps").json()
    assert body["total_count"] == 2
    assert {a["notebook"] for a in body["apps"]} == {"a.ipynb", "b.ipynb"}
    assert all(a["status"] == "compiled" and a["notebook_exists"] and a["compiled_at"] for a in body["apps"])
    assert str(env) not in json.dumps(body)


def test_notebook_list_marks_which_notebooks_have_apps_and_which_changed(env):
    _up("a.ipynb", "fa")
    _up("b.ipynb", "fb")
    _compile("a.ipynb")
    entries = {n["filename"]: n for n in client.get("/api/notebooks").json()["notebooks"]}
    assert entries["b.ipynb"]["per_notebook_app"] is None
    assert entries["a.ipynb"]["per_notebook_app"]["status"] == "compiled"
    assert entries["a.ipynb"]["per_notebook_app"]["notebook_changed_since_compile"] is False
    _up("a.ipynb", "fa", extra=" + 1", overwrite=True)
    entries = {n["filename"]: n for n in client.get("/api/notebooks").json()["notebooks"]}
    assert entries["a.ipynb"]["per_notebook_app"]["notebook_changed_since_compile"] is True


def test_inspect_reports_the_notebooks_own_generated_files(env):
    _up("a.ipynb", "fa")
    _up("b.ipynb", "fb")
    _compile("a.ipynb")
    own = client.post("/api/inspect", json={"notebook_path": "a.ipynb"}).json()
    assert own["generated_files_status"] == "generated" and "app.py" in own["generated_files"]
    assert own["per_notebook_app"]["status"] == "compiled"
    other = client.post("/api/inspect", json={"notebook_path": "b.ipynb"}).json()
    assert other["generated_files_status"] == "none" and other["per_notebook_app"] is None


def test_health_counts_apps(env):
    _up("a.ipynb", "fa")
    assert client.get("/api/health").json()["per_notebook_apps_count"] == 0
    _compile("a.ipynb")
    assert client.get("/api/health").json()["per_notebook_apps_count"] == 1


def test_targeted_and_bulk_deletion(env):
    for n, f in (("a.ipynb", "fa"), ("b.ipynb", "fb"), ("c.ipynb", "fc")):
        _up(n, f)
        _compile(n)
    assert client.delete("/api/generated", params={"notebook": "a.ipynb"}).status_code == 200
    assert client.get("/api/apps").json()["total_count"] == 2
    dry = client.delete("/api/generated", params={"all_apps": "true", "dry_run": "true"}).json()
    assert len(dry["removed_apps"]) == 2 and client.get("/api/apps").json()["total_count"] == 2
    done = client.delete("/api/generated", params={"all_apps": "true"}).json()
    assert len(done["removed_apps"]) == 2 and client.get("/api/apps").json()["total_count"] == 0


def test_partial_and_corrupt_directories_are_reported_not_fatal(env):
    _up("a.ipynb", "fa")
    _compile("a.ipynb")
    (env / "apps" / "orphan").mkdir()
    app_dir = next(p for p in (env / "apps").iterdir() if p.name != "orphan")
    (app_dir / ".compile_metadata.json").write_text("{not json")
    body = client.get("/api/apps").json()
    assert body["total_count"] == 2 and {a["status"] for a in body["apps"]} == {"incomplete"}
    assert client.get("/api/notebooks").status_code == 200
    assert client.post("/api/inspect", json={"notebook_path": "a.ipynb"}).status_code == 200


def test_legacy_callers_are_unchanged(env):
    _up("a.ipynb", "fa")
    assert client.post("/api/compile", json={"notebook_path": "a.ipynb"}).status_code == 200
    assert client.post("/api/inspect", json={"notebook_path": "a.ipynb"}).json()["generated_files_status"] == "generated"
    assert client.delete("/api/generated").status_code == 200
