"""POST /api/inspect reports generated files only when they came from that notebook."""
import shutil

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)


def _nb(name_fn):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(f"def {name_fn}(x: int) -> int:\n    return x + 1\n"))
    return nbformat.writes(nb).encode("utf-8")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_routes, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(upload_routes, "GENERATED_DIR", str(tmp_path / "generated"))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _upload(name, fn):
    r = client.post("/api/upload", files={"file": (name, _nb(fn), "application/json")})
    assert r.status_code == 200, r.text
    return r.json()["filename"]


def _inspect(name):
    r = client.post("/api/inspect", json={"notebook_path": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_files_of_another_notebook_are_not_reported_as_this_notebooks(dirs):
    a, b = _upload("a.ipynb", "add_a"), _upload("b.ipynb", "add_b")
    assert client.post("/api/compile", json={"notebook_path": a}).status_code == 200

    own = _inspect(a)
    assert own["generated_files_status"] == "generated" and "app.py" in own["generated_files"]

    other = _inspect(b)
    assert other["generated_files"] == [] and other["generated_files_status"] == "other_notebook"

    assert client.post("/api/compile", json={"notebook_path": b}).status_code == 200
    assert _inspect(b)["generated_files_status"] == "generated"
    assert _inspect(a)["generated_files_status"] == "other_notebook"


def test_nothing_compiled_yet_and_a_deleted_output_directory_are_reported_as_none(dirs):
    a = _upload("a.ipynb", "add_a")
    assert _inspect(a)["generated_files"] == [] and _inspect(a)["generated_files_status"] == "none"
    client.post("/api/compile", json={"notebook_path": a})
    shutil.rmtree(dirs / "generated")
    result = _inspect(a)  # missing directory: no crash
    assert result["generated_files"] == [] and result["generated_files_status"] == "none"


def test_nested_files_are_listed_relative_and_nothing_escapes_the_output_directory(dirs):
    a = _upload("a.ipynb", "add_a")
    client.post("/api/compile", json={"notebook_path": a})
    nested = dirs / "generated" / "extra" / "deep"
    nested.mkdir(parents=True)
    (nested / "note.txt").write_text("x")
    (dirs / "outside.txt").write_text("secret")
    (dirs / "generated" / "link.txt").symlink_to(dirs / "outside.txt")
    files = _inspect(a)["generated_files"]
    assert "extra/deep/note.txt" in files
    assert all(not f.startswith(("/", "..")) and ".." not in f.split("/") for f in files)
    assert str(dirs) not in " ".join(files)
    assert client.post("/api/inspect", json={"notebook_path": "../outside.txt"}).status_code in (400, 404)
