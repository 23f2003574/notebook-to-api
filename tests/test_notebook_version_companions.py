"""Versions carry the data files that went with the notebook, and restore brings them back."""
import io
import os
import zipfile
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)


def _nb(marker):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(f"def f() -> str:\n    return open('weights.json').read() + {marker!r}\n"))
    return nbformat.writes(nb).encode()


def _zip(marker, weights, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("m.ipynb", _nb(marker))
        archive.writestr("weights.json", weights)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _import(data, overwrite=False):
    r = client.post("/api/notebooks/import", params={"overwrite": str(overwrite).lower()},
                    files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def _weights(env):
    return (env / "uploads" / ".m.ipynb.files" / "weights.json").read_text()


def _versions():
    return client.get("/api/notebooks/m.ipynb/versions?companions=true").json()["versions"]


def test_overwriting_notebook_and_data_then_restoring_returns_both(env):
    _import(_zip("v1", "DATA-ONE"))
    _import(_zip("v2", "DATA-TWO"), overwrite=True)
    assert _weights(env) == "DATA-TWO"
    versions = _versions()
    assert len(versions) == 1 and versions[0]["companion_files"] == 1
    restored = client.post(f"/api/notebooks/m.ipynb/versions/{versions[0]['version_id']}/restore")
    assert restored.status_code == 200
    assert "v1" in (env / "uploads" / "m.ipynb").read_text()
    assert _weights(env) == "DATA-ONE"
    # the restore itself is undoable: it saved the state it replaced, data included
    newest = _versions()[0]
    assert newest["companion_files"] == 1
    client.post(f"/api/notebooks/m.ipynb/versions/{newest['version_id']}/restore")
    assert _weights(env) == "DATA-TWO" and "v2" in (env / "uploads" / "m.ipynb").read_text()


def test_companion_counts_are_opt_in_so_the_listing_shape_is_unchanged(env):
    _import(_zip("v1", "ONE"))
    _import(_zip("v2", "TWO"), overwrite=True)
    plain = client.get("/api/notebooks/m.ipynb/versions").json()["versions"][0]
    assert set(plain) == {"version_id", "size_bytes", "saved_at"}


def test_a_version_saved_without_data_files_restores_to_no_data_files(env):
    r = client.post("/api/upload", files={"file": ("m.ipynb", _nb("v1"), "application/json")})
    assert r.status_code == 200
    _import(_zip("v2", "LATER"), overwrite=True)  # snapshots v1, which had no data files
    version = _versions()[0]
    assert version["companion_files"] == 0
    client.post(f"/api/notebooks/m.ipynb/versions/{version['version_id']}/restore")
    assert not (env / "uploads" / ".m.ipynb.files").exists()


def test_versions_saved_before_data_files_were_versioned_leave_current_files_alone(env):
    _import(_zip("v1", "DATA-ONE"))
    _import(_zip("v2", "DATA-TWO"), overwrite=True)
    version = _versions()[0]
    import shutil

    for snapshot in Path(upload_routes._notebook_versions_dir("m.ipynb")).glob("*.files"):
        shutil.rmtree(snapshot)
    assert _versions()[0]["companion_files"] is None
    client.post(f"/api/notebooks/m.ipynb/versions/{version['version_id']}/restore")
    assert _weights(env) == "DATA-TWO"


def test_a_failure_while_installing_new_data_keeps_the_previous_data(env, monkeypatch):
    _import(_zip("v1", "DATA-ONE"))
    real_replace = os.replace

    def failing(src, dst, *a, **k):
        if str(dst).endswith(".m.ipynb.files") and ".tmp" in str(src):
            raise OSError("disk error")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(upload_routes.os, "replace", failing)
    quiet = TestClient(app, raise_server_exceptions=False)
    r = quiet.post("/api/notebooks/import", params={"overwrite": "true"},
                   files={"file": ("x.zip", _zip("v2", "DATA-TWO"), "application/zip")})
    monkeypatch.undo()
    assert r.status_code >= 500 or r.json()["results"][0]["status"] != "success"
    assert _weights(env) == "DATA-ONE"
    assert not [p for p in (env / "uploads").iterdir() if p.name.endswith((".old", ".tmp"))]


def test_deleting_versions_removes_their_data_snapshots(env):
    _import(_zip("v1", "ONE"))
    _import(_zip("v2", "TWO"), overwrite=True)
    _import(_zip("v3", "THREE"), overwrite=True)
    snapshots = lambda: sorted(p.name for p in Path(upload_routes._notebook_versions_dir("m.ipynb")).glob("*.files"))  # noqa: E731
    assert len(snapshots()) == 2
    first = _versions()[0]["version_id"]
    assert client.delete(f"/api/notebooks/m.ipynb/versions/{first}").status_code == 200
    assert len(snapshots()) == 1
    assert client.delete("/api/notebooks/m.ipynb/versions").status_code == 200
    assert snapshots() == []
    _import(_zip("v4", "FOUR"), overwrite=True)
    assert len(snapshots()) == 1
    client.delete("/api/notebooks/m.ipynb")
    assert not Path(upload_routes._notebook_versions_dir("m.ipynb")).exists()


def test_pruning_old_versions_removes_their_data_snapshots(env, monkeypatch):
    monkeypatch.setattr(upload_routes, "MAX_NOTEBOOK_VERSIONS", 2)
    _import(_zip("v1", "ONE"))
    for i in range(2, 6):
        _import(_zip(f"v{i}", f"DATA{i}"), overwrite=True)
    assert len(_versions()) == 2
    assert len(list(Path(upload_routes._notebook_versions_dir("m.ipynb")).glob("*.files"))) == 2


def test_symlinks_in_the_data_folder_are_never_followed_or_snapshotted(env):
    _import(_zip("v1", "ONE"))
    outside = env / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, env / "uploads" / ".m.ipynb.files" / "link.json")
    _import(_zip("v2", "TWO"), overwrite=True)
    snapshot = next(Path(upload_routes._notebook_versions_dir("m.ipynb")).glob("*.files"))
    assert not (snapshot / "link.json").exists() and (snapshot / "weights.json").read_text() == "ONE"


def test_snapshots_count_toward_the_storage_quota(env):
    _import(_zip("v1", "x" * 1000))
    before = upload_routes._current_total_storage_bytes()
    _import(_zip("v2", "y" * 1000), overwrite=True)
    assert upload_routes._current_total_storage_bytes() >= before + 1000
