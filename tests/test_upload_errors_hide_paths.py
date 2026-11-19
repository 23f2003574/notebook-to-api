"""Invalid notebook uploads are explained without revealing server paths."""
import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)

BAD = {
    "malformed_json": b"{not json",
    "empty": b"",
    "missing_cells": b'{"nbformat": 4}',
    "not_an_object": b"[1, 2]",
    "wrong_cells_type": b'{"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": 5}',
}
LEAK = re.compile(r"(/tmp|/var|/home|/private|[A-Za-z]:\\|\.part\b|[0-9a-f]{32}|uploads/)")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_routes, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(upload_routes, "GENERATED_DIR", str(tmp_path / "generated"))
    (tmp_path / "uploads").mkdir()
    return tmp_path


@pytest.mark.parametrize("kind", sorted(BAD))
def test_upload_error_names_the_file_not_the_server_path(dirs, kind):
    r = client.post("/api/upload", files={"file": ("mine.ipynb", BAD[kind], "application/json")})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert not LEAK.search(detail), detail
    assert str(dirs) not in detail
    assert "not a valid Jupyter notebook" in detail  # still specific
    assert "mine.ipynb" in detail or "JSON" in detail or "cells" in detail


def test_batch_and_zip_import_errors_do_not_leak_either(dirs):
    r = client.post("/api/upload/batch", files=[("files", ("a.ipynb", BAD["malformed_json"], "application/json"))])
    assert not LEAK.search(r.text) and str(dirs) not in r.text
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bad.ipynb", BAD["missing_cells"])
    r = client.post("/api/notebooks/import", files={"file": ("x.zip", buf.getvalue(), "application/zip")})
    assert not LEAK.search(r.text) and str(dirs) not in r.text


def test_public_error_text_reduces_paths_and_temp_names():
    text = upload_routes._public_error_text(
        ValueError("/tmp/x/uploads/.mine.ipynb.0123456789abcdef0123456789abcdef.part is not a Jupyter notebook")
    )
    assert text == "mine.ipynb is not a Jupyter notebook"
    assert upload_routes._public_error_text(ValueError("no path here: line 1")) == "no path here: line 1"
