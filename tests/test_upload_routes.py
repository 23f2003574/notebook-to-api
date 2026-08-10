import io
import os
import zipfile

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.dashboard import app
from backend.routes.upload import UPLOAD_DIR, resolve_upload_path

client = TestClient(app)


def _notebook_bytes(function_source):
    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(nbformat.v4.new_code_cell(function_source))
    return nbformat.writes(notebook).encode("utf-8")


@pytest.fixture(autouse=True)
def _cleanup_uploaded_files():
    created_before = set(os.listdir(UPLOAD_DIR))
    yield
    for name in set(os.listdir(UPLOAD_DIR)) - created_before:
        os.remove(os.path.join(UPLOAD_DIR, name))


def test_resolve_upload_path_rejects_absolute_path():

    with pytest.raises(Exception):
        resolve_upload_path("/etc/passwd")


def test_resolve_upload_path_rejects_relative_traversal():

    with pytest.raises(Exception):
        resolve_upload_path("../../../../etc/passwd")


def test_resolve_upload_path_accepts_plain_filename():

    resolved = resolve_upload_path("my_notebook.ipynb")

    assert resolved.name == "my_notebook.ipynb"
    assert str(resolved).startswith(os.path.abspath(UPLOAD_DIR))


def test_upload_rejects_filename_that_escapes_upload_dir():
    """Confirmed exploitable before this fix: an uploaded file named
    '../poc.ipynb' was written one directory above uploads/, outside the
    intended storage location, with status 200 "success".
    """

    resp = client.post(
        "/api/upload",
        files={"file": ("../escape_test.ipynb", io.BytesIO(b"data"), "application/json")},
    )

    assert resp.status_code == 400
    assert not os.path.exists("escape_test.ipynb")


def test_inspect_rejects_absolute_notebook_path():
    """Confirmed exploitable before this fix: passing an absolute path
    like /etc/passwd caused the server to read that file and leak its
    contents back in the HTTP error response.
    """

    resp = client.post("/api/inspect", json={"notebook_path": "/etc/passwd"})

    assert resp.status_code == 400
    assert "passwd" not in resp.text.lower() or "root:" not in resp.text


def test_inspect_rejects_relative_traversal_notebook_path():

    resp = client.post(
        "/api/inspect", json={"notebook_path": "../../../../etc/passwd"}
    )

    assert resp.status_code == 400


def test_compile_rejects_absolute_notebook_path():

    resp = client.post("/api/compile", json={"notebook_path": "/etc/passwd"})

    assert resp.status_code == 400


def test_compile_rejects_relative_traversal_notebook_path():

    resp = client.post(
        "/api/compile", json={"notebook_path": "../../../../etc/passwd"}
    )

    assert resp.status_code == 400


def test_upload_inspect_compile_still_works_for_a_legitimate_notebook():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("legit_test.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["filename"] == "legit_test.ipynb"

    inspect_resp = client.post(
        "/api/inspect", json={"notebook_path": "legit_test.ipynb"}
    )
    assert inspect_resp.status_code == 200
    functions = inspect_resp.json()["functions"]
    assert functions[0]["name"] == "add"

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "legit_test.ipynb"}
    )
    assert compile_resp.status_code == 200
    assert compile_resp.json()["endpoints"] == ["/add"]


def test_inspect_missing_notebook_still_returns_404_not_400():
    """A well-formed, in-bounds filename that simply doesn't exist should
    still 404 (existing behaviour), not be confused with a rejected path.
    """

    resp = client.post(
        "/api/inspect", json={"notebook_path": "does_not_exist_at_all.ipynb"}
    )

    assert resp.status_code == 404


def test_inspect_reports_dependencies_and_generated_files_after_a_compile():
    """/api/inspect previously only ever returned "functions", even though
    inspect_notebook_data (backend/inspector.py) already computed
    dependencies and generated_files -- it just wasn't wired to this
    route.
    """

    content = _notebook_bytes(
        "import math\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "inspect_flow_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "inspect_flow_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    inspect_resp = client.post(
        "/api/inspect", json={"notebook_path": "inspect_flow_test.ipynb"}
    )
    assert inspect_resp.status_code == 200
    body = inspect_resp.json()

    assert body["functions"][0]["name"] == "add"
    assert body["dependencies"] == ["math"]
    assert "app.py" in body["generated_files"]
    assert "requirements.txt" in body["generated_files"]


def test_inspect_generated_files_is_empty_before_any_compile(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_inspect_test_missing_dir"
    )

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "inspect_no_compile_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    inspect_resp = client.post(
        "/api/inspect", json={"notebook_path": "inspect_no_compile_test.ipynb"}
    )
    assert inspect_resp.status_code == 200
    body = inspect_resp.json()

    assert body["functions"][0]["name"] == "add"
    assert body["generated_files"] == []


def test_export_openapi_and_export_sdk_full_flow():
    """The dashboard frontend can compile a notebook via /api/compile but,
    before this, had no way to fetch the OpenAPI schema or an SDK client
    without shelling out to the `export-openapi`/`export-sdk` CLI commands.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "export_flow_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "export_flow_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    openapi_resp = client.post("/api/export-openapi", json={"format": "json"})
    assert openapi_resp.status_code == 200
    openapi_body = openapi_resp.json()
    assert openapi_body["format"] == "json"
    assert "/add" in openapi_body["schema"]["paths"]

    yaml_resp = client.post("/api/export-openapi", json={"format": "yaml"})
    assert yaml_resp.status_code == 200
    yaml_body = yaml_resp.json()
    assert yaml_body["format"] == "yaml"
    assert "content" in yaml_body

    sdk_resp = client.post("/api/export-sdk", json={"language": "python"})
    assert sdk_resp.status_code == 200
    sdk_body = sdk_resp.json()
    assert sdk_body["language"] == "python"
    assert "class NotebookAPIClient" in sdk_body["code"]
    assert "def add" in sdk_body["code"]

    ts_resp = client.post("/api/export-sdk", json={"language": "typescript"})
    assert ts_resp.status_code == 200
    ts_body = ts_resp.json()
    assert ts_body["language"] == "typescript"
    assert "class NotebookAPIClient" in ts_body["code"]


def test_export_openapi_rejects_invalid_format():

    resp = client.post("/api/export-openapi", json={"format": "xml"})

    assert resp.status_code == 400


def test_export_sdk_rejects_invalid_language():

    resp = client.post("/api/export-sdk", json={"language": "rust"})

    assert resp.status_code == 400


def test_export_openapi_returns_404_when_nothing_compiled_yet(monkeypatch):
    """Uses a package name that has never been compiled anywhere on
    sys.path, so the dynamic `importlib.import_module` in
    export_openapi_schema is guaranteed to raise ModuleNotFoundError
    rather than risk resolving a stale cached "generated" module from an
    earlier test in this same process.
    """

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_export_test_missing_dir"
    )

    resp = client.post("/api/export-openapi", json={"format": "json"})

    assert resp.status_code == 404


def test_export_sdk_returns_404_without_prior_openapi_export(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_export_test_missing_dir_2"
    )

    resp = client.post("/api/export-sdk", json={"language": "python"})

    assert resp.status_code == 404


def test_download_returns_404_when_nothing_compiled_yet(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_download_test_missing_dir"
    )

    resp = client.get("/api/download")

    assert resp.status_code == 404


def test_download_returns_a_zip_of_the_compiled_app():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "download_flow_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "download_flow_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    download_resp = client.get("/api/download")

    assert download_resp.status_code == 200
    assert download_resp.headers["content-type"] == "application/zip"
    assert (
        'attachment; filename="generated.zip"'
        == download_resp.headers["content-disposition"]
    )

    archive = zipfile.ZipFile(io.BytesIO(download_resp.content))
    names = set(archive.namelist())

    assert "app.py" in names
    assert "requirements.txt" in names
    assert "Dockerfile" in names
    assert "runtime/notebook_module.py" in names

    app_source = archive.read("app.py").decode("utf-8")
    assert "def add(" in app_source
