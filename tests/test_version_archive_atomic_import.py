"""POST /api/notebooks/{f}/versions/import is all-or-nothing: a rejected archive changes nothing."""
import functools
import io
import zipfile

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.routes import upload


@functools.lru_cache(maxsize=None)
def _nb(text):
    return nbformat.writes(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(f"def f():\n    return {text}\n")])).encode()


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS", "NOTEBOOK_API_DASHBOARD_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(upload, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(upload, "GENERATED_APPS_DIR", str(tmp_path / "apps"))
    (tmp_path / "up").mkdir()
    return TestClient(dashboard.app)


def _state(tmp_path):
    root = tmp_path / "up"
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _import(client, entries, **params):
    return client.post("/api/notebooks/n.ipynb/versions/import", params=params, files={"file": ("a.zip", _zip(entries))})


VERSION = "20260101T000000000000_aaaaaaaa.ipynb"


def test_successful_import_restores_notebook_and_versions(client, tmp_path):
    r = _import(client, {"n.ipynb": _nb(1), f"versions/{VERSION}": _nb(0)})
    assert r.status_code == 200 and r.json()["imported_version_ids"] == [VERSION]
    assert (tmp_path / "up" / "n.ipynb").read_bytes() == _nb(1)
    assert (tmp_path / "up" / ".versions" / "n.ipynb" / VERSION).read_bytes() == _nb(0)
    assert not list((tmp_path / "up").glob(".import-*"))


def test_oversized_version_entry_leaves_everything_unchanged(client, tmp_path, monkeypatch):
    assert client.post("/api/upload", files={"file": ("n.ipynb", _nb(1))}).status_code == 200
    before = _state(tmp_path)
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 5000)
    r = _import(client, {"n.ipynb": _nb(2), f"versions/{VERSION}": b"x" * 6000}, overwrite="true")
    assert r.status_code == 413
    assert _state(tmp_path) == before


def test_corrupt_member_leaves_everything_unchanged(client, tmp_path, monkeypatch):
    assert client.post("/api/upload", files={"file": ("n.ipynb", _nb(1))}).status_code == 200
    before = _state(tmp_path)

    def broken(archive, name):
        if name.startswith("versions/"):
            raise zipfile.BadZipFile("bad CRC")
        return _nb(2)

    monkeypatch.setattr(upload, "_read_archive_member", broken)
    r = _import(client, {"n.ipynb": _nb(2), f"versions/{VERSION}": _nb(0)}, overwrite="true")
    assert r.status_code == 400
    assert _state(tmp_path) == before


def test_empty_version_entry_is_refused_without_a_zero_byte_file(client, tmp_path):
    r = _import(client, {"n.ipynb": _nb(1), f"versions/{VERSION}": b""})
    assert r.status_code == 400
    assert _state(tmp_path) == {}


def test_metadata_write_failure_rolls_back_overwrite(client, tmp_path, monkeypatch):
    assert client.post("/api/upload", files={"file": ("n.ipynb", _nb(1))}).status_code == 200
    before = _state(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(upload, "_write_notebook_tags", boom)
    with pytest.raises(RuntimeError):
        _import(client, {"n.ipynb": _nb(2), f"versions/{VERSION}": _nb(0), "tags.json": b'["a"]'}, overwrite="true")
    assert _state(tmp_path) == before


def test_dry_run_writes_nothing_and_reports_same_ids(client, tmp_path):
    r = _import(client, {"n.ipynb": _nb(1), f"versions/{VERSION}": _nb(0)}, dry_run="true")
    assert r.status_code == 200 and r.json()["imported_version_ids"] == [VERSION]
    assert _state(tmp_path) == {}
