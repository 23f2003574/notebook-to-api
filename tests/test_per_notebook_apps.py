"""Compiling with per_notebook keeps one compiled app per uploaded notebook."""
import json
import threading
import zipfile
import io

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)


def _nb(fn):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(f"def {fn}(x: int) -> int:\n    return x + 1\n"))
    return nbformat.writes(nb).encode("utf-8")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_routes, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(upload_routes, "GENERATED_DIR", str(tmp_path / "generated"))
    monkeypatch.setattr(upload_routes, "GENERATED_APPS_DIR", str(tmp_path / "generated_apps"))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _up(name, fn):
    assert client.post("/api/upload", files={"file": (name, _nb(fn), "application/json")}).status_code == 200
    return name


def _compile(name, **extra):
    r = client.post("/api/compile", json={"notebook_path": name, "per_notebook": True, **extra})
    assert r.status_code == 200, r.text
    return r


def test_two_notebooks_keep_independent_apps_and_metadata(dirs):
    a, b = _up("one.ipynb", "f_one"), _up("two.ipynb", "f_two")
    _compile(a)
    _compile(b)
    roots = sorted((dirs / "generated_apps").iterdir())
    assert len(roots) == 2
    texts = {r.name: (r / "app.py").read_text() for r in roots}
    assert sum("f_one" in t for t in texts.values()) == 1
    assert sum("f_two" in t for t in texts.values()) == 1
    metas = [json.loads((r / ".compile_metadata.json").read_text()) for r in roots]
    assert len({m["source_notebook_path"] if "source_notebook_path" in m else json.dumps(m, sort_keys=True) for m in metas}) == 2
    assert not (dirs / "generated" / "app.py").exists()


def test_generated_routes_target_the_requested_notebook(dirs):
    a, b = _up("one.ipynb", "f_one"), _up("two.ipynb", "f_two")
    _compile(a)
    _compile(b)
    assert "f_one" in client.get("/api/generated/app.py", params={"notebook": a}).text
    assert "f_two" in client.get("/api/generated/app.py", params={"notebook": b}).text
    listing = client.get("/api/generated", params={"notebook": a}).json()
    assert "app.py" in json.dumps(listing)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/download", params={"notebook": b}).content))
    assert "f_two" in z.read("app.py").decode() and "f_one" not in z.read("app.py").decode()
    r = client.post("/api/export-openapi", json={"notebook": a})
    assert r.status_code == 200 and "/f_one" in json.dumps(r.json()) and "/f_two" not in json.dumps(r.json())
    assert client.delete("/api/generated", params={"notebook": a}).status_code == 200
    assert "f_two" in client.get("/api/generated/app.py", params={"notebook": b}).text


def test_default_behaviour_is_unchanged(dirs):
    a, b = _up("one.ipynb", "f_one"), _up("two.ipynb", "f_two")
    assert client.post("/api/compile", json={"notebook_path": a}).status_code == 200
    assert client.post("/api/compile", json={"notebook_path": b}).status_code == 200
    text = (dirs / "generated" / "app.py").read_text()
    assert "f_two" in text and "f_one" not in text
    assert not (dirs / "generated_apps").exists()


@pytest.mark.parametrize("bad", ["../x.ipynb", "a/b.ipynb", "..", "x.txt", "a\\b.ipynb"])
def test_notebook_identifier_is_never_used_as_a_path(dirs, bad):
    assert client.get("/api/generated", params={"notebook": bad}).status_code == 400
    assert client.post("/api/export-openapi", json={"notebook": bad}).status_code == 400


def test_similar_names_do_not_collide():
    ids = {upload_routes._notebook_app_id(n) for n in ("a-b.ipynb", "a_b.ipynb", "A B.ipynb", "1x.ipynb")}
    assert len(ids) == 4 and all(i.isidentifier() for i in ids)


def test_concurrent_compiles_do_not_overwrite_each_other(dirs):
    names = [_up(f"n{i}.ipynb", f"fn_{i}") for i in range(4)]
    codes = []
    ts = [threading.Thread(target=lambda n=n: codes.append(client.post(
        "/api/compile", json={"notebook_path": n, "per_notebook": True}).status_code)) for n in names]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes == [200] * 4
    for i, n in enumerate(names):
        assert f"fn_{i}" in client.get("/api/generated/app.py", params={"notebook": n}).text


def test_per_notebook_compile_smoke_test_imports_that_notebooks_app(dirs):
    a = _up("one.ipynb", "f_one")
    body = _compile(a, smoke_test=True).json()
    assert body["smoke_test"]["passed"] is True
