"""A zip member is size-checked before it is inflated: a tiny archive of a huge
member must not be expanded into memory."""
import io
import tracemalloc
import zipfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import dashboard
from backend.routes import upload as upload_module


def _zip(entries, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_a_member_within_the_limit_is_returned(monkeypatch):
    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 100)
    assert upload_module._read_archive_member(_zip([("a.ipynb", b"x" * 100)]), "a.ipynb") == b"x" * 100


def test_a_member_over_the_limit_is_refused_without_inflating_it(monkeypatch):
    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 1000)
    archive = _zip([("big.ipynb", b"\0" * 5_000_000)])
    monkeypatch.setattr(archive, "open", lambda *a, **k: pytest.fail("inflated an oversized member"))
    with pytest.raises(HTTPException) as caught:
        upload_module._read_archive_member(archive, "big.ipynb")
    assert caught.value.status_code == 413


def test_a_forged_declared_size_is_rejected_not_trusted(monkeypatch):
    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 1000)
    archive = _zip([("big.ipynb", b"\0" * 5_000_000)])
    archive.getinfo("big.ipynb").file_size = 10  # a forged header
    with pytest.raises((HTTPException, zipfile.BadZipFile)):
        upload_module._read_archive_member(archive, "big.ipynb")


def test_importing_a_compression_bomb_stays_in_small_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.ipynb", b"\0" * 100_000_000)
    tracemalloc.start()
    response = TestClient(dashboard.app).post(
        "/api/notebooks/import", files={"file": ("a.zip", buffer.getvalue(), "application/zip")}
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert "maximum upload size" in response.json()["results"][0]["detail"]
    assert peak < 40_000_000


def test_an_oversized_archive_upload_is_refused_with_413(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(upload_module, "MAX_ARCHIVE_BYTES", 1_000_000)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("junk.bin", bytes(range(256)) * 8000)  # ~2 MB, stored
    response = TestClient(dashboard.app).post(
        "/api/notebooks/import", files={"file": ("a.zip", buffer.getvalue(), "application/zip")}
    )
    assert response.status_code == 413 and "maximum size" in response.json()["detail"]


def test_an_archive_within_the_cap_still_imports(tmp_path, monkeypatch):
    import nbformat
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    notebook = nbformat.writes(nbformat.v4.new_notebook())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ok.ipynb", notebook)
    response = TestClient(dashboard.app).post(
        "/api/notebooks/import", files={"file": ("a.zip", buffer.getvalue(), "application/zip")}
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "success", response.text
