import io
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import COMPILE_LOCK, COMPILE_METADATA_FILENAME
from backend.dashboard import app
from backend.routes.upload import UPLOAD_DIR, resolve_generated_path, resolve_upload_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

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


def test_blocking_endpoints_are_declared_as_plain_def_not_async_def():
    """FastAPI only runs `async def` path operations directly on the
    single asyncio event loop; a handler that does purely synchronous,
    blocking work (file I/O, subprocess.run for `docker build`/`docker
    push` -- up to DEPLOY_SUBPROCESS_TIMEOUT_SECONDS, 600s by default --
    and compile_notebook's own file writes and per-dependency
    importlib.metadata.version() lookups) with no `await` inside it
    blocks *every* concurrent request this server is handling for as
    long as it runs -- not just the one caller who made it, including an
    unrelated GET /api/health from a completely different client.

    Confirmed against a real (non-TestClient) uvicorn server: an async
    def endpoint blocking for 1.5s with no await delayed a concurrent
    request to a trivial endpoint by the same 1.5s; the identical
    blocking call in a plain def endpoint (which FastAPI runs in its
    worker threadpool instead) added under 2ms. Every one of these
    handlers is purely synchronous internally already, so declaring them
    `def` instead of `async def` changes nothing about how they work --
    only how FastAPI schedules them -- which is also why this can be
    verified directly (is this a coroutine function or not) rather than
    through a timing-based test: TestClient's own threading model doesn't
    reproduce single-event-loop contention the way a real server does.
    """
    import inspect

    from backend.routes import upload as upload_module

    blocking_endpoints = [
        upload_module.list_notebooks,
        upload_module.delete_notebook,
        upload_module.get_notebook,
        upload_module.rename_notebook,
        upload_module.inspect_notebook_endpoint,
        upload_module.compile_notebook_endpoint,
        upload_module.export_openapi_endpoint,
        upload_module.export_sdk_endpoint,
        upload_module.deploy_generated_app,
        upload_module.download_generated_app,
        upload_module.list_generated_files_endpoint,
        upload_module.delete_generated_app,
        upload_module.health_check,
    ]

    for endpoint in blocking_endpoints:
        assert not inspect.iscoroutinefunction(endpoint), (
            f"{endpoint.__name__} is declared async def but does no "
            "awaiting -- it blocks the whole event loop, not just its "
            "own caller, for as long as it runs."
        )

    # upload_notebook genuinely awaits UploadFile.read and must stay async.
    assert inspect.iscoroutinefunction(upload_module.upload_notebook)


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


def test_resolve_generated_path_rejects_absolute_path():

    with pytest.raises(Exception):
        resolve_generated_path("/etc/passwd")


def test_resolve_generated_path_rejects_relative_traversal():

    with pytest.raises(Exception):
        resolve_generated_path("../../../../etc/passwd")


def test_resolve_generated_path_accepts_a_nested_path():

    resolved = resolve_generated_path("runtime/notebook_module.py")

    assert resolved.name == "notebook_module.py"
    assert str(resolved).startswith(os.path.abspath("generated"))


def test_upload_dir_defaults_to_uploads_without_the_env_var():
    """Run in a fresh subprocess (no NOTEBOOK_API_UPLOAD_DIR set) rather
    than asserting against the already-imported UPLOAD_DIR in this test
    process, which could be misleadingly "correct" simply because nothing
    in this test session happens to have set the env var -- the same
    care test_allowed_origins_env_var_overrides_default_list
    (test_dashboard_cors.py) already takes for GENERATED_DIR's sibling
    NOTEBOOK_API_ALLOWED_ORIGINS.
    """

    env = {k: v for k, v in os.environ.items() if k != "NOTEBOOK_API_UPLOAD_DIR"}

    proc = subprocess.run(
        [sys.executable, "-c", "from backend.routes.upload import UPLOAD_DIR; print(UPLOAD_DIR)"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "uploads"


def test_upload_dir_env_var_overrides_the_default(tmp_path):
    """Before this, UPLOAD_DIR was permanently fixed to "uploads" with no
    way for an operator to point the dashboard at a different uploads
    directory -- unlike its sibling GENERATED_DIR, which already supports
    exactly this via NOTEBOOK_API_GENERATED_DIR (see GENERATED_DIR's own
    comment in backend/routes/upload.py). A container deployment wanting
    to mount a persistent volume for uploads at a specific path, or avoid
    colliding with an "uploads" directory something else on the host
    already uses, had no way to configure that without editing source.

    Run end-to-end in a fresh subprocess (POST /api/upload through a real
    TestClient, then confirm the file landed on disk at the configured
    path) since UPLOAD_DIR's directory is created once, eagerly, at
    import time -- setting the env var only takes effect for a process
    that hasn't imported backend.routes.upload yet.
    """

    custom_dir = tmp_path / "custom_uploads_env_var_test"

    script = f"""
import io
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})

from fastapi.testclient import TestClient
from backend.dashboard import app
from backend.routes.upload import UPLOAD_DIR

assert UPLOAD_DIR == {str(custom_dir)!r}, UPLOAD_DIR

client = TestClient(app)
resp = client.post(
    "/api/upload",
    files={{"file": ("env_var_test.ipynb", io.BytesIO(b'{{"cells": [], "metadata": {{}}, "nbformat": 4, "nbformat_minor": 5}}'), "application/json")}},
)
assert resp.status_code == 200, resp.text

print("UPLOAD_DIR_ENV_OVERRIDE_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "NOTEBOOK_API_UPLOAD_DIR": str(custom_dir)},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UPLOAD_DIR_ENV_OVERRIDE_OK" in proc.stdout
    assert (custom_dir / "env_var_test.ipynb").is_file()
    # Must not have fallen back to the default "uploads" directory instead.
    assert not (PROJECT_ROOT / "uploads" / "env_var_test.ipynb").exists()


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


def test_upload_rejects_a_filename_containing_a_nested_directory():
    """Confirmed exploitable before this fix: file.filename staying
    within UPLOAD_DIR (so the traversal check above lets it through) does
    not mean it has no directory component of its own. "subdir/nb.ipynb"
    crashed upload_notebook's own os.replace(temp_path, file_path) call
    with an unhandled FileNotFoundError -- an uncaught 500, not a clean
    400 -- since nothing ever creates the intermediate "subdir/"
    directory, and even if it did, every other route that operates on an
    uploaded notebook by name (list_notebooks, get_notebook,
    delete_notebook) already assumes a flat, single-segment filename.
    """

    resp = client.post(
        "/api/upload",
        files={
            "file": (
                "nested_dir_test/nb.ipynb",
                io.BytesIO(b"data"),
                "application/json",
            )
        },
    )

    assert resp.status_code == 400
    assert not os.path.isdir("uploads/nested_dir_test")


def test_compile_rejects_a_notebook_path_containing_a_nested_directory():
    """The same flat-filename restriction applies to notebook_path on
    every route that resolves it via resolve_upload_path, not just
    upload's own file.filename -- a caller can't route around it by
    typing a nested path directly into the JSON body instead.
    """

    resp = client.post(
        "/api/compile", json={"notebook_path": "some_dir/nb.ipynb"}
    )

    assert resp.status_code == 400


def test_upload_still_accepts_a_normal_flat_filename():
    """Sanity check alongside the nested-directory rejection above: an
    ordinary, single-segment filename must still upload successfully.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    resp = client.post(
        "/api/upload",
        files={
            "file": (
                "flat_filename_sanity_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )

    assert resp.status_code == 200


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


def test_upload_sweeps_a_stale_leftover_temp_file_from_a_previous_crashed_upload(
    monkeypatch,
):
    """A hard process crash/restart between upload_notebook creating its
    hidden ".part" temp file and finishing the request skips every one of
    upload_notebook's own cleanup paths, leaving that file behind
    permanently -- it doesn't end in ".ipynb", so GET /api/notebooks never
    lists it, and nothing else ever looked at UPLOAD_DIR for one again.
    Confirmed: before this fix, such a file just sat there forever with
    no way to reclaim the disk space short of an operator finding and
    deleting a hidden dot-file on the server by hand.
    """

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "STALE_UPLOAD_TEMP_FILE_SECONDS", 1)

    stale_temp_path = Path(UPLOAD_DIR) / ".crash_leftover_test.ipynb.deadbeef.part"
    stale_temp_path.write_text("leftover from a crashed upload", encoding="utf-8")

    old_time = os.path.getmtime(stale_temp_path) - 100
    os.utime(stale_temp_path, (old_time, old_time))

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    resp = client.post(
        "/api/upload",
        files={
            "file": (
                "stale_temp_sweep_trigger.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )

    assert resp.status_code == 200
    assert not stale_temp_path.exists()


def test_upload_leaves_a_recent_in_flight_temp_file_alone(monkeypatch):
    """The sweep must be age-gated, not indiscriminate -- a ".part" file
    younger than the staleness threshold could belong to a large upload
    that is itself still genuinely streaming on another concurrent
    request, and must not be deleted out from under it.
    """

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "STALE_UPLOAD_TEMP_FILE_SECONDS", 3600)

    recent_temp_path = Path(UPLOAD_DIR) / ".still_in_flight_test.ipynb.deadbeef.part"
    recent_temp_path.write_text("still streaming", encoding="utf-8")

    try:
        content = _notebook_bytes(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
        resp = client.post(
            "/api/upload",
            files={
                "file": (
                    "recent_temp_sweep_trigger.ipynb",
                    io.BytesIO(content),
                    "application/json",
                )
            },
        )

        assert resp.status_code == 200
        assert recent_temp_path.exists()
    finally:
        recent_temp_path.unlink(missing_ok=True)


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


def test_list_notebooks_reports_the_compiled_at_timestamp():
    """.compile_metadata.json already records when the compile that
    produced the current generated/ output happened (see
    write_compile_metadata in backend/compiler.py), and this endpoint
    already reads that same file to resolve currently_compiled and
    notebook_changed_since_compile -- but previously discarded
    "compiled_at" rather than returning it. Without it, a caller could
    tell *that* the currently running app might be stale (via
    notebook_changed_since_compile) but not *how* stale -- e.g. to show
    "last compiled 3 minutes ago" -- without a separate, redundant read
    of the same file.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": ("compiled_at_test.ipynb", io.BytesIO(content), "application/json")
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "compiled_at_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    with open(
        Path("generated") / COMPILE_METADATA_FILENAME, "r", encoding="utf-8"
    ) as f:
        metadata = json.load(f)

    notebooks = client.get("/api/notebooks").json()["notebooks"]
    entry = next(nb for nb in notebooks if nb["filename"] == "compiled_at_test.ipynb")

    assert entry["compiled_at"] == metadata["compiled_at"]


def test_list_notebooks_omits_compiled_at_for_a_notebook_that_is_not_currently_compiled():

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("compiled_at_a.ipynb", content_a),
        ("compiled_at_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "compiled_at_a.ipynb"}
    )
    assert compile_resp.status_code == 200

    notebooks = client.get("/api/notebooks").json()["notebooks"]
    entry_b = next(nb for nb in notebooks if nb["filename"] == "compiled_at_b.ipynb")

    assert entry_b["currently_compiled"] is False
    assert "compiled_at" not in entry_b


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
    # Never compiled -- deleting it can't have orphaned anything.
    assert delete_resp.json()["was_currently_compiled"] is False

    assert not os.path.exists(os.path.join(UPLOAD_DIR, "delete_test.ipynb"))

    list_resp = client.get("/api/notebooks")
    filenames = {nb["filename"] for nb in list_resp.json()["notebooks"]}
    assert "delete_test.ipynb" not in filenames


def test_delete_notebook_flags_was_currently_compiled_true_for_the_compiled_notebook():
    """Deleting the notebook that produced whatever's currently running in
    GENERATED_DIR doesn't touch the compiled app itself -- it keeps
    running exactly as before -- but silently orphans it: there's no
    longer an uploaded notebook a caller could re-inspect, diff, or
    recompile from to confirm what's currently being served. Before this,
    a caller had no way to know that had just happened short of a
    separate GET /api/notebooks call beforehand to check.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "delete_currently_compiled_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "delete_currently_compiled_test.ipynb"},
    )
    assert compile_resp.status_code == 200

    delete_resp = client.delete(
        "/api/notebooks/delete_currently_compiled_test.ipynb"
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["was_currently_compiled"] is True


def test_delete_notebook_flags_was_currently_compiled_false_for_an_unrelated_notebook():

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("delete_unrelated_a.ipynb", content_a),
        ("delete_unrelated_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "delete_unrelated_a.ipynb"}
    )
    assert compile_resp.status_code == 200

    delete_resp = client.delete("/api/notebooks/delete_unrelated_b.ipynb")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["was_currently_compiled"] is False


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


def test_rename_notebook_renames_the_file_on_disk():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("rename_source.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    rename_resp = client.patch(
        "/api/notebooks/rename_source.ipynb",
        json={"new_filename": "rename_target.ipynb"},
    )

    assert rename_resp.status_code == 200
    body = rename_resp.json()
    assert body["filename"] == "rename_source.ipynb"
    assert body["new_filename"] == "rename_target.ipynb"
    assert body["was_currently_compiled"] is False

    assert not (Path(UPLOAD_DIR) / "rename_source.ipynb").exists()
    assert (Path(UPLOAD_DIR) / "rename_target.ipynb").is_file()

    filenames = {nb["filename"] for nb in client.get("/api/notebooks").json()["notebooks"]}
    assert "rename_source.ipynb" not in filenames
    assert "rename_target.ipynb" in filenames

    os.remove(Path(UPLOAD_DIR) / "rename_target.ipynb")


def test_rename_notebook_returns_404_for_missing_file():

    resp = client.patch(
        "/api/notebooks/does_not_exist_at_all.ipynb",
        json={"new_filename": "whatever.ipynb"},
    )

    assert resp.status_code == 404


def test_rename_notebook_requires_new_filename():

    content = _notebook_bytes("def add(a, b):\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("rename_missing_target.ipynb", io.BytesIO(content), "application/json")},
    )

    resp = client.patch(
        "/api/notebooks/rename_missing_target.ipynb",
        json={},
    )

    assert resp.status_code == 400


def test_rename_notebook_rejects_a_non_ipynb_target_name():

    content = _notebook_bytes("def add(a, b):\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("rename_bad_ext.ipynb", io.BytesIO(content), "application/json")},
    )

    resp = client.patch(
        "/api/notebooks/rename_bad_ext.ipynb",
        json={"new_filename": "rename_bad_ext.txt"},
    )

    assert resp.status_code == 400


def test_rename_notebook_rejects_a_traversal_target_name():

    content = _notebook_bytes("def add(a, b):\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("rename_traversal_source.ipynb", io.BytesIO(content), "application/json")},
    )

    resp = client.patch(
        "/api/notebooks/rename_traversal_source.ipynb",
        json={"new_filename": "../../../../etc/passwd.ipynb"},
    )

    assert resp.status_code == 400
    assert (Path(UPLOAD_DIR) / "rename_traversal_source.ipynb").is_file()


def test_rename_notebook_to_its_own_name_is_a_no_op():

    content = _notebook_bytes("def add(a, b):\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("rename_noop.ipynb", io.BytesIO(content), "application/json")},
    )

    resp = client.patch(
        "/api/notebooks/rename_noop.ipynb",
        json={"new_filename": "rename_noop.ipynb"},
    )

    assert resp.status_code == 200
    assert resp.json()["was_currently_compiled"] is False
    assert (Path(UPLOAD_DIR) / "rename_noop.ipynb").is_file()


def test_rename_notebook_rejects_collision_without_overwrite():

    content_a = _notebook_bytes("def add(a, b):\n    return a + b\n")
    content_b = _notebook_bytes("def sub(a, b):\n    return a - b\n")

    client.post(
        "/api/upload",
        files={"file": ("rename_collision_a.ipynb", io.BytesIO(content_a), "application/json")},
    )
    client.post(
        "/api/upload",
        files={"file": ("rename_collision_b.ipynb", io.BytesIO(content_b), "application/json")},
    )

    resp = client.patch(
        "/api/notebooks/rename_collision_a.ipynb",
        json={"new_filename": "rename_collision_b.ipynb"},
    )

    assert resp.status_code == 409
    # Neither file should have moved.
    assert (Path(UPLOAD_DIR) / "rename_collision_a.ipynb").is_file()
    assert json.loads((Path(UPLOAD_DIR) / "rename_collision_b.ipynb").read_bytes()) == json.loads(content_b)


def test_rename_notebook_overwrites_when_requested():

    content_a = _notebook_bytes("def add(a, b):\n    return a + b\n")
    content_b = _notebook_bytes("def sub(a, b):\n    return a - b\n")

    client.post(
        "/api/upload",
        files={"file": ("rename_overwrite_a.ipynb", io.BytesIO(content_a), "application/json")},
    )
    client.post(
        "/api/upload",
        files={"file": ("rename_overwrite_b.ipynb", io.BytesIO(content_b), "application/json")},
    )

    resp = client.patch(
        "/api/notebooks/rename_overwrite_a.ipynb",
        json={"new_filename": "rename_overwrite_b.ipynb", "overwrite": True},
    )

    assert resp.status_code == 200
    assert not (Path(UPLOAD_DIR) / "rename_overwrite_a.ipynb").exists()
    assert json.loads((Path(UPLOAD_DIR) / "rename_overwrite_b.ipynb").read_bytes()) == json.loads(content_a)


def test_rename_notebook_keeps_currently_compiled_tracking_under_the_new_name():
    """The gap this closes: deleting and re-uploading the currently-
    compiled notebook under a new name left .compile_metadata.json's
    "source_notebook" pointing at a path that no longer existed, so every
    uploaded notebook -- including the freshly re-uploaded one -- reported
    "currently_compiled": false afterward, with no way to tell which
    notebook (if any) actually produced what's still running in
    GENERATED_DIR. Renaming in place must not have the same failure mode.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("rename_compiled_source.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "rename_compiled_source.ipynb"}
    )
    assert compile_resp.status_code == 200

    rename_resp = client.patch(
        "/api/notebooks/rename_compiled_source.ipynb",
        json={"new_filename": "rename_compiled_target.ipynb"},
    )

    assert rename_resp.status_code == 200
    assert rename_resp.json()["was_currently_compiled"] is True

    notebooks = {
        nb["filename"]: nb for nb in client.get("/api/notebooks").json()["notebooks"]
    }
    assert notebooks["rename_compiled_target.ipynb"]["currently_compiled"] is True
    assert (
        notebooks["rename_compiled_target.ipynb"]["notebook_changed_since_compile"]
        is False
    )

    os.remove(Path(UPLOAD_DIR) / "rename_compiled_target.ipynb")


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


@pytest.mark.parametrize(
    "bad_notebook_path", [123, 1.5, True, ["a.ipynb"], {"path": "a.ipynb"}]
)
def test_inspect_rejects_a_non_string_notebook_path(bad_notebook_path):
    """Confirmed exploitable before this fix: "notebook_path" arrives as a
    raw JSON body field, not a Pydantic-validated string, so a caller can
    send any JSON type there. Path(123) raises a bare TypeError nothing
    here caught, crashing the request with an unhandled 500 instead of
    the same clean 400 a malformed *string* path already got (see
    test_inspect_rejects_absolute_notebook_path above).
    """

    resp = client.post(
        "/api/inspect", json={"notebook_path": bad_notebook_path}
    )

    assert resp.status_code == 400


@pytest.mark.parametrize(
    "bad_notebook_path", [123, 1.5, True, ["a.ipynb"], {"path": "a.ipynb"}]
)
def test_compile_rejects_a_non_string_notebook_path(bad_notebook_path):

    resp = client.post(
        "/api/compile", json={"notebook_path": bad_notebook_path}
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


def test_compile_response_reports_the_dependencies_actually_pinned_in_requirements_txt():
    """Before this, /api/compile's response had no "dependencies" field
    at all -- a dashboard frontend showing "here's what your notebook
    compiled into" had no way to say what would actually get installed
    into the Docker image (`deploy`/`docker build`'s `pip install -r
    requirements.txt`) without a separate, redundant POST /api/inspect
    call right after compiling.
    """

    content = _notebook_bytes(
        "import pandas as pd\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_dependencies_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "compile_dependencies_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    assert "pandas" in compile_resp.json()["dependencies"]


def test_compile_response_lists_the_generated_files_it_just_wrote():
    """Same gap as "dependencies" above, for the files this compile
    actually produced (app.py, requirements.txt, Dockerfile, ...) -- the
    same "generated_files" field GET /api/download's zip and
    /api/inspect's preview already expose, now also available from the
    compile response itself instead of requiring a follow-up call.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_generated_files_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "compile_generated_files_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    generated_files = compile_resp.json()["generated_files"]

    assert "app.py" in generated_files
    assert "requirements.txt" in generated_files
    assert "Dockerfile" in generated_files


def test_compile_reports_skipped_functions():
    """Before this, a function that couldn't be turned into an endpoint
    (e.g. one taking **kwargs) just silently had no corresponding route in
    /api/compile's response, with nothing to explain why -- the same gap
    /api/inspect's "skipped_functions" field closes for the pre-compile
    preview.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def unsupported(a, **kwargs):\n    return a\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_skipped_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "compile_skipped_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    body = compile_resp.json()

    assert body["skipped_functions"] == [
        {
            "name": "unsupported",
            "reason": (
                "uses *args/**kwargs, which can't be represented as a "
                "fixed set of request fields"
            ),
        }
    ]
    assert {f["name"] for f in body["functions"]} == {"add"}


def test_inspect_reports_skipped_functions_before_compiling():

    content = _notebook_bytes(
        "class Model:\n    def predict(self, x):\n        return x\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "inspect_skipped_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    inspect_resp = client.post(
        "/api/inspect", json={"notebook_path": "inspect_skipped_test.ipynb"}
    )
    assert inspect_resp.status_code == 200

    assert inspect_resp.json()["skipped_functions"] == [
        {
            "name": "predict",
            "reason": (
                "defined inside a class or nested function, so it isn't "
                "callable as a standalone endpoint"
            ),
        }
    ]


def test_inspect_missing_notebook_still_returns_404_not_400():
    """A well-formed, in-bounds filename that simply doesn't exist should
    still 404 (existing behaviour), not be confused with a rejected path.
    """

    resp = client.post(
        "/api/inspect", json={"notebook_path": "does_not_exist_at_all.ipynb"}
    )

    assert resp.status_code == 404


def test_inspect_returns_400_not_500_for_a_malformed_notebook_file():
    """Confirmed exploitable before this fix: a notebook file that fails
    nbformat's own load/validation (invalid JSON, or valid JSON missing
    required notebook keys) is a problem with the file's content, not
    this server -- but /api/inspect reported it as a bare 500, the same
    misdiagnosis ReservedFunctionNameError's dedicated 400 already fixed
    for a different failure mode in /api/compile. /api/upload itself
    would never accept content like this, so this writes straight into
    UPLOAD_DIR to reach the endpoint with it, e.g. a file placed there
    outside the API.
    """

    filename = "malformed_inspect_test.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.post("/api/inspect", json={"notebook_path": filename})

    assert resp.status_code == 400
    assert "not a valid Jupyter notebook" in resp.json()["detail"]


def test_compile_returns_400_not_500_for_a_malformed_notebook_file():

    filename = "malformed_compile_test.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.post("/api/compile", json={"notebook_path": filename})

    assert resp.status_code == 400
    assert "not a valid Jupyter notebook" in resp.json()["detail"]


def test_inspect_returns_400_not_500_for_valid_json_missing_required_notebook_keys():
    """Distinct failure mode from the invalid-JSON case above: valid JSON
    that isn't a valid notebook (e.g. no "cells" key) raises nbformat's
    ValidationError, not NotJSONError -- both must be treated the same
    way.
    """

    filename = "missing_keys_inspect_test.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"not_a_notebook": True}))

    resp = client.post("/api/inspect", json={"notebook_path": filename})

    assert resp.status_code == 400
    assert "not a valid Jupyter notebook" in resp.json()["detail"]


def test_inspect_reports_dependencies_and_generated_files_after_a_compile():
    """/api/inspect previously only ever returned "functions", even though
    inspect_notebook_data (backend/inspector.py) already computed
    dependencies and generated_files -- it just wasn't wired to this
    route.

    Includes a standard-library import ("math") alongside the real
    third-party one specifically to also confirm "dependencies" only ever
    reports what actually gets pinned into requirements.txt -- "math"
    never does (see _third_party_dependencies in backend/inspector.py),
    so it must not appear here either.
    """

    content = _notebook_bytes(
        "import math\n"
        "import pandas as pd\n\n"
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
    assert body["dependencies"] == ["pandas"]
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


def test_compile_respects_a_configured_generated_dir(tmp_path, monkeypatch):
    """POST /api/compile previously always wrote to a hardcoded "generated"
    string, ignoring GENERATED_DIR entirely -- every other endpoint that
    reads compiled output (list_notebooks' currently_compiled check,
    /api/export-openapi, /api/export-sdk, /api/deploy, /api/download)
    already honored GENERATED_DIR (now configurable via
    NOTEBOOK_API_GENERATED_DIR), so pointing it elsewhere would have
    silently only taken effect for those, while /api/compile kept writing
    to "generated/" regardless -- the two would disagree about where the
    compiled app actually lives.
    """

    from backend.routes import upload as upload_module

    custom_dir = tmp_path / "custom_generated"
    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(custom_dir))

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "custom_generated_dir_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "custom_generated_dir_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    assert (custom_dir / "app.py").is_file()
    assert (custom_dir / "requirements.txt").is_file()
    assert (custom_dir / COMPILE_METADATA_FILENAME).is_file()


def test_compile_into_a_custom_generated_dir_is_visible_to_other_endpoints(
    tmp_path, monkeypatch
):
    """End-to-end proof that /api/compile and the rest of the dashboard API
    agree on where the compiled app lives once GENERATED_DIR is
    configured: list_notebooks' currently_compiled check and /api/download
    both read from GENERATED_DIR, so if /api/compile had still written to
    the hardcoded "generated/" instead, neither would ever find it.
    """

    from backend.routes import upload as upload_module

    custom_dir = tmp_path / "custom_generated_2"
    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(custom_dir))

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    filename = "custom_generated_dir_consistency_test.ipynb"

    upload_resp = client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post("/api/compile", json={"notebook_path": filename})
    assert compile_resp.status_code == 200

    notebooks_resp = client.get("/api/notebooks")
    assert notebooks_resp.status_code == 200
    entry = next(
        n for n in notebooks_resp.json()["notebooks"] if n["filename"] == filename
    )
    assert entry["currently_compiled"] is True

    download_resp = client.get("/api/download")
    assert download_resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(download_resp.content))
    assert "app.py" in archive.namelist()


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


def test_export_openapi_reflects_a_recompile_within_the_same_process():
    """Confirmed exploitable before this fix: export_openapi_schema
    (backend/exporters/openapi_exporter.py) imports "<package_name>.app"
    with plain importlib.import_module, which Python resolves from
    sys.modules -- not from disk -- once that name has already been
    imported in this process. The dashboard is exactly that kind of
    long-running process, so the second /api/compile -> /api/export-openapi
    round trip silently returned the *first* compile's schema: compiling a
    notebook exposing `add`, exporting it, then recompiling the same
    upload to expose `multiply` instead and exporting again still returned
    `add` in the schema's paths, with the freshly-written app.py on disk
    never actually read.
    """

    filename = "reimport_staleness_test.ipynb"

    first_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    upload_resp = client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(first_content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post("/api/compile", json={"notebook_path": filename})
    assert compile_resp.status_code == 200

    first_export = client.post("/api/export-openapi", json={"format": "json"})
    assert first_export.status_code == 200
    assert "/add" in first_export.json()["schema"]["paths"]

    second_content = _notebook_bytes(
        "def multiply(a: int, b: int) -> int:\n    return a * b\n"
    )
    overwrite_resp = client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(second_content), "application/json")},
    )
    assert overwrite_resp.status_code == 200

    recompile_resp = client.post("/api/compile", json={"notebook_path": filename})
    assert recompile_resp.status_code == 200

    second_export = client.post("/api/export-openapi", json={"format": "json"})
    assert second_export.status_code == 200
    second_paths = second_export.json()["schema"]["paths"]

    assert "/multiply" in second_paths
    assert "/add" not in second_paths


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


def test_export_sdk_reports_a_clean_400_for_a_corrupt_openapi_schema(monkeypatch, tmp_path):
    """Confirmed exploitable before this fix: _load_openapi_schema
    (exporters/sdk_generator.py) raises ValueError for content that isn't
    valid JSON, but this endpoint's except clause only ever caught bare
    Exception and wrapped it in a 500 -- indistinguishable from an actual
    server-side bug, unlike every other malformed-client-input case in
    this codebase (a bad language, a missing export, ...). "openapi.json"
    existing but being corrupt (e.g. a truncated write) is squarely the
    client-visible state's own problem, not this server's.
    """

    from backend.routes import upload as upload_module

    isolated_dir = tmp_path / "generated_export_sdk_corrupt_test"
    isolated_dir.mkdir()
    (isolated_dir / "openapi.json").write_text("not json at all", encoding="utf-8")

    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(isolated_dir))

    resp = client.post("/api/export-sdk", json={"language": "python"})

    assert resp.status_code == 400
    assert "not a valid OpenAPI JSON schema" in resp.json()["detail"]


def test_export_sdk_gives_a_clean_400_not_a_misleading_404_for_a_yaml_only_export(
    monkeypatch, tmp_path
):
    """Confirmed exploitable before this fix: POST /api/export-sdk only
    ever checked for "openapi.json", hardcoded -- but POST
    /api/export-openapi is just as capable of writing "openapi.yaml"
    instead, via {"format": "yaml"}. A caller who did exactly that, then
    called export-sdk, got a 404 saying "No exported OpenAPI schema
    found. Run /api/export-openapi first" -- flatly wrong, since they
    just had, in the only other format this same API offers -- instead of
    the clean 400 with a specific "re-export with format=json" hint
    _load_openapi_schema (exporters/sdk_generator.py) already writes for
    exactly this situation, and which the CLI's own `export-sdk --openapi
    generated/openapi.yaml` could already reach.
    """

    from backend.routes import upload as upload_module

    isolated_dir = tmp_path / "generated_export_sdk_yaml_only_test"
    isolated_dir.mkdir()
    (isolated_dir / "openapi.yaml").write_text(
        "openapi: 3.0.0\ninfo:\n  title: Test\n  version: '1.0'\npaths: {}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(isolated_dir))

    resp = client.post("/api/export-sdk", json={"language": "python"})

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "YAML export" in detail
    assert "format json" in detail or "--format json" in detail


def test_export_sdk_prefers_a_json_export_over_a_yaml_one_when_both_exist(
    monkeypatch, tmp_path
):
    """If a caller exported both formats (e.g. json first, then yaml for
    a human-readable copy), export-sdk must still read the json export --
    the one it actually knows how to parse -- rather than picking
    whichever file the directory listing happens to prefer.
    """

    from backend.routes import upload as upload_module

    isolated_dir = tmp_path / "generated_export_sdk_prefers_json_test"
    isolated_dir.mkdir()
    (isolated_dir / "openapi.json").write_text("{}", encoding="utf-8")
    # Deliberately not valid JSON -- if export-sdk picked this file
    # instead, _load_openapi_schema would raise and this test would see a
    # 400, not the 200 a real "both exports present" caller expects.
    (isolated_dir / "openapi.yaml").write_text(
        "openapi: 3.0.0\ninfo:\n  title: Test\n  version: '1.0'\npaths: {}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(isolated_dir))

    resp = client.post("/api/export-sdk", json={"language": "python"})

    assert resp.status_code == 200
    assert "class NotebookAPIClient" in resp.json()["code"]


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


def test_deploy_endpoint_returns_409_when_the_compiled_notebook_is_stale(
    tmp_path, monkeypatch
):
    """Unlike the CLI's `deploy` command -- which always recompiles from
    the notebook as its own first step, so it can never build a stale
    image -- /api/deploy builds whatever is already sitting in
    GENERATED_DIR from an earlier, separate /api/compile call. Before
    this, editing the notebook after that compile (e.g. via
    /api/upload?overwrite=true) without recompiling went completely
    unchecked: this could silently build (and, with "push": true,
    publish) a Docker image reflecting outdated code -- the exact
    staleness list_notebooks' notebook_changed_since_compile field
    already exists to warn about, just never enforced here.
    """

    filename = "deploy_stale_test.ipynb"
    _compile_a_notebook(filename)

    changed_content = _notebook_bytes(
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )
    overwrite_resp = client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(changed_content), "application/json")},
    )
    assert overwrite_resp.status_code == 200

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 409
    assert "edited since the last compile" in resp.json()["detail"]
    # Docker must never have been invoked at all.
    assert not log_path.exists()


def test_deploy_endpoint_force_true_deploys_a_stale_build_anyway(tmp_path, monkeypatch):

    filename = "deploy_stale_force_test.ipynb"
    _compile_a_notebook(filename)

    changed_content = _notebook_bytes(
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )
    overwrite_resp = client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(changed_content), "application/json")},
    )
    assert overwrite_resp.status_code == 200

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={"force": True})

    assert resp.status_code == 200
    assert log_path.exists()


def test_deploy_endpoint_does_not_require_force_when_the_notebook_is_unchanged(
    tmp_path, monkeypatch
):

    _compile_a_notebook("deploy_not_stale_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 200
    assert log_path.exists()


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


def test_deploy_endpoint_respects_custom_platform(tmp_path, monkeypatch):
    """`docker build`'s own default target platform is the local Docker
    daemon's host architecture -- not necessarily the deploy target's
    (almost every cloud PaaS runs linux/amd64). Before "platform" existed
    here, the dashboard's /api/deploy had no way to override it at all.
    """

    _compile_a_notebook("deploy_platform_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={"platform": "linux/amd64"})

    assert resp.status_code == 200

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    build_call = calls[0].splitlines()
    assert build_call[:-1] == [
        "build", "-t", "generated:latest", "--platform", "linux/amd64", ".",
    ]


def test_deploy_endpoint_omits_platform_flag_by_default(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_no_platform_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={})

    assert resp.status_code == 200

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    build_call = calls[0].splitlines()
    assert "--platform" not in build_call


@pytest.mark.parametrize("bad_platform", [123, 1.5, ["linux/amd64"], {"platform": "linux/amd64"}])
def test_deploy_endpoint_rejects_a_non_string_platform(bad_platform):
    """Mirrors test_deploy_endpoint_rejects_a_non_string_tag: "platform"
    flows into the same subprocess argument list "tag" does.
    """

    _compile_a_notebook("deploy_bad_platform_test.ipynb")

    resp = client.post("/api/deploy", json={"platform": bad_platform})

    assert resp.status_code == 400


@pytest.mark.parametrize("bad_tag", [123, 1.5, ["myapp:v1"], {"tag": "myapp:v1"}])
def test_deploy_endpoint_rejects_a_non_string_tag(bad_tag):
    """Confirmed exploitable before this fix: "tag" flows straight into a
    `docker build`/`docker push` subprocess argument list -- subprocess.run
    requires every element to be str/bytes/PathLike, so a non-string tag
    crashed with an unhandled TypeError from deep inside subprocess
    internals instead of the same clean 400 a legitimate string tag
    already validates fine with.
    """

    _compile_a_notebook("deploy_bad_tag_test.ipynb")

    resp = client.post("/api/deploy", json={"tag": bad_tag})

    assert resp.status_code == 400


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


def test_download_excludes_pycache_from_the_zip():
    """__pycache__ is created by Python itself the first time the compiled
    app or its runtime module gets imported (e.g. by a prior
    /api/export-openapi call) -- it is not part of what the compiler
    actually wrote, and its .pyc filenames are tied to whichever Python
    version happened to import it. Before this fix, the downloaded
    "compiled app" bundle could ship this non-portable bytecode cache
    alongside the actual deliverable.
    """

    _compile_a_notebook("download_pycache_test.ipynb")

    generated_dir = Path("generated")
    pycache_dir = generated_dir / "__pycache__"
    nested_pycache_dir = generated_dir / "runtime" / "__pycache__"

    try:
        pycache_dir.mkdir(exist_ok=True)
        (pycache_dir / "app.cpython-314.pyc").write_bytes(b"\x00")

        nested_pycache_dir.mkdir(parents=True, exist_ok=True)
        (nested_pycache_dir / "notebook_module.cpython-314.pyc").write_bytes(b"\x00")

        download_resp = client.get("/api/download")

        assert download_resp.status_code == 200

        archive = zipfile.ZipFile(io.BytesIO(download_resp.content))
        names = archive.namelist()

        assert "app.py" in names
        assert not any("__pycache__" in name for name in names)
        assert not any(name.endswith(".pyc") for name in names)
    finally:
        shutil.rmtree(pycache_dir, ignore_errors=True)
        shutil.rmtree(nested_pycache_dir, ignore_errors=True)


def test_download_excludes_compile_metadata_from_the_zip():
    """.compile_metadata.json (write_compile_metadata, backend/compiler.py)
    is dashboard-internal bookkeeping, not a compiled deliverable -- and
    its "source_notebook" field is the source notebook's absolute
    filesystem path on the compiling server. Before this fix, it was
    written into GENERATED_DIR by every compile like any other file, so it
    was zipped up and handed back by this endpoint too, alongside the
    actual deliverable.
    """

    _compile_a_notebook("download_compile_metadata_test.ipynb")

    assert (Path("generated") / ".compile_metadata.json").is_file()

    download_resp = client.get("/api/download")

    assert download_resp.status_code == 200

    archive = zipfile.ZipFile(io.BytesIO(download_resp.content))
    names = archive.namelist()

    assert "app.py" in names
    assert ".compile_metadata.json" not in names


def test_download_waits_for_an_in_flight_compile_to_release_compile_lock():
    """GET /api/download walks GENERATED_DIR to build its zip -- without
    holding COMPILE_LOCK (see backend/compiler.py) for that walk, a
    concurrent POST /api/compile racing it on another thread (both run in
    FastAPI's worker threadpool -- see the plain `def` routes in this
    module) could rewrite files out from under it mid-zip, downloading a
    torn mix of the old and new compile. Verified by holding the lock
    from a background thread and confirming this request doesn't return
    until it's released.
    """

    _compile_a_notebook("download_lock_test.ipynb")

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with COMPILE_LOCK:
            lock_acquired.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)

    result = {}

    def do_download():
        result["resp"] = client.get("/api/download")

    request_thread = threading.Thread(target=do_download)
    request_thread.start()
    request_thread.join(timeout=0.3)

    assert request_thread.is_alive(), (
        "GET /api/download should still be blocked on COMPILE_LOCK"
    )

    release_lock.set()
    request_thread.join(timeout=5)
    holder.join(timeout=5)

    assert not request_thread.is_alive()
    assert result["resp"].status_code == 200


def test_list_generated_files_returns_empty_before_any_compile(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_list_files_test_missing_dir"
    )

    resp = client.get("/api/generated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["generated_files"] == []
    assert body["compiled_at"] is None
    assert body["source_notebook_filename"] is None
    assert body["source_notebook_exists"] is False


def test_list_generated_files_lists_the_compiled_output():

    _compile_a_notebook("list_generated_files_test.ipynb")

    resp = client.get("/api/generated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "app.py" in body["generated_files"]
    assert "requirements.txt" in body["generated_files"]
    assert body["compiled_at"] is not None
    assert body["source_notebook_filename"] == "list_generated_files_test.ipynb"
    assert body["source_notebook_exists"] is True

    os.remove(Path(UPLOAD_DIR) / "list_generated_files_test.ipynb")


def test_list_generated_files_excludes_pycache_and_compile_metadata():
    """Same exclusions GET /api/download's zip and GET
    /api/generated/{filename} already apply -- __pycache__ is a Python-
    created implementation artifact never written by the compiler, and
    .compile_metadata.json is dashboard-internal bookkeeping whose
    "source_notebook" field is an absolute filesystem path on the
    compiling server, not a compiled deliverable this listing should
    expose.
    """

    from backend.routes import upload as upload_module

    filename = "list_generated_files_exclusions_test.ipynb"
    _compile_a_notebook(filename)

    pycache_dir = Path(upload_module.GENERATED_DIR) / "__pycache__"
    pycache_dir.mkdir(exist_ok=True)
    (pycache_dir / "app.cpython-000.pyc").write_bytes(b"")

    resp = client.get("/api/generated")

    assert resp.status_code == 200
    body = resp.json()
    assert not any("__pycache__" in f for f in body["generated_files"])
    assert COMPILE_METADATA_FILENAME not in body["generated_files"]

    shutil.rmtree(pycache_dir)
    os.remove(Path(UPLOAD_DIR) / filename)


def test_list_generated_files_reports_the_notebook_still_exists_after_deleting_an_unrelated_one():

    filename = "list_generated_files_survives_test.ipynb"
    _compile_a_notebook(filename)

    other_content = _notebook_bytes("def sub(a, b):\n    return a - b\n")
    client.post(
        "/api/upload",
        files={
            "file": (
                "list_generated_files_unrelated.ipynb",
                io.BytesIO(other_content),
                "application/json",
            )
        },
    )

    delete_resp = client.delete("/api/notebooks/list_generated_files_unrelated.ipynb")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["was_currently_compiled"] is False

    resp = client.get("/api/generated")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_notebook_filename"] == filename
    assert body["source_notebook_exists"] is True

    os.remove(Path(UPLOAD_DIR) / filename)


def test_list_generated_files_reports_source_notebook_gone_after_it_is_deleted():
    """The gap this endpoint closes: deleting the notebook that produced
    GENERATED_DIR's current contents (DELETE /api/notebooks/{filename},
    "was_currently_compiled": true) doesn't touch GENERATED_DIR at all --
    the compiled app keeps running exactly as before -- but previously
    left no way to even list what's still in it, short of GET
    /api/download's opaque zip bytes or already knowing an exact filename
    to pass GET /api/generated/{filename}.
    """

    filename = "list_generated_files_orphan_test.ipynb"
    _compile_a_notebook(filename)

    delete_resp = client.delete(f"/api/notebooks/{filename}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["was_currently_compiled"] is True

    resp = client.get("/api/generated")

    assert resp.status_code == 200
    body = resp.json()
    # The generated app itself is untouched by deleting its source
    # notebook -- it's still fully listable.
    assert "app.py" in body["generated_files"]
    assert body["source_notebook_filename"] == filename
    assert body["source_notebook_exists"] is False


def test_delete_generated_app_returns_404_when_nothing_compiled_yet(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_delete_test_missing_dir"
    )

    resp = client.delete("/api/generated")

    assert resp.status_code == 404


def test_delete_generated_app_removes_the_generated_directory(tmp_path, monkeypatch):
    """Before this, the only ways to make GENERATED_DIR empty again were
    to delete it by hand on the server's filesystem, or to recompile some
    other notebook over it -- which still leaves *a* compiled app sitting
    there, just a different one. An operator who wants to actually
    reclaim the disk space or reset the dashboard to a clean slate had no
    endpoint to call for it.
    """

    from backend.routes import upload as upload_module

    custom_dir = tmp_path / "generated_delete_test"
    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(custom_dir))

    filename = "delete_generated_app_test.ipynb"
    _compile_a_notebook(filename)

    assert custom_dir.is_dir()

    resp = client.delete("/api/generated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["generated_dir"] == str(custom_dir)
    assert not custom_dir.exists()

    os.remove(Path(UPLOAD_DIR) / filename)


def test_delete_generated_app_resets_list_generated_files_to_empty(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    custom_dir = tmp_path / "generated_delete_reset_test"
    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(custom_dir))

    filename = "delete_generated_app_reset_test.ipynb"
    _compile_a_notebook(filename)

    delete_resp = client.delete("/api/generated")
    assert delete_resp.status_code == 200

    list_resp = client.get("/api/generated")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["generated_files"] == []
    assert body["compiled_at"] is None
    assert body["source_notebook_filename"] is None
    assert body["source_notebook_exists"] is False

    os.remove(Path(UPLOAD_DIR) / filename)


def test_delete_generated_app_evicts_the_compiled_app_from_the_module_cache():
    """Confirmed exploitable for a different endpoint before
    _evict_compiled_app_from_module_cache existed (see its own docstring
    on POST /api/export-openapi): plain importlib.import_module resolves
    an already-imported package straight from sys.modules, not from disk.
    Once GENERATED_DIR is deleted entirely, a cached import of it in this
    long-running dashboard process no longer corresponds to anything on
    disk at all -- the same staleness class /api/export-openapi's own
    eviction already guards against, just total instead of partial.

    Deliberately exercises the real, default GENERATED_DIR ("generated")
    rather than an isolated tmp_path one: export_openapi_schema imports
    "<package_name>.app" via plain importlib.import_module, which only
    resolves at all when GENERATED_DIR's parent is already on sys.path --
    true for the project root every other export-openapi test in this file
    already relies on, not for an arbitrary tmp_path directory.
    """

    import sys

    from backend.routes import upload as upload_module

    filename = "delete_generated_app_evict_test.ipynb"
    _compile_a_notebook(filename)

    # POST /api/export-openapi already imports "<package_name>.app" into
    # sys.modules as part of exporting the schema.
    export_resp = client.post("/api/export-openapi", json={"format": "json"})
    assert export_resp.status_code == 200

    package_name = upload_module.package_name_for_output_dir(
        upload_module.GENERATED_DIR
    )
    assert package_name in sys.modules

    delete_resp = client.delete("/api/generated")
    assert delete_resp.status_code == 200

    assert package_name not in sys.modules
    assert not any(
        name == package_name or name.startswith(f"{package_name}.")
        for name in sys.modules
    )

    os.remove(Path(UPLOAD_DIR) / filename)


def test_get_generated_file_returns_404_when_nothing_compiled_yet(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(
        upload_module, "GENERATED_DIR", "generated_get_file_test_missing_dir"
    )

    resp = client.get("/api/generated/app.py")

    assert resp.status_code == 404


def test_get_generated_file_returns_app_py_content():
    """GET /api/download already lets a caller retrieve the whole compiled
    output as a zip, and inspect_notebook_data's "generated_files" field
    already lists what's in it by name -- but before this, there was no
    way to read any *one* of those files' actual content back through the
    API: a dashboard wanting to preview "here's the app.py you're about
    to deploy" had no choice but to download and unzip the entire bundle
    client-side just to show a single file.
    """

    _compile_a_notebook("get_file_app_py_test.ipynb")

    resp = client.get("/api/generated/app.py")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["filename"] == "app.py"
    assert "def add(" in body["content"]


def test_get_generated_file_returns_requirements_txt_content():

    _compile_a_notebook("get_file_requirements_test.ipynb")

    resp = client.get("/api/generated/requirements.txt")

    assert resp.status_code == 200
    body = resp.json()
    assert "fastapi" in body["content"]


def test_get_generated_file_supports_nested_paths():
    """The runtime module lives under a subdirectory ("runtime/
    notebook_module.py"), not directly in GENERATED_DIR -- the route must
    accept a nested path as a single parameter, not just a bare filename,
    the way GET /api/notebooks/{filename} does.
    """

    _compile_a_notebook("get_file_nested_test.ipynb")

    resp = client.get("/api/generated/runtime/notebook_module.py")

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "runtime/notebook_module.py"
    assert "def add(" in body["content"]


def test_get_generated_file_returns_404_for_a_file_that_does_not_exist():

    _compile_a_notebook("get_file_missing_test.ipynb")

    resp = client.get("/api/generated/does_not_exist.txt")

    assert resp.status_code == 404


def test_get_generated_file_rejects_absolute_path():

    _compile_a_notebook("get_file_absolute_test.ipynb")

    resp = client.get("/api/generated//etc/passwd")

    assert resp.status_code in (400, 404)


def test_get_generated_file_rejects_relative_traversal():
    """Confirmed exploitable before resolve_generated_path existed: a
    filename like "../../etc/passwd" would resolve outside GENERATED_DIR
    entirely, the same traversal hazard resolve_upload_path already
    guards against for UPLOAD_DIR.
    """

    _compile_a_notebook("get_file_traversal_test.ipynb")

    resp = client.get("/api/generated/../../../../etc/passwd")

    assert resp.status_code in (400, 404)
    assert "root:" not in resp.text


def test_get_generated_file_excludes_pycache():
    """Same __pycache__ exclusion GET /api/download and
    inspect_notebook_data's "generated_files" field already apply (see
    EXCLUDED_GENERATED_DIR_NAMES) -- it's a Python-created implementation
    artifact never actually written by the compiler, not a real
    deliverable this endpoint should serve back.
    """

    _compile_a_notebook("get_file_pycache_test.ipynb")

    generated_dir = Path("generated")
    pycache_dir = generated_dir / "__pycache__"

    try:
        pycache_dir.mkdir(exist_ok=True)
        (pycache_dir / "app.cpython-314.pyc").write_bytes(b"\x00")

        resp = client.get("/api/generated/__pycache__/app.cpython-314.pyc")

        assert resp.status_code == 404
    finally:
        shutil.rmtree(pycache_dir, ignore_errors=True)


def test_get_generated_file_excludes_compile_metadata():
    """.compile_metadata.json (write_compile_metadata, backend/compiler.py)
    is dashboard-internal bookkeeping, never a compiled deliverable this
    endpoint should serve back -- and its "source_notebook" field is the
    source notebook's absolute filesystem path on the compiling server, so
    serving it back would leak server-side filesystem layout to any caller
    who guesses the filename.
    """

    _compile_a_notebook("get_file_compile_metadata_test.ipynb")

    assert (Path("generated") / ".compile_metadata.json").is_file()

    resp = client.get("/api/generated/.compile_metadata.json")

    assert resp.status_code == 404


def test_get_generated_file_waits_for_an_in_flight_compile_to_release_compile_lock():
    """GET /api/generated/{filename} reads a file out of GENERATED_DIR --
    without holding COMPILE_LOCK (see backend/compiler.py) for that read,
    a concurrent POST /api/compile racing it on another thread could
    rewrite that exact file out from under it mid-read, the same hazard
    already guarded against for GET /api/download's zip walk.
    """

    _compile_a_notebook("get_file_lock_test.ipynb")

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with COMPILE_LOCK:
            lock_acquired.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)

    result = {}

    def do_get():
        result["resp"] = client.get("/api/generated/app.py")

    request_thread = threading.Thread(target=do_get)
    request_thread.start()
    request_thread.join(timeout=0.3)

    assert request_thread.is_alive(), (
        "GET /api/generated/{filename} should still be blocked on COMPILE_LOCK"
    )

    release_lock.set()
    request_thread.join(timeout=5)
    holder.join(timeout=5)

    assert not request_thread.is_alive()
    assert result["resp"].status_code == 200


def test_export_openapi_waits_for_an_in_flight_compile_to_release_compile_lock():
    """POST /api/export-openapi dynamically imports "<package_name>.app"
    -- without holding COMPILE_LOCK for that import, a concurrent POST
    /api/compile racing it on another thread could rewrite app.py (and
    the runtime module it imports) mid-import, importing a torn mix of
    the old and new compile instead of a consistent one.
    """

    _compile_a_notebook("export_openapi_lock_test.ipynb")

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with COMPILE_LOCK:
            lock_acquired.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)

    result = {}

    def do_export():
        result["resp"] = client.post("/api/export-openapi", json={})

    request_thread = threading.Thread(target=do_export)
    request_thread.start()
    request_thread.join(timeout=0.3)

    assert request_thread.is_alive(), (
        "POST /api/export-openapi should still be blocked on COMPILE_LOCK"
    )

    release_lock.set()
    request_thread.join(timeout=5)
    holder.join(timeout=5)

    assert not request_thread.is_alive()
    assert result["resp"].status_code == 200


def test_health_check_reports_no_compiled_app_before_anything_has_been_compiled(
    monkeypatch, tmp_path
):
    """Before this, GET /api/health returned the exact same static body
    whether or not a notebook had ever been compiled -- a readinessProbe
    pointed at it could only ever confirm the process itself was up, not
    that it actually had a compiled app ready to serve traffic for.
    """

    from backend.routes import upload as upload_module

    empty_dir = tmp_path / "generated_health_empty_test"
    empty_dir.mkdir()

    monkeypatch.setattr(upload_module, "GENERATED_DIR", str(empty_dir))

    resp = client.get("/api/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["compiled_app_present"] is False
    assert body["compiled_at"] is None


def test_health_check_reports_a_compiled_app_and_its_compiled_at_timestamp():

    _compile_a_notebook("health_check_compiled_test.ipynb")

    resp = client.get("/api/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["compiled_app_present"] is True
    assert body["compiled_at"] is not None


def test_health_check_never_leaks_the_source_notebooks_server_side_filesystem_path():
    """.compile_metadata.json's "source_notebook" field is the source
    notebook's absolute filesystem path on the compiling server -- the
    same field EXCLUDED_GENERATED_FILE_NAMES already keeps out of GET
    /api/download and GET /api/generated/{filename}. A health probe,
    polled by infrastructure outside this tool's own trust boundary, has
    even less business exposing that than an authenticated dashboard
    caller does.
    """

    _compile_a_notebook("health_check_no_leak_test.ipynb")

    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert "source_notebook" not in resp.json()
    assert "uploads" not in json.dumps(resp.json())


def test_export_sdk_waits_for_an_in_flight_compile_to_release_compile_lock():
    """Confirmed missing before this fix: unlike export-openapi, deploy,
    download, and get_generated_file (see the identical tests above),
    export-sdk held COMPILE_LOCK nowhere at all -- a concurrent POST
    /api/compile racing it on another thread runs
    clear_stale_export_artifacts (backend/compiler.py) as part of every
    recompile, which unlinks openapi.json/.yaml and rmtree's the sdk/
    directory. Without the lock, this could read a half-deleted openapi
    export or write its generated client into a sdk/ directory a
    concurrent recompile is simultaneously removing out from under it.
    """

    _compile_a_notebook("export_sdk_lock_test.ipynb")

    export_resp = client.post("/api/export-openapi", json={"format": "json"})
    assert export_resp.status_code == 200

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with COMPILE_LOCK:
            lock_acquired.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)

    result = {}

    def do_export():
        result["resp"] = client.post("/api/export-sdk", json={"language": "python"})

    request_thread = threading.Thread(target=do_export)
    request_thread.start()
    request_thread.join(timeout=0.3)

    assert request_thread.is_alive(), (
        "POST /api/export-sdk should still be blocked on COMPILE_LOCK"
    )

    release_lock.set()
    request_thread.join(timeout=5)
    holder.join(timeout=5)

    assert not request_thread.is_alive()
    assert result["resp"].status_code == 200


def test_inspect_waits_for_an_in_flight_compile_to_release_compile_lock():
    """Confirmed missing before this fix: unlike export-openapi,
    export-sdk, deploy, download, and get_generated_file (see the
    identical tests above), /api/inspect held COMPILE_LOCK nowhere at
    all, even though its response's "generated_files" field walks
    GENERATED_DIR the exact same way those other routes read it. A
    concurrent POST /api/compile racing it on another thread runs
    clear_stale_export_artifacts (backend/compiler.py) as part of every
    recompile, which rmtree's the sdk/ subdirectory -- without the lock,
    that walk could raise FileNotFoundError if the subdirectory
    disappeared out from under it mid-walk.
    """

    _compile_a_notebook("inspect_lock_test.ipynb")

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with COMPILE_LOCK:
            lock_acquired.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)

    result = {}

    def do_inspect():
        result["resp"] = client.post(
            "/api/inspect", json={"notebook_path": "inspect_lock_test.ipynb"}
        )

    request_thread = threading.Thread(target=do_inspect)
    request_thread.start()
    request_thread.join(timeout=0.3)

    assert request_thread.is_alive(), (
        "POST /api/inspect should still be blocked on COMPILE_LOCK"
    )

    release_lock.set()
    request_thread.join(timeout=5)
    holder.join(timeout=5)

    assert not request_thread.is_alive()
    assert result["resp"].status_code == 200


def test_compile_response_waits_for_a_release_compile_lock_before_reading_generated_files():
    """Confirmed missing before this fix: compile_notebook (called first,
    inside compile_notebook_endpoint) only holds COMPILE_LOCK for its own
    write phase, releasing it before returning -- the endpoint's
    subsequent inspect_notebook_data call (which builds the
    "dependencies"/"generated_files" fields of the response, see
    test_compile_response_reports_the_dependencies_actually_pinned_in_requirements_txt
    above) read GENERATED_DIR with no lock held at all. A concurrent
    POST /api/compile for a *different* notebook racing in that exact
    window runs clear_stale_export_artifacts as part of its own
    recompile, which rmtree's the sdk/ subdirectory -- the os.walk inside
    _list_generated_files (backend/inspector.py) can raise
    FileNotFoundError if that subdirectory disappears out from under it
    mid-walk. Held externally here, this proves the endpoint's entire
    compile-then-read lifecycle is now serialized against a concurrent
    lock holder, not just its write phase.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_lock_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with COMPILE_LOCK:
            lock_acquired.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)

    result = {}

    def do_compile():
        result["resp"] = client.post(
            "/api/compile", json={"notebook_path": "compile_lock_test.ipynb"}
        )

    request_thread = threading.Thread(target=do_compile)
    request_thread.start()
    request_thread.join(timeout=0.3)

    assert request_thread.is_alive(), (
        "POST /api/compile should still be blocked on COMPILE_LOCK"
    )

    release_lock.set()
    request_thread.join(timeout=5)
    holder.join(timeout=5)

    assert not request_thread.is_alive()
    assert result["resp"].status_code == 200
    assert "generated_files" in result["resp"].json()


def test_export_openapi_read_back_does_not_race_a_concurrent_recompiles_cleanup():
    """Confirmed exploitable before this fix: export_openapi_endpoint
    released COMPILE_LOCK right after export_openapi_schema wrote
    output_path, then read that same file back completely unlocked. A
    concurrent POST /api/compile racing in during that exact window runs
    clear_stale_export_artifacts (backend/compiler.py) as part of its own
    recompile, which unlinks openapi.json/.yaml unconditionally --
    reproduced directly against clear_stale_export_artifacts: a file
    written successfully one moment raised a bare FileNotFoundError on
    the very next read, immediately after. The write and the read-back
    are now both inside the same COMPILE_LOCK section, so a thread
    racing to acquire that same lock (simulating clear_stale_export_
    artifacts, which itself only ever runs from inside a compile that
    already holds COMPILE_LOCK -- see compile_notebook_to_api) can't run
    between them anymore -- it can only run before the write starts or
    after the read has already finished.
    """

    _compile_a_notebook("export_openapi_read_race_test.ipynb")

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with COMPILE_LOCK:
            lock_acquired.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)

    result = {}

    def do_export():
        result["resp"] = client.post("/api/export-openapi", json={"format": "json"})

    request_thread = threading.Thread(target=do_export)
    request_thread.start()
    request_thread.join(timeout=0.3)

    assert request_thread.is_alive(), (
        "POST /api/export-openapi should still be blocked on COMPILE_LOCK "
        "for its whole write-then-read-back sequence"
    )

    release_lock.set()
    request_thread.join(timeout=5)
    holder.join(timeout=5)

    assert not request_thread.is_alive()
    assert result["resp"].status_code == 200
    assert "paths" in result["resp"].json()["schema"]
