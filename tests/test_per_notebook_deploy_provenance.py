"""Deploying a notebook's own app records that notebook, checks its own staleness, and tags it sensibly."""
import json
import os
import stat

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
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text("#!/bin/sh\necho ok\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _upload(name, fn, extra=""):
    assert client.post("/api/upload", params={"overwrite": "true"}, files={"file": (name, _nb(fn, extra), "application/json")}).status_code == 200


def _history():
    return client.get("/api/deploy/history").json()["entries"]


def test_deploy_records_the_deployed_notebook_not_the_shared_directory_one(env):
    _upload("a.ipynb", "fa")
    _upload("b.ipynb", "fb")
    assert client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True}).status_code == 200
    assert client.post("/api/compile", json={"notebook_path": "b.ipynb"}).status_code == 200  # shared dir holds b
    r = client.post("/api/deploy", json={"notebook": "a.ipynb"})
    assert r.status_code == 200, r.text
    entry = _history()[0]
    assert entry["source_notebook_filename"] == "a.ipynb"
    assert r.json()["tag"] == "a:latest"


def test_staleness_is_judged_against_the_deployed_notebooks_own_compile(env):
    _upload("a.ipynb", "fa")
    _upload("b.ipynb", "fb")
    client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True})
    client.post("/api/compile", json={"notebook_path": "b.ipynb"})
    _upload("a.ipynb", "fa", extra=" + 1")  # a changes after its compile
    stale = client.post("/api/deploy", json={"notebook": "a.ipynb"})
    assert stale.status_code == 409 or "stale" in stale.text.lower() or "changed" in stale.text.lower(), stale.text
    assert client.post("/api/deploy", json={"notebook": "a.ipynb", "force": True}).status_code == 200
    # b's shared-directory compile is still current and unaffected by a's edit
    assert client.post("/api/deploy", json={}).status_code == 200
    assert _history()[0]["source_notebook_filename"] == "b.ipynb"


@pytest.mark.parametrize("name,tag", [("my_app.ipynb", "my_app:latest"), ("My App.ipynb", None), ("a b.ipynb", None)])
def test_default_tags_are_deterministic_and_collision_free(name, tag):
    first = upload_routes._per_notebook_image_tag(name)
    assert first == upload_routes._per_notebook_image_tag(name)
    if tag:
        assert first == tag
    assert first == first.lower() and " " not in first
    assert upload_routes._per_notebook_image_tag("a b.ipynb") != upload_routes._per_notebook_image_tag("a-b.ipynb")


def test_legacy_shared_directory_deploy_keeps_its_default_tag(env):
    _upload("b.ipynb", "fb")
    client.post("/api/compile", json={"notebook_path": "b.ipynb"})
    r = client.post("/api/deploy", json={})
    assert r.status_code == 200 and r.json()["tag"] == "generated:latest"
    assert json.dumps(_history()[0]).count("b.ipynb") >= 1
