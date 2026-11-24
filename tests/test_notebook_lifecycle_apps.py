"""Per-notebook compiled apps follow their notebook through delete, rename and copy."""
import os
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)


def _nb(fn):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(f"def {fn}(x: int) -> int:\n    return x\n"))
    return nbformat.writes(nb).encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _make(*names):
    for name in names:
        assert client.post("/api/upload", files={"file": (name, _nb("f_" + name[0]), "application/json")}).status_code == 200
        assert client.post("/api/compile", json={"notebook_path": name, "per_notebook": True}).status_code == 200


def _apps(env):
    root = env / "apps"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _id(name):
    return upload_routes._notebook_app_id(name)


def test_delete_removes_only_that_notebooks_app(env):
    _make("a.ipynb", "b.ipynb")
    assert client.delete("/api/notebooks/a.ipynb").status_code == 200
    assert _apps(env) == [_id("b.ipynb")]
    assert "f_b" in client.get("/api/generated/app.py", params={"notebook": "b.ipynb"}).text


def test_dry_run_delete_keeps_the_app(env):
    _make("a.ipynb")
    assert client.delete("/api/notebooks/a.ipynb", params={"dry_run": "true"}).status_code == 200
    assert _apps(env) == [_id("a.ipynb")]


def test_batch_delete_removes_each_app(env):
    _make("a.ipynb", "b.ipynb", "c.ipynb")
    r = client.post("/api/notebooks/delete-batch", json={"filenames": ["a.ipynb", "b.ipynb"]})
    assert r.status_code == 200, r.text
    assert _apps(env) == [_id("c.ipynb")]


def test_rename_removes_the_stale_app_and_reports_it(env):
    _make("a.ipynb", "b.ipynb")
    r = client.patch("/api/notebooks/a.ipynb", json={"new_filename": "z.ipynb"})
    assert r.status_code == 200, r.text
    assert r.json()["generated_app_removed"] is True
    assert _apps(env) == [_id("b.ipynb")]
    # the renamed notebook is recompiled into an app under its own new identity
    assert client.post("/api/compile", json={"notebook_path": "z.ipynb", "per_notebook": True}).status_code == 200
    assert _apps(env) == sorted([_id("b.ipynb"), _id("z.ipynb")])


def test_rename_of_a_notebook_without_an_app_reports_false(env):
    assert client.post("/api/upload", files={"file": ("n.ipynb", _nb("f_n"), "application/json")}).status_code == 200
    r = client.patch("/api/notebooks/n.ipynb", json={"new_filename": "m.ipynb"})
    assert r.status_code == 200 and r.json()["generated_app_removed"] is False


def test_copy_does_not_copy_or_disturb_apps(env):
    _make("a.ipynb")
    r = client.post("/api/notebooks/a.ipynb/copy", json={"new_filename": "copy.ipynb"})
    assert r.status_code == 200, r.text
    assert _apps(env) == [_id("a.ipynb")]


def test_helper_is_safe_for_missing_dirs_and_unrelated_names(env, tmp_path):
    assert upload_routes._remove_notebook_app("never.ipynb") is False
    (tmp_path / "apps").mkdir(exist_ok=True)
    (tmp_path / "apps" / "keep").mkdir()
    assert upload_routes._remove_notebook_app("../apps/keep") is False or (tmp_path / "apps" / "keep").exists()
    assert (tmp_path / "apps" / "keep").exists()


def test_a_removal_failure_does_not_break_the_delete(env, monkeypatch):
    _make("a.ipynb")
    monkeypatch.setattr(upload_routes.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("busy"))
                        if "apps" in str(a[0]) else None)
    r = client.delete("/api/notebooks/a.ipynb?permanent=true")
    assert r.status_code == 200
    assert not (env / "uploads" / "a.ipynb").exists()
    assert Path(env / "apps" / _id("a.ipynb")).exists()  # left behind, logged, still clearable
    assert os.path.isdir(env / "apps")
