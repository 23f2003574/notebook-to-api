"""Soft delete: everything moves to the trash together, restores byte-identically, expires safely."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import dashboard, notebook_trash
from backend.observability.deployment_governance_audit import reset_audit_service
from backend.routes import upload

SRC = "def f(a: int) -> int:\n    return a + len(open('data.csv').read())\n"
NB = nbformat.writes(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(SRC)]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS", "NOTEBOOK_API_DASHBOARD_KEYS_FILE",
                 "NOTEBOOK_API_SOFT_DELETE", "NOTEBOOK_API_TRASH_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(upload, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(upload, "GENERATED_APPS_DIR", str(tmp_path / "apps"))
    (tmp_path / "up").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory")
    reset_audit_service()
    with TestClient(dashboard.app) as client:
        yield client, tmp_path
    reset_audit_service()


def _bundle(name="a.ipynb", data="a,b\n1,2\n"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, NB)
        z.writestr("data.csv", data)
    return buf.getvalue()


def _populate(client):
    assert client.post("/api/notebooks/import", files={"file": ("b.zip", _bundle())}).status_code == 200
    client.post("/api/upload?overwrite=true", files={"file": ("a.ipynb", (NB + " ").encode())})  # a version
    client.put("/api/notebooks/a.ipynb/tags", json={"tags": ["prod"]})
    client.put("/api/notebooks/a.ipynb/description", json={"description": "hello"})
    assert client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True}).status_code == 200


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and ".trash" not in p.parts}


def test_hard_delete_is_still_the_default(env):
    client, tmp = env
    _populate(client)
    body = client.delete("/api/notebooks/a.ipynb").json()
    assert "soft" not in body and not (tmp / "up" / ".trash").exists()


def test_soft_delete_moves_everything_and_restore_is_byte_identical(env):
    client, tmp = env
    _populate(client)
    before_up, before_apps = _snapshot(tmp / "up"), _snapshot(tmp / "apps")
    assert any(".versions" in k for k in before_up) and any(k.endswith("data.csv") for k in before_up) and before_apps
    r = client.delete("/api/notebooks/a.ipynb?soft=true")
    assert r.status_code == 200 and r.json()["soft"] is True
    assert client.get("/api/notebooks").json()["notebooks"] == [] and client.get("/api/notebooks").json()["trash_count"] == 1
    assert not any(k.startswith("a.ipynb") for k in _snapshot(tmp / "up"))
    assert not list((tmp / "apps").iterdir())
    items = client.get("/api/notebooks/trash").json()["items"]
    assert [i["filename"] for i in items] == ["a.ipynb"]
    restored = client.post(f"/api/notebooks/trash/{items[0]['trash_id']}/restore")
    assert restored.status_code == 200
    assert _snapshot(tmp / "up") == before_up and _snapshot(tmp / "apps") == before_apps
    assert client.get("/api/notebooks/trash").json()["items"] == []
    assert len(client.get("/api/notebooks/a.ipynb/versions").json()["versions"]) == 1


def test_env_default_makes_delete_soft_and_hard_can_be_forced(env, monkeypatch):
    client, tmp = env
    monkeypatch.setenv("NOTEBOOK_API_SOFT_DELETE", "true")
    client.post("/api/upload", files={"file": ("a.ipynb", NB.encode())})
    assert client.delete("/api/notebooks/a.ipynb").json()["soft"] is True
    client.post("/api/upload", files={"file": ("b.ipynb", NB.encode())})
    assert "soft" not in client.delete("/api/notebooks/b.ipynb?soft=false").json()


def test_restore_name_conflict_is_explicit_and_changes_nothing(env):
    client, tmp = env
    client.post("/api/upload", files={"file": ("a.ipynb", NB.encode())})
    client.delete("/api/notebooks/a.ipynb?soft=true")
    client.post("/api/upload", files={"file": ("a.ipynb", b'{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')})
    tid = client.get("/api/notebooks/trash").json()["items"][0]["trash_id"]
    r = client.post(f"/api/notebooks/trash/{tid}/restore")
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert len(client.get("/api/notebooks/trash").json()["items"]) == 1
    assert client.post("/api/notebooks/trash/nope/restore").status_code == 404


def test_expired_entries_are_purged_and_cannot_be_restored(env, monkeypatch):
    client, tmp = env
    monkeypatch.setenv("NOTEBOOK_API_TRASH_RETENTION_DAYS", "0")
    client.post("/api/upload", files={"file": ("a.ipynb", NB.encode())})
    tid = client.delete("/api/notebooks/a.ipynb?soft=true").json()["trash_id"]
    assert client.post(f"/api/notebooks/trash/{tid}/restore").status_code == 404
    assert client.get("/api/notebooks/trash").json()["items"] == []
    # Listing and a refused restore are read-only: the expired entry is still on disk ...
    assert len(list((tmp / "up" / ".trash").iterdir())) == 1
    assert client.get("/api/notebooks").json().get("trash_count", 0) == 0
    # ... until the separate, audited purge removes it.
    assert client.post("/api/notebooks/trash/purge").json()["purged"] == [tid]
    assert not any((tmp / "up" / ".trash").iterdir())


def test_purge_everything_and_audit_records(env):
    client, tmp = env
    client.post("/api/upload", files={"file": ("a.ipynb", NB.encode())})
    tid = client.delete("/api/notebooks/a.ipynb?soft=true").json()["trash_id"]
    client.post("/api/upload", files={"file": ("b.ipynb", NB.encode())})
    client.delete("/api/notebooks/b.ipynb?soft=true")
    client.post(f"/api/notebooks/trash/{tid}/restore")
    assert client.post("/api/notebooks/trash/purge?everything=true").json()["purged"]
    actions = [(r["action"], r["outcome"]) for r in client.get("/governance/audit/history?limit=50").json()["records"]]
    assert ("notebook.undelete", "success") in actions and ("notebook.trash_purge", "success") in actions
    assert actions.count(("notebook.delete", "success")) == 2


def test_failed_move_rolls_back_and_leaves_no_partial_entry(tmp_path, monkeypatch):
    roots = {"uploads": tmp_path / "up"}
    roots["uploads"].mkdir()
    (roots["uploads"] / "a").write_text("1")
    (roots["uploads"] / "b").write_text("2")
    real = notebook_trash._move
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real(src, dst)

    monkeypatch.setattr(notebook_trash, "_move", flaky)
    with pytest.raises(notebook_trash.TrashError):
        notebook_trash.soft_delete(tmp_path / "trash", roots, [("uploads", "a"), ("uploads", "b")], {"filename": "x"}, 1)
    assert (roots["uploads"] / "a").read_text() == "1" and (roots["uploads"] / "b").read_text() == "2"
    assert notebook_trash.list_entries(tmp_path / "trash") == []
    assert not [p for p in (tmp_path / "trash").iterdir()]


def test_failed_restore_moves_everything_back_to_the_trash(tmp_path, monkeypatch):
    roots = {"uploads": tmp_path / "up"}
    roots["uploads"].mkdir()
    (roots["uploads"] / "a").write_text("1")
    (roots["uploads"] / "b").write_text("2")
    rec = notebook_trash.soft_delete(tmp_path / "trash", roots, [("uploads", "a"), ("uploads", "b")], {"filename": "x"}, 1)
    real = notebook_trash._move
    state = {"n": 0}

    def flaky(src, dst):
        state["n"] += 1
        if state["n"] == 2:
            raise OSError("boom")
        real(src, dst)

    monkeypatch.setattr(notebook_trash, "_move", flaky)
    with pytest.raises(notebook_trash.TrashError):
        notebook_trash.restore(tmp_path / "trash", roots, rec["trash_id"])
    monkeypatch.setattr(notebook_trash, "_move", real)
    assert not any(roots["uploads"].iterdir())
    notebook_trash.restore(tmp_path / "trash", roots, rec["trash_id"])
    assert (roots["uploads"] / "a").read_text() == "1" and (roots["uploads"] / "b").read_text() == "2"


def test_staging_leftovers_are_ignored_and_expiry_math(tmp_path):
    trash = tmp_path / "trash"
    (trash / ".tmp-x" / "payload").mkdir(parents=True)
    assert notebook_trash.list_entries(trash) == []
    old = datetime.now(timezone.utc) - timedelta(days=40)
    roots = {"uploads": tmp_path / "up"}
    roots["uploads"].mkdir()
    (roots["uploads"] / "a").write_text("1")
    rec = notebook_trash.soft_delete(trash, roots, [("uploads", "a")], {"filename": "a"}, 30, now=old)
    assert json.loads((trash / rec["trash_id"] / "meta.json").read_text())["expires_at"] < datetime.now(timezone.utc).isoformat()
    assert notebook_trash.purge_expired(trash) == [rec["trash_id"]]


def test_concurrent_compile_and_soft_delete_leave_consistent_state(env):
    import threading

    client, tmp = env
    client.post("/api/upload", files={"file": ("a.ipynb", NB.encode())})
    results = []

    def compile_():
        results.append(client.post("/api/compile", json={"notebook_path": "a.ipynb", "per_notebook": True}).status_code)

    def delete():
        results.append(client.delete("/api/notebooks/a.ipynb?soft=true").status_code)

    threads = [threading.Thread(target=compile_) for _ in range(3)] + [threading.Thread(target=delete)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(results) <= {200, 404}
    assert not (tmp / "up" / "a.ipynb").exists()
    assert len(client.get("/api/notebooks/trash").json()["items"]) == 1
    # the app of a compile that lost the race must not outlive the notebook's move to the trash
    tid = client.get("/api/notebooks/trash").json()["items"][0]["trash_id"]
    assert client.post(f"/api/notebooks/trash/{tid}/restore").status_code == 200
    assert (tmp / "up" / "a.ipynb").exists()


def test_batch_delete_can_soft_delete_and_each_entry_restores(env):
    client, tmp = env
    for name in ("a.ipynb", "b.ipynb"):
        client.post("/api/upload", files={"file": (name, NB.encode())})
    r = client.post("/api/notebooks/delete-batch", json={"filenames": ["a.ipynb", "b.ipynb", "nope.ipynb"], "soft": True}).json()
    assert r["succeeded_count"] == 2 and r["failed_count"] == 1
    assert all(e.get("soft") and e.get("trash_id") for e in r["results"] if e["status"] == "success")
    assert sorted(i["filename"] for i in client.get("/api/notebooks/trash").json()["items"]) == ["a.ipynb", "b.ipynb"]
    for item in client.get("/api/notebooks/trash").json()["items"]:
        assert client.post(f"/api/notebooks/trash/{item['trash_id']}/restore").status_code == 200
    records = client.get("/governance/audit/history?limit=50").json()["records"]
    assert any(x["action"] == "notebook.delete" and x["metadata"].get("trash_id") for x in records)


def test_batch_delete_is_hard_by_default_dry_run_moves_nothing_and_validates(env):
    client, tmp = env
    client.post("/api/upload", files={"file": ("a.ipynb", NB.encode())})
    dry = client.post("/api/notebooks/delete-batch", json={"filenames": ["a.ipynb"], "soft": True, "dry_run": True}).json()
    assert dry["succeeded_count"] == 1 and (tmp / "up" / "a.ipynb").exists() and not (tmp / "up" / ".trash").exists()
    assert client.post("/api/notebooks/delete-batch", json={"filenames": ["a.ipynb"], "soft": "yes"}).status_code == 400
    client.post("/api/notebooks/delete-batch", json={"filenames": ["a.ipynb"]})
    assert not (tmp / "up" / "a.ipynb").exists() and not (tmp / "up" / ".trash").exists()
