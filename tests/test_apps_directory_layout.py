"""The per-notebook apps directory can never overlap uploads or the shared output, and clearing apps removes only apps."""
import importlib
import os
import sys

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app
from backend.routes.upload import validate_storage_layout

client = TestClient(app)


@pytest.mark.parametrize("apps", [
    "uploads", "uploads/apps", "generated", "generated/apps", ".", "uploads/nested/../../uploads",
])
def test_overlapping_layouts_are_rejected(tmp_path, apps):
    with pytest.raises(ValueError, match="GENERATED_APPS_DIR"):
        validate_storage_layout(tmp_path / "uploads", tmp_path / "generated", tmp_path / apps)


@pytest.mark.parametrize("apps", ["generated_apps", "apps", "elsewhere/apps"])
def test_separate_directories_are_accepted(tmp_path, apps):
    validate_storage_layout(tmp_path / "uploads", tmp_path / "generated", tmp_path / apps)


def test_default_configuration_is_valid():
    validate_storage_layout(upload_routes.UPLOAD_DIR, upload_routes.GENERATED_DIR, upload_routes.GENERATED_APPS_DIR)


def test_importing_with_an_overlapping_environment_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("NOTEBOOK_API_GENERATED_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("NOTEBOOK_API_GENERATED_APPS_DIR", str(tmp_path / "uploads"))
    saved = sys.modules.pop("backend.routes.upload")
    try:
        with pytest.raises(ValueError, match="GENERATED_APPS_DIR"):
            importlib.import_module("backend.routes.upload")
    finally:
        sys.modules["backend.routes.upload"] = saved


def _nb():
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(x: int) -> int:\n    return x\n"))
    return nbformat.writes(nb).encode()


def test_clearing_all_apps_removes_only_app_directories(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    for n in ("a.ipynb", "b.ipynb"):
        client.post("/api/upload", files={"file": (n, _nb(), "application/json")})
        assert client.post("/api/compile", json={"notebook_path": n, "per_notebook": True}).status_code == 200
    stray = tmp_path / "apps" / "not-an-app.txt"
    stray.write_text("keep me")
    r = client.delete("/api/generated", params={"all_apps": "true"})
    assert r.status_code == 200 and len(r.json()["removed_apps"]) == 2
    assert stray.read_text() == "keep me"  # the root is not removed wholesale
    assert sorted(p.name for p in (tmp_path / "apps").iterdir()) == ["not-an-app.txt"]
    assert (tmp_path / "uploads" / "a.ipynb").is_file() and (tmp_path / "uploads" / "b.ipynb").is_file()


def test_an_empty_apps_root_is_tidied_up_and_a_missing_one_is_fine(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    assert client.delete("/api/generated", params={"all_apps": "true"}).json()["removed_apps"] == []
    client.post("/api/upload", files={"file": ("a.ipynb", _nb(), "application/json")})
    client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True})
    client.delete("/api/generated", params={"all_apps": "true"})
    assert not (tmp_path / "apps").exists()
