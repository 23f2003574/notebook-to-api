"""New notebook names are validated and may not collide by letter case; stored odd names stay manageable."""
import io
import zipfile

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.observability.deployment_governance_audit import reset_audit_service
from backend.routes import upload

NB = nbformat.writes(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("def f(a: int) -> int:\n    return a\n")]))
BAD = ["..\\x.ipynb", "a\\b.ipynb", "CON.ipynb", "nul.txt.ipynb", "a:b.ipynb", "name .ipynb", "..ipynb", ".ipynb"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS", "NOTEBOOK_API_DASHBOARD_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(upload, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(upload, "GENERATED_DIR", str(tmp_path / "gen"))
    monkeypatch.setattr(upload, "GENERATED_APPS_DIR", str(tmp_path / "apps"))
    (tmp_path / "up").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory")
    reset_audit_service()
    yield TestClient(dashboard.app)
    reset_audit_service()


def _up(c, name, **params):
    return c.post("/api/upload", files={"file": (name, NB.encode())}, params=params)


def _zip(name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, NB)
    return buf.getvalue()


@pytest.mark.parametrize("name", BAD)
def test_unsafe_names_rejected_on_upload(client, name):
    r = _up(client, name)
    assert r.status_code == 400, (name, r.text)


def test_valid_names_still_work(client):
    for name in ("plain.ipynb", "with space.ipynb", "é.ipynb", ".hidden.ipynb", "a.b.ipynb", "UPPER.ipynb"):
        assert _up(client, name).status_code == 200, name


def test_case_colliding_upload_is_refused_even_with_overwrite(client):
    assert _up(client, "a.ipynb").status_code == 200
    for params in ({}, {"overwrite": "true"}):
        r = _up(client, "A.ipynb", **params)
        assert r.status_code == 409 and "letter case" in r.json()["detail"]
    assert _up(client, "a.ipynb", overwrite="true").status_code == 200  # same name: overwrite semantics intact
    assert [n["filename"] for n in client.get("/api/notebooks").json()["notebooks"]] == ["a.ipynb"]


def test_import_validates_names(client):
    r = client.post("/api/notebooks/import", files={"file": ("x.zip", _zip("CON.ipynb"))})
    assert r.status_code == 400 or r.json()["results"][0]["status"] != "success"
    assert client.get("/api/notebooks").json()["notebooks"] == []


def test_rename_validates_and_detects_case_collisions(client):
    _up(client, "a.ipynb")
    _up(client, "b.ipynb")
    assert client.patch("/api/notebooks/a.ipynb", json={"new_filename": "..\\x.ipynb"}).status_code == 400
    assert client.patch("/api/notebooks/a.ipynb", json={"new_filename": "B.ipynb"}).status_code == 409
    assert client.patch("/api/notebooks/a.ipynb", json={"new_filename": "A.ipynb"}).status_code == 200  # case-only rename


def test_copy_validates_and_detects_case_collisions(client):
    _up(client, "a.ipynb")
    assert client.post("/api/notebooks/a.ipynb/copy", json={"new_filename": "nul.ipynb"}).status_code == 400
    assert client.post("/api/notebooks/a.ipynb/copy", json={"new_filename": "A.ipynb"}).status_code == 409
    assert client.post("/api/notebooks/a.ipynb/copy", json={"new_filename": "copy.ipynb"}).status_code == 200


def test_version_copy_validates_names(client):
    _up(client, "a.ipynb")
    _up(client, "a.ipynb", overwrite="true")
    vid = client.get("/api/notebooks/a.ipynb/versions").json()["versions"][0]["version_id"]
    r = client.post(f"/api/notebooks/a.ipynb/versions/{vid}/copy", json={"new_filename": "a:b.ipynb"})
    assert r.status_code == 400


def test_already_stored_odd_names_can_still_be_deleted(client, tmp_path):
    (tmp_path / "up" / "..\\old.ipynb").write_text(NB)
    assert client.delete("/api/notebooks/..%5Cold.ipynb").status_code == 200


def test_control_characters_are_rejected_by_the_validator():
    from fastapi import HTTPException

    for name in ("x\x01.ipynb", "x\n.ipynb", "x\x7f.ipynb"):
        with pytest.raises(HTTPException) as err:
            upload.validate_new_notebook_filename(name)
        assert err.value.status_code == 400


@pytest.mark.parametrize("name", ["evil‮gpj.ipynb", "a​b.ipynb", "a b.ipynb", "a﻿b.ipynb"])
def test_invisible_and_direction_control_characters_are_rejected(client, name):
    r = _up(client, name)
    assert r.status_code == 400 and "Unicode character" in r.json()["detail"]
    assert client.get("/api/notebooks").json()["notebooks"] == []


def test_ordinary_unicode_names_still_work(client):
    assert _up(client, "métrique-数据.ipynb").status_code == 200


def test_unicode_normalization_colliding_upload_is_refused(client):
    assert _up(client, "café.ipynb").status_code == 200
    r = _up(client, "café.ipynb", overwrite="true")
    assert r.status_code == 409 and "normalization" in r.json()["detail"]
