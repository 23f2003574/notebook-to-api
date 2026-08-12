import io
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path

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


def test_upload_rejects_content_that_is_not_a_valid_notebook():
    """Before this fix, /api/upload only checked the filename ended in
    ".ipynb" -- literally any content was accepted onto disk with a 200
    "success", and only failed later, opaquely, whenever /api/inspect or
    /api/compile next tried to parse it.
    """

    resp = client.post(
        "/api/upload",
        files={
            "file": (
                "garbage.ipynb",
                io.BytesIO(b"this is not json, let alone a notebook"),
                "application/json",
            )
        },
    )

    assert resp.status_code == 400
    assert not os.path.exists(os.path.join(UPLOAD_DIR, "garbage.ipynb"))


def test_upload_rejects_a_notebook_exceeding_the_configured_max_size(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 10)

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    assert len(content) > 10

    resp = client.post(
        "/api/upload",
        files={"file": ("too_big.ipynb", io.BytesIO(content), "application/json")},
    )

    assert resp.status_code == 413
    assert not os.path.exists(os.path.join(UPLOAD_DIR, "too_big.ipynb"))


def test_upload_accepts_a_notebook_within_a_raised_size_limit(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 10 * 1024 * 1024)

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    resp = client.post(
        "/api/upload",
        files={"file": ("within_limit.ipynb", io.BytesIO(content), "application/json")},
    )

    assert resp.status_code == 200
    assert os.path.exists(os.path.join(UPLOAD_DIR, "within_limit.ipynb"))


def test_upload_reports_overwritten_false_for_a_brand_new_notebook():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    resp = client.post(
        "/api/upload",
        files={"file": ("fresh.ipynb", io.BytesIO(content), "application/json")},
    )

    assert resp.status_code == 200
    assert resp.json()["overwritten"] is False


def test_upload_rejects_a_same_named_reupload_without_overwrite():
    """Before overwrite protection existed, re-uploading an existing
    filename silently replaced its bytes as they streamed in -- with no
    conflict response and no way to opt out of the collision.
    """

    original_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    first_resp = client.post(
        "/api/upload",
        files={"file": ("collide.ipynb", io.BytesIO(original_content), "application/json")},
    )
    assert first_resp.status_code == 200

    conflicting_content = _notebook_bytes(
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )

    second_resp = client.post(
        "/api/upload",
        files={"file": ("collide.ipynb", io.BytesIO(conflicting_content), "application/json")},
    )

    assert second_resp.status_code == 409
    assert "already exists" in second_resp.json()["detail"]

    # The original file must be completely untouched.
    on_disk = Path(UPLOAD_DIR, "collide.ipynb").read_bytes()
    assert on_disk == original_content


def test_upload_replaces_a_same_named_notebook_when_overwrite_is_requested():

    original_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    first_resp = client.post(
        "/api/upload",
        files={"file": ("replace_me.ipynb", io.BytesIO(original_content), "application/json")},
    )
    assert first_resp.status_code == 200

    new_content = _notebook_bytes(
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )

    second_resp = client.post(
        "/api/upload?overwrite=true",
        files={"file": ("replace_me.ipynb", io.BytesIO(new_content), "application/json")},
    )

    assert second_resp.status_code == 200
    assert second_resp.json()["overwritten"] is True

    on_disk = Path(UPLOAD_DIR, "replace_me.ipynb").read_bytes()
    assert on_disk == new_content


def test_upload_with_overwrite_leaves_original_untouched_if_replacement_is_invalid():
    """The critical data-loss case this fix closes: even with
    ?overwrite=true, an invalid or corrupt re-upload must not destroy the
    existing good notebook. Before streaming to a temp file first, the
    write to the final path happened before validation, so this exact
    scenario silently lost the original.
    """

    original_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    first_resp = client.post(
        "/api/upload",
        files={"file": ("dont_lose_me.ipynb", io.BytesIO(original_content), "application/json")},
    )
    assert first_resp.status_code == 200

    second_resp = client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                "dont_lose_me.ipynb",
                io.BytesIO(b"this is not json, let alone a notebook"),
                "application/json",
            )
        },
    )

    assert second_resp.status_code == 400

    on_disk = Path(UPLOAD_DIR, "dont_lose_me.ipynb").read_bytes()
    assert on_disk == original_content


def test_upload_leaves_no_temp_files_behind_after_a_rejected_reupload():

    original_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    client.post(
        "/api/upload",
        files={"file": ("no_debris.ipynb", io.BytesIO(original_content), "application/json")},
    )
    client.post(
        "/api/upload",
        files={"file": ("no_debris.ipynb", io.BytesIO(original_content), "application/json")},
    )

    leftover = [
        name for name in os.listdir(UPLOAD_DIR)
        if "no_debris" in name and not name.endswith(".ipynb")
    ]
    assert leftover == []


def test_list_notebooks_includes_uploaded_files():
    """/api/upload was previously a one-way door: nothing in the API let
    a caller see what had already been uploaded, or remove it again.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("list_test.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    list_resp = client.get("/api/notebooks")
    assert list_resp.status_code == 200

    notebooks = list_resp.json()["notebooks"]
    entry = next(nb for nb in notebooks if nb["filename"] == "list_test.ipynb")

    assert entry["size_bytes"] == len(content)
    assert "modified_at" in entry
    assert entry["currently_compiled"] is False


def test_list_notebooks_marks_the_currently_compiled_notebook():
    """Nothing previously recorded which uploaded notebook (if any)
    produced whatever's currently in GENERATED_DIR -- a dashboard
    frontend had to track that itself client-side, which is fragile
    (lost on refresh) and wrong the moment a second compile happens
    without it finding out.

    /api/compile always targets the real "generated" directory (like
    /api/export-openapi, /api/export-sdk, /api/download and /api/deploy
    already do -- see their own docstrings), so this exercises that same
    shared directory rather than an isolated one, matching how the other
    compile-flow tests in this file already operate.
    """

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("currently_compiled_a.ipynb", content_a),
        ("currently_compiled_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "currently_compiled_a.ipynb"}
    )
    assert compile_resp.status_code == 200

    after = {
        nb["filename"]: nb["currently_compiled"]
        for nb in client.get("/api/notebooks").json()["notebooks"]
    }
    assert after["currently_compiled_a.ipynb"] is True
    assert after["currently_compiled_b.ipynb"] is False

    # Recompiling a different notebook must flip which one is flagged.
    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "currently_compiled_b.ipynb"}
    )
    assert compile_resp.status_code == 200

    final = {
        nb["filename"]: nb["currently_compiled"]
        for nb in client.get("/api/notebooks").json()["notebooks"]
    }
    assert final["currently_compiled_a.ipynb"] is False
    assert final["currently_compiled_b.ipynb"] is True


def test_list_notebooks_currently_compiled_is_false_when_metadata_is_missing(
    monkeypatch
):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_no_metadata_test_dir"
    )

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    resp = client.post(
        "/api/upload",
        files={"file": ("no_metadata_test.ipynb", io.BytesIO(content), "application/json")},
    )
    assert resp.status_code == 200

    notebooks = client.get("/api/notebooks").json()["notebooks"]
    entry = next(nb for nb in notebooks if nb["filename"] == "no_metadata_test.ipynb")

    assert entry["currently_compiled"] is False
    # Not the currently-compiled notebook at all -- staleness is only
    # meaningful (and only reported) for the one that is.
    assert "notebook_changed_since_compile" not in entry


def test_list_notebooks_reports_the_currently_compiled_notebook_as_unchanged_right_after_compile():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("freshly_compiled.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "freshly_compiled.ipynb"}
    )
    assert compile_resp.status_code == 200

    notebooks = client.get("/api/notebooks").json()["notebooks"]
    entry = next(nb for nb in notebooks if nb["filename"] == "freshly_compiled.ipynb")

    assert entry["currently_compiled"] is True
    assert entry["notebook_changed_since_compile"] is False


def test_list_notebooks_flags_a_notebook_changed_since_its_last_compile():
    """The gap this closes: /api/notebooks could already say "this is the
    notebook that produced generated/" (see
    test_list_notebooks_marks_the_currently_compiled_notebook), but had
    no way to tell a caller that notebook had since been edited and
    re-uploaded -- e.g. via /api/upload?overwrite=true -- *after* that
    compile, silently leaving the currently-served app stale relative to
    what a caller might reasonably assume it still matches exactly.
    """

    original_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("edited_after_compile.ipynb", io.BytesIO(original_content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "edited_after_compile.ipynb"}
    )
    assert compile_resp.status_code == 200

    changed_content = _notebook_bytes(
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )

    overwrite_resp = client.post(
        "/api/upload?overwrite=true",
        files={"file": ("edited_after_compile.ipynb", io.BytesIO(changed_content), "application/json")},
    )
    assert overwrite_resp.status_code == 200

    notebooks = client.get("/api/notebooks").json()["notebooks"]
    entry = next(nb for nb in notebooks if nb["filename"] == "edited_after_compile.ipynb")

    # Still the notebook that produced the current generated/ output --
    # just no longer an exact match for what's actually on disk now.
    assert entry["currently_compiled"] is True
    assert entry["notebook_changed_since_compile"] is True


def test_delete_notebook_removes_an_uploaded_file():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("delete_test.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200
    assert os.path.exists(os.path.join(UPLOAD_DIR, "delete_test.ipynb"))

    delete_resp = client.delete("/api/notebooks/delete_test.ipynb")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["filename"] == "delete_test.ipynb"

    assert not os.path.exists(os.path.join(UPLOAD_DIR, "delete_test.ipynb"))

    list_resp = client.get("/api/notebooks")
    filenames = {nb["filename"] for nb in list_resp.json()["notebooks"]}
    assert "delete_test.ipynb" not in filenames


def test_delete_notebook_returns_404_for_missing_file():

    resp = client.delete("/api/notebooks/does_not_exist_at_all.ipynb")

    assert resp.status_code == 404


def test_get_notebook_returns_the_uploaded_content():
    """GET /api/notebooks lists what's been uploaded and DELETE removes
    it, but there was previously no way to retrieve a specific notebook's
    actual content again -- only re-upload a fresh copy from scratch.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("get_test.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    get_resp = client.get("/api/notebooks/get_test.ipynb")

    assert get_resp.status_code == 200
    assert get_resp.headers["content-type"] == "application/x-ipynb+json"
    assert json.loads(get_resp.content) == json.loads(content)


def test_get_notebook_returns_404_for_missing_file():

    resp = client.get("/api/notebooks/does_not_exist_at_all.ipynb")

    assert resp.status_code == 404


def test_get_notebook_rejects_absolute_filename():

    resp = client.get("/api/notebooks/%2Fetc%2Fpasswd")

    assert resp.status_code in (400, 404)
    assert "root:" not in resp.text


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
    assert compile_resp.json()["endpoints"] == [
        {"path": "/add", "method": "POST", "is_async": False}
    ]


def test_compile_endpoints_flag_background_functions_as_async():
    """A dashboard frontend building a UI from /api/compile's response
    previously had no way to tell a background/task_id-based endpoint
    (see LONG_RUNNING_KEYWORDS in generator/api_generator.py) apart from
    a synchronous one -- only the separately-fetched OpenAPI schema
    marked these with x-notebook-to-api-async (see
    test_background_endpoint_documents_the_task_response_it_actually_sends
    in test_generator.py).
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def train_model(epochs: int) -> str:\n    return 'done'\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "async_endpoints_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "async_endpoints_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    endpoints = {e["path"]: e for e in compile_resp.json()["endpoints"]}

    assert endpoints["/add"] == {"path": "/add", "method": "POST", "is_async": False}
    assert endpoints["/train_model"] == {
        "path": "/train_model", "method": "POST", "is_async": True
    }


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


def test_inspect_reports_reserved_name_conflicts_for_a_colliding_function():
    """/api/inspect is the tool's own "preview what compiling this
    notebook will do" step, but had no idea a function named
    "health_check" collides with an identifier the generated app itself
    defines (see RESERVED_INFRASTRUCTURE_NAMES in
    generator/api_generator.py) until /api/compile actually failed on it.
    """

    content = _notebook_bytes(
        "def health_check() -> dict:\n    return {}\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "reserved_name_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    inspect_resp = client.post(
        "/api/inspect", json={"notebook_path": "reserved_name_test.ipynb"}
    )
    assert inspect_resp.status_code == 200

    body = inspect_resp.json()
    assert body["reserved_name_conflicts"] == ["health_check"]


def test_inspect_reports_no_reserved_name_conflicts_for_a_clean_notebook():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "no_conflicts_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    inspect_resp = client.post(
        "/api/inspect", json={"notebook_path": "no_conflicts_test.ipynb"}
    )
    assert inspect_resp.status_code == 200
    assert inspect_resp.json()["reserved_name_conflicts"] == []


def test_inspect_reports_endpoints_and_flags_background_ones_before_compiling():
    """Mirrors test_compile_endpoints_flag_background_functions_as_async
    above, but for /api/inspect: before this fix, that classification was
    only visible in /api/compile's response, after compiling.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def train_model(epochs: int) -> str:\n    return 'done'\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "inspect_async_endpoints_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    inspect_resp = client.post(
        "/api/inspect", json={"notebook_path": "inspect_async_endpoints_test.ipynb"}
    )
    assert inspect_resp.status_code == 200

    endpoints = {e["path"]: e for e in inspect_resp.json()["endpoints"]}

    assert endpoints["/add"] == {"path": "/add", "method": "POST", "is_async": False}
    assert endpoints["/train_model"] == {
        "path": "/train_model", "method": "POST", "is_async": True
    }


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


def test_compile_returns_400_for_a_reserved_function_name():
    """generate_fastapi_code (backend/generator/api_generator.py) refuses
    to compile a function named "health_check" -- it collides with an
    identifier the generated app itself defines. Before this, that
    ReservedFunctionNameError (the notebook's own fault, not this
    server's) fell through the endpoint's generic `except Exception` and
    came back as a misleading 500, identical to what a genuine server-side
    bug would produce.
    """

    content = _notebook_bytes(
        "def health_check() -> dict:\n    return {}\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "reserved_name_compile_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "reserved_name_compile_test.ipynb"}
    )

    assert compile_resp.status_code == 400
    assert "health_check" in compile_resp.json()["detail"]


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


def _install_fake_docker(bin_dir, log_path):
    """A fake `docker` executable that records how it was invoked instead
    of actually building/pushing an image (mirrors the technique used in
    tests/test_cli_deploy.py for the CLI's own `deploy` command). Appends
    a record per invocation so build and push calls can each be
    inspected independently, in order.
    """

    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/bin/sh\n"
        f'{{ printf \'%s\\n\' "$@"; pwd; printf \'%s\\n\' "==CALL=="; }} >> "{log_path}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _compile_a_notebook(filename):

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post("/api/compile", json={"notebook_path": filename})
    assert compile_resp.status_code == 200


def test_deploy_endpoint_returns_404_when_nothing_compiled_yet(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_deploy_test_missing_dir"
    )

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 404


def test_deploy_endpoint_builds_image_with_default_tag(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_flow_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["tag"] == "generated:latest"
    assert body["pushed"] is False

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    assert len(calls) == 1
    build_call = calls[0].splitlines()
    assert build_call[:-1] == ["build", "-t", "generated:latest", "."]
    assert build_call[-1] == os.path.abspath("generated")


def test_deploy_endpoint_respects_custom_tag(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_tag_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={"tag": "myregistry.example.com/myapp:v2"})

    assert resp.status_code == 200
    assert resp.json()["tag"] == "myregistry.example.com/myapp:v2"

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    build_call = calls[0].splitlines()
    assert build_call[:-1] == ["build", "-t", "myregistry.example.com/myapp:v2", "."]


def test_deploy_endpoint_pushes_when_requested(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_push_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post(
        "/api/deploy", json={"tag": "myregistry.example.com/myapp:v3", "push": True}
    )

    assert resp.status_code == 200
    assert resp.json()["pushed"] is True

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    assert len(calls) == 2
    assert calls[0].splitlines()[:-1] == ["build", "-t", "myregistry.example.com/myapp:v3", "."]
    assert calls[1].splitlines()[:-1] == ["push", "myregistry.example.com/myapp:v3"]


def test_deploy_endpoint_does_not_push_by_default(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_no_push_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 200
    assert resp.json()["pushed"] is False

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    assert len(calls) == 1


def test_deploy_endpoint_returns_500_when_docker_is_missing(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_missing_docker_test.ipynb")

    empty_bin_dir = tmp_path / "empty_bin"
    empty_bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin_dir))

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 500
    assert "Docker CLI not found" in resp.json()["detail"]


def test_deploy_endpoint_returns_500_when_docker_is_missing_for_push(monkeypatch):
    """Before this fix, only the `docker build` call handled Docker being
    missing at all -- `docker push` had no FileNotFoundError handling
    whatsoever, so losing Docker between a successful build and the push
    step crashed the request instead of returning a clean error.
    """

    from backend.routes import upload as upload_module

    _compile_a_notebook("deploy_missing_docker_for_push_test.ipynb")

    def fake_run(args, **kwargs):
        if args[1] == "build":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(upload_module.subprocess, "run", fake_run)

    resp = client.post("/api/deploy", json={"push": True})

    assert resp.status_code == 500
    assert "Docker CLI not found" in resp.json()["detail"]


def test_deploy_endpoint_returns_504_when_docker_build_times_out(monkeypatch):
    """subprocess.run(..., timeout=...) raises TimeoutExpired, not
    FileNotFoundError -- before this fix, that exception type wasn't
    caught anywhere in /api/deploy at all, so a `docker build` that ran
    past the timeout crashed the request with FastAPI's generic
    unhandled-exception 500 instead of an actionable error.
    """

    from backend.routes import upload as upload_module

    _compile_a_notebook("deploy_build_timeout_test.ipynb")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(upload_module.subprocess, "run", fake_run)

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 504
    assert "did not finish within" in resp.json()["detail"]


def test_deploy_endpoint_returns_504_when_docker_push_times_out(monkeypatch):

    from backend.routes import upload as upload_module

    _compile_a_notebook("deploy_push_timeout_test.ipynb")

    def fake_run(args, **kwargs):
        if args[1] == "build":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(upload_module.subprocess, "run", fake_run)

    resp = client.post("/api/deploy", json={"push": True})

    assert resp.status_code == 504
    assert "did not finish within" in resp.json()["detail"]


def test_deploy_subprocess_timeout_is_configurable(monkeypatch):
    """DEPLOY_SUBPROCESS_TIMEOUT_SECONDS matches the existing
    NOTEBOOK_API_* env-var convention (see MAX_UPLOAD_BYTES) instead of
    the fixed 600s previously hardcoded directly into each subprocess.run
    call, so a deploy environment that needs longer (a slow/cold layer
    cache) or wants it clamped shorter (fail fast in CI) can configure it.
    """

    from backend.routes import upload as upload_module

    _compile_a_notebook("deploy_custom_timeout_test.ipynb")

    monkeypatch.setattr(upload_module, "DEPLOY_SUBPROCESS_TIMEOUT_SECONDS", 5)

    captured_kwargs = {}

    def fake_run(args, **kwargs):
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(upload_module.subprocess, "run", fake_run)

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 200
    assert captured_kwargs["timeout"] == 5


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
