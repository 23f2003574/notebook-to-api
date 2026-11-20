"""Compile, delete and rename of one notebook exclude each other; different notebooks stay concurrent."""
import os
import threading
import time

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)


def _nb(fn="f", src=None):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(src or f"def {fn}(x: int) -> int:\n    return x\n"))
    return nbformat.writes(nb).encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _upload(name, fn="f", src=None):
    assert client.post("/api/upload", params={"overwrite": "true"}, files={"file": (name, _nb(fn, src), "application/json")}).status_code == 200


def _apps(env):
    root = env / "apps"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def test_compile_racing_delete_never_errors_or_leaves_an_orphan(env):
    for _ in range(12):
        _upload("r.ipynb")
        out = {}
        t1 = threading.Thread(target=lambda: out.update(c=client.post("/api/compile", json={"notebook_path": "r.ipynb", "per_notebook": True}).status_code))
        t2 = threading.Thread(target=lambda: out.update(d=client.delete("/api/notebooks/r.ipynb").status_code))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert out["d"] == 200 and out["c"] in (200, 404), out
        assert _apps(env) == [], "an app survived its notebook"


def test_compile_after_delete_is_a_clean_404_and_publishes_nothing(env):
    _upload("a.ipynb")
    client.delete("/api/notebooks/a.ipynb")
    assert client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True}).status_code == 404
    assert _apps(env) == []


def test_compile_racing_rename_ends_with_one_consistent_state(env):
    for _ in range(8):
        _upload("a.ipynb")
        for stale in env.joinpath("uploads").glob("z.ipynb"):
            stale.unlink()
        out = {}
        t1 = threading.Thread(target=lambda: out.update(c=client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True}).status_code))
        t2 = threading.Thread(target=lambda: out.update(r=client.patch("/api/notebooks/a.ipynb", json={"new_filename": "z.ipynb", "overwrite": True}).status_code))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert out["r"] == 200 and out["c"] in (200, 404), out
        assert _apps(env) == [], "the old name's app must not outlive the rename"
        client.delete("/api/notebooks/z.ipynb")


def test_a_failed_compile_leaves_no_empty_or_misleading_app(env):
    _upload("bad.ipynb", src="def metrics(x: int) -> int:\n    return x\n")
    assert client.post("/api/compile", json={"notebook_path": "bad.ipynb", "per_notebook": True}).status_code == 400
    assert _apps(env) == [] and client.get("/api/apps").json()["total_count"] == 0


def test_a_failed_recompile_keeps_the_previous_good_app(env):
    _upload("g.ipynb")
    assert client.post("/api/compile", json={"notebook_path": "g.ipynb", "per_notebook": True}).status_code == 200
    _upload("g.ipynb", src="def metrics(x: int) -> int:\n    return x\n")
    assert client.post("/api/compile", json={"notebook_path": "g.ipynb", "per_notebook": True}).status_code == 400
    assert len(_apps(env)) == 1
    assert "def f(" in client.get("/api/generated/runtime/notebook_module.py", params={"notebook": "g.ipynb"}).text


def test_different_notebooks_are_not_serialized_by_the_lifecycle_lock(env):
    inside = threading.Event()
    release = threading.Event()
    done = {}

    def hold():
        with upload_routes._notebook_lifecycle("one.ipynb"):
            inside.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    assert inside.wait(5)
    started = time.monotonic()
    with upload_routes._notebook_lifecycle("two.ipynb"):
        done["waited"] = time.monotonic() - started
    release.set()
    holder.join()
    assert done["waited"] < 0.5  # an unrelated notebook did not wait for "one"


def test_opposite_renames_cannot_deadlock(env):
    results = []

    def take(first, second):
        with upload_routes._notebook_lifecycle(first, second):
            time.sleep(0.05)
            results.append((first, second))

    threads = [threading.Thread(target=take, args=pair) for pair in [("a.ipynb", "b.ipynb"), ("b.ipynb", "a.ipynb")] * 5]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads) and len(results) == 10


def test_the_lock_is_reentrant_and_released_after_an_error(env):
    with pytest.raises(RuntimeError):
        with upload_routes._notebook_lifecycle("x.ipynb"):
            with upload_routes._notebook_lifecycle("x.ipynb"):
                raise RuntimeError("boom")
    acquired = threading.Event()

    def other():
        with upload_routes._notebook_lifecycle("x.ipynb"):
            acquired.set()

    t = threading.Thread(target=other)
    t.start()
    t.join(5)
    assert acquired.is_set()


def test_normal_and_version_pinned_compiles_still_work(env):
    _upload("v.ipynb")
    _upload("v.ipynb", fn="g")
    version = client.get("/api/notebooks/v.ipynb/versions").json()["versions"][0]["version_id"]
    assert client.post("/api/compile", json={"notebook_path": "v.ipynb"}).status_code == 200
    assert client.post("/api/compile", json={"notebook_path": "v.ipynb", "per_notebook": True, "version_id": version}).status_code == 200
    assert client.post("/api/compile", json={}).status_code == 400
    assert os.path.isdir(env / "apps")
