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
from backend.routes.upload import (
    MAX_NOTEBOOK_VERSIONS,
    UPLOAD_DIR,
    _description_sidecar_path,
    _notebook_versions_dir,
    _tags_sidecar_path,
    resolve_generated_path,
    resolve_upload_path,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

client = TestClient(app)


def _notebook_bytes(function_source):
    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(nbformat.v4.new_code_cell(function_source))
    return nbformat.writes(notebook).encode("utf-8")


@pytest.fixture(autouse=True)
def _cleanup_uploaded_files():
    # Backs up full file contents, not just names -- test_delete_all_notebooks_*
    # below exercises DELETE /api/notebooks, which (correctly, by design) removes
    # every ".ipynb" file directly in UPLOAD_DIR, including any that predate this
    # test run (e.g. uploads/sample.ipynb, checked into this repo). Restoring by
    # name alone (the previous behavior: remove whatever's new, ignore what's
    # missing) left a pre-existing file permanently deleted from disk the first
    # time a test actually exercised that endpoint -- confirmed: it happened.
    names_before = set(os.listdir(UPLOAD_DIR))
    backup = {
        name: (Path(UPLOAD_DIR) / name).read_bytes()
        for name in names_before
        if (Path(UPLOAD_DIR) / name).is_file()
    }
    yield
    names_after = set(os.listdir(UPLOAD_DIR))
    for name in names_after - names_before:
        path = Path(UPLOAD_DIR) / name
        # A test exercising notebook version history (see
        # _notebook_versions_dir in backend/routes/upload.py) creates the
        # ".versions" directory as a new top-level UPLOAD_DIR entry the
        # first time it runs -- os.remove alone can't remove a directory
        # (it raises IsADirectoryError), so this needs the same
        # dir-vs-file branch DELETE /api/notebooks/{filename} itself
        # already applies to that same directory.
        if path.is_dir():
            shutil.rmtree(path)
        else:
            os.remove(path)
    for name in names_before - names_after:
        if name in backup:
            (Path(UPLOAD_DIR) / name).write_bytes(backup[name])


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
        upload_module.delete_all_notebooks,
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


def test_resolve_upload_path_rejects_an_embedded_null_byte():
    """Confirmed exploitable before this fix: Path("nb\x00.ipynb").is_absolute()
    doesn't raise (a null byte isn't special to pathlib's own parsing),
    so this sailed past the existing absolute-path guard clause -- but
    the later .resolve() call eventually hands it to the underlying
    os.path.realpath/lstat syscalls, which do reject it, as a bare
    ValueError ("embedded null character in path"), an unhandled 500
    instead of the same clean 400 every other malformed-path case in
    this file already gets.
    """

    with pytest.raises(Exception):
        resolve_upload_path("nb\x00.ipynb")


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


def test_resolve_generated_path_rejects_an_embedded_null_byte():

    with pytest.raises(Exception):
        resolve_generated_path("app\x00.py")


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


def test_upload_dir_and_generated_dir_configured_to_the_same_path_are_rejected_at_import(
    tmp_path
):
    """Confirmed catastrophic if left unchecked: UPLOAD_DIR and
    GENERATED_DIR are each read independently from their own env var, with
    nothing stopping an operator from configuring them to the same
    directory. Reproduced live before this fix: pointing both at the same
    path, uploading a notebook, compiling it, then calling DELETE
    /api/generated -- whose own docstring says it resets the dashboard's
    compiled-app state back to "nothing compiled yet" via
    shutil.rmtree(GENERATED_DIR) -- permanently destroyed the uploaded
    notebook right along with it: the whole shared directory vanished
    outright, not just the compiled output, with no way to recover it.

    Run in a fresh subprocess since both directories are only ever read
    once, at import time.
    """

    shared_dir = tmp_path / "shared_dir"

    proc = subprocess.run(
        [sys.executable, "-c", "from backend.routes import upload"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "NOTEBOOK_API_UPLOAD_DIR": str(shared_dir),
            "NOTEBOOK_API_GENERATED_DIR": str(shared_dir),
        },
    )

    assert proc.returncode != 0
    assert "must not be the same directory" in proc.stderr


def test_upload_dir_nested_inside_generated_dir_is_rejected_at_import(tmp_path):
    """Same class of destructive overlap as the identical-path case above,
    just reached the other way around: GENERATED_DIR nested inside
    UPLOAD_DIR (DELETE /api/generated's own shutil.rmtree(GENERATED_DIR)
    would still remove real uploaded notebooks sitting under it) or
    UPLOAD_DIR nested inside GENERATED_DIR (the reverse -- a recompile's
    own directory-wide writes could just as easily reach into it).
    """

    parent_dir = tmp_path / "parent_dir"
    nested_dir = parent_dir / "nested"

    proc = subprocess.run(
        [sys.executable, "-c", "from backend.routes import upload"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "NOTEBOOK_API_UPLOAD_DIR": str(parent_dir),
            "NOTEBOOK_API_GENERATED_DIR": str(nested_dir),
        },
    )

    assert proc.returncode != 0
    assert "must not be the same directory" in proc.stderr


def test_upload_dir_and_generated_dir_configured_to_separate_paths_import_cleanly(
    tmp_path
):
    """The common, correct case -- two distinct, non-nested directories --
    must be completely unaffected by the collision check above.
    """

    proc = subprocess.run(
        [sys.executable, "-c", "from backend.routes import upload; print('IMPORT_OK')"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "NOTEBOOK_API_UPLOAD_DIR": str(tmp_path / "separate_uploads"),
            "NOTEBOOK_API_GENERATED_DIR": str(tmp_path / "separate_generated"),
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "IMPORT_OK" in proc.stdout


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


def test_upload_batch_uploads_multiple_notebooks_in_one_request():

    first_content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")
    second_content = _notebook_bytes("def subtract(a: int, b: int) -> int:\n    return a - b\n")

    resp = client.post(
        "/api/upload/batch",
        files=[
            ("files", ("batch_a.ipynb", io.BytesIO(first_content), "application/json")),
            ("files", ("batch_b.ipynb", io.BytesIO(second_content), "application/json")),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()

    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0
    assert [r["filename"] for r in body["results"]] == ["batch_a.ipynb", "batch_b.ipynb"]
    assert all(r["status"] == "success" for r in body["results"])
    assert all(r["overwritten"] is False for r in body["results"])

    assert (Path(UPLOAD_DIR) / "batch_a.ipynb").read_bytes() == first_content
    assert (Path(UPLOAD_DIR) / "batch_b.ipynb").read_bytes() == second_content


def test_upload_batch_continues_past_a_single_invalid_file():
    """One bad file in the batch must not abort the rest -- each file is
    processed independently, unlike a naive loop of individual POST
    /api/upload calls a caller might otherwise have to write, where an
    unhandled error on file N could leave files after it never attempted.
    """

    good_content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    resp = client.post(
        "/api/upload/batch",
        files=[
            ("files", ("batch_good.ipynb", io.BytesIO(good_content), "application/json")),
            ("files", ("batch_bad.ipynb", io.BytesIO(b"not a notebook"), "application/json")),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()

    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    good_result, bad_result = body["results"]
    assert good_result == {
        "status": "success",
        "filename": "batch_good.ipynb",
        "path": str(resolve_upload_path("batch_good.ipynb")),
        "overwritten": False,
    }
    assert bad_result["filename"] == "batch_bad.ipynb"
    assert bad_result["status"] == "error"
    assert "not a valid Jupyter notebook" in bad_result["detail"]

    assert (Path(UPLOAD_DIR) / "batch_good.ipynb").is_file()
    assert not (Path(UPLOAD_DIR) / "batch_bad.ipynb").exists()


def test_upload_batch_reports_a_collision_error_without_overwrite():

    original_content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("batch_collide.ipynb", io.BytesIO(original_content), "application/json")},
    )

    resp = client.post(
        "/api/upload/batch",
        files=[
            (
                "files",
                (
                    "batch_collide.ipynb",
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                ),
            ),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()

    assert body["succeeded_count"] == 0
    assert body["failed_count"] == 1
    assert body["results"][0]["status"] == "error"
    assert "already exists" in body["results"][0]["detail"]

    # The original file must be completely untouched.
    assert (Path(UPLOAD_DIR) / "batch_collide.ipynb").read_bytes() == original_content


def test_upload_batch_overwrite_applies_to_every_file():

    original_content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("batch_overwrite.ipynb", io.BytesIO(original_content), "application/json")},
    )

    replacement_content = _notebook_bytes("def subtract(a: int, b: int) -> int:\n    return a - b\n")

    resp = client.post(
        "/api/upload/batch?overwrite=true",
        files=[
            (
                "files",
                ("batch_overwrite.ipynb", io.BytesIO(replacement_content), "application/json"),
            ),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()

    assert body["succeeded_count"] == 1
    assert body["results"][0]["overwritten"] is True
    assert (Path(UPLOAD_DIR) / "batch_overwrite.ipynb").read_bytes() == replacement_content


def test_upload_batch_rejects_more_files_than_the_configured_maximum(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "MAX_BATCH_UPLOAD_FILES", 1)

    content = _notebook_bytes("def f() -> int:\n    return 1\n")

    resp = client.post(
        "/api/upload/batch",
        files=[
            ("files", ("batch_max_a.ipynb", io.BytesIO(content), "application/json")),
            ("files", ("batch_max_b.ipynb", io.BytesIO(content), "application/json")),
        ],
    )

    assert resp.status_code == 400
    assert "at most 1" in resp.json()["detail"]
    assert not (Path(UPLOAD_DIR) / "batch_max_a.ipynb").exists()
    assert not (Path(UPLOAD_DIR) / "batch_max_b.ipynb").exists()


def _zip_bytes(entries):
    """Build an in-memory .zip archive from {entry_name: content_bytes}."""

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry_name, content in entries.items():
            archive.writestr(entry_name, content)

    return buffer.getvalue()


def test_import_notebooks_uploads_every_ipynb_entry_in_the_zip():

    content_a = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")
    content_b = _notebook_bytes("def sub(a: int, b: int) -> int:\n    return a - b\n")

    archive_bytes = _zip_bytes({
        "import_a.ipynb": content_a,
        "import_b.ipynb": content_b,
    })

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0
    assert [r["filename"] for r in body["results"]] == ["import_a.ipynb", "import_b.ipynb"]
    assert all(r["status"] == "success" for r in body["results"])

    assert (Path(UPLOAD_DIR) / "import_a.ipynb").read_bytes() == content_a
    assert (Path(UPLOAD_DIR) / "import_b.ipynb").read_bytes() == content_b


def test_import_notebooks_flattens_nested_paths_to_their_basename():

    content = _notebook_bytes("def f() -> int:\n    return 1\n")

    archive_bytes = _zip_bytes({"nested/dir/import_nested.ipynb": content})

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["filename"] == "import_nested.ipynb"
    assert (Path(UPLOAD_DIR) / "import_nested.ipynb").read_bytes() == content


def test_import_notebooks_skips_non_ipynb_entries():

    content = _notebook_bytes("def f() -> int:\n    return 1\n")

    archive_bytes = _zip_bytes({
        "import_readme.ipynb": content,
        "README.md": b"not a notebook",
    })

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert [r["filename"] for r in body["results"]] == ["import_readme.ipynb"]


def test_import_notebooks_continues_past_a_single_invalid_entry():

    good_content = _notebook_bytes("def f() -> int:\n    return 1\n")

    archive_bytes = _zip_bytes({
        "import_good.ipynb": good_content,
        "import_bad.ipynb": b"not a notebook",
    })

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    good_result, bad_result = body["results"]
    assert good_result["status"] == "success"
    assert bad_result["filename"] == "import_bad.ipynb"
    assert bad_result["status"] == "error"
    assert "not a valid Jupyter notebook" in bad_result["detail"]

    assert (Path(UPLOAD_DIR) / "import_good.ipynb").is_file()
    assert not (Path(UPLOAD_DIR) / "import_bad.ipynb").exists()


def test_import_notebooks_reports_a_collision_error_without_overwrite():

    original_content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("import_collide.ipynb", io.BytesIO(original_content), "application/json")},
    )

    archive_bytes = _zip_bytes({
        "import_collide.ipynb": _notebook_bytes("def f() -> int:\n    return 1\n"),
    })

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["failed_count"] == 1
    assert "already exists" in body["results"][0]["detail"]

    assert (Path(UPLOAD_DIR) / "import_collide.ipynb").read_bytes() == original_content


def test_import_notebooks_overwrite_applies_to_every_entry():

    original_content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("import_overwrite.ipynb", io.BytesIO(original_content), "application/json")},
    )

    replacement_content = _notebook_bytes("def sub(a: int, b: int) -> int:\n    return a - b\n")

    archive_bytes = _zip_bytes({"import_overwrite.ipynb": replacement_content})

    resp = client.post(
        "/api/notebooks/import?overwrite=true",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["overwritten"] is True
    assert (Path(UPLOAD_DIR) / "import_overwrite.ipynb").read_bytes() == replacement_content


def test_import_notebooks_tags_applies_to_every_successfully_imported_entry():

    content_a = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")
    content_b = _notebook_bytes("def sub(a: int, b: int) -> int:\n    return a - b\n")

    archive_bytes = _zip_bytes({
        "import_tags_a.ipynb": content_a,
        "import_tags_b.ipynb": content_b,
    })

    resp = client.post(
        "/api/notebooks/import",
        params={"tags": "imported,reviewed"},
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    assert resp.json()["succeeded_count"] == 2

    assert client.get(
        "/api/notebooks/import_tags_a.ipynb/tags"
    ).json()["tags"] == ["imported", "reviewed"]
    assert client.get(
        "/api/notebooks/import_tags_b.ipynb/tags"
    ).json()["tags"] == ["imported", "reviewed"]


def test_import_notebooks_tags_is_not_applied_to_a_failed_entry():

    content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("import_tags_collision.ipynb", io.BytesIO(content), "application/json")},
    )

    archive_bytes = _zip_bytes({"import_tags_collision.ipynb": content})

    resp = client.post(
        "/api/notebooks/import",
        params={"tags": "imported"},
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["status"] == "error"

    assert client.get(
        "/api/notebooks/import_tags_collision.ipynb/tags"
    ).json()["tags"] == []


def test_import_notebooks_rejects_an_invalid_tags_value_before_reading_the_archive():

    content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")
    archive_bytes = _zip_bytes({"import_tags_bad.ipynb": content})

    resp = client.post(
        "/api/notebooks/import",
        params={"tags": "x" * 51},
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 400
    assert not (Path(UPLOAD_DIR) / "import_tags_bad.ipynb").exists()


def test_import_notebooks_rejects_a_non_zip_file():

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("notes.ipynb", io.BytesIO(b"{}"), "application/json")},
    )

    assert resp.status_code == 400
    assert "must be a .zip archive" in resp.json()["detail"]


def test_import_notebooks_rejects_a_corrupt_zip_file():

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(b"not a real zip"), "application/zip")},
    )

    assert resp.status_code == 400
    assert "not a valid zip archive" in resp.json()["detail"]


def test_import_notebooks_rejects_a_zip_with_no_ipynb_files():

    archive_bytes = _zip_bytes({"README.md": b"nothing to import here"})

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 400
    assert "no .ipynb files" in resp.json()["detail"]


def test_import_notebooks_rejects_more_entries_than_the_configured_maximum(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "MAX_BATCH_UPLOAD_FILES", 1)

    content = _notebook_bytes("def f() -> int:\n    return 1\n")

    archive_bytes = _zip_bytes({
        "import_max_a.ipynb": content,
        "import_max_b.ipynb": content,
    })

    resp = client.post(
        "/api/notebooks/import",
        files={"file": ("bundle.zip", io.BytesIO(archive_bytes), "application/zip")},
    )

    assert resp.status_code == 400
    assert "at most 1" in resp.json()["detail"]
    assert not (Path(UPLOAD_DIR) / "import_max_a.ipynb").exists()
    assert not (Path(UPLOAD_DIR) / "import_max_b.ipynb").exists()


def test_upload_lock_for_returns_the_same_lock_for_the_same_filename():
    """_upload_lock_for must hand back the *same* Lock instance for the
    same filename across separate calls (separate requests, in practice)
    -- otherwise two concurrent uploads of the same filename would each
    acquire their own independent Lock and never actually exclude each
    other at all, silently defeating the whole point of it.
    """

    from backend.routes.upload import _upload_lock_for

    assert (
        _upload_lock_for("same_lock_test.ipynb")
        is _upload_lock_for("same_lock_test.ipynb")
    )


def test_upload_lock_for_returns_different_locks_for_different_filenames():
    """Scoped per filename, not a single global lock -- two concurrent
    uploads of two *different* notebooks (the overwhelmingly common case)
    must stay fully concurrent; only genuinely colliding same-name
    uploads should ever need to serialize.
    """

    from backend.routes.upload import _upload_lock_for

    assert (
        _upload_lock_for("different_lock_test_a.ipynb")
        is not _upload_lock_for("different_lock_test_b.ipynb")
    )


def test_upload_lock_for_enforces_mutual_exclusion_for_the_same_filename():
    """The actual property upload_notebook depends on to close the race
    this fix exists for: two coroutines contending for the same
    filename's lock can never both be inside the critical section at
    once, and the second only ever enters after the first has fully
    exited (not merely "started exiting") -- reproduced deterministically
    via two coroutines racing the identical lock, driven by
    asyncio.gather on a single event loop, rather than trying to force
    this specific interleaving through two full, independent HTTP
    requests (whose exact timing an in-memory ASGI transport doesn't
    reliably reproduce -- confirmed while writing this test: even a
    deliberately delayed UploadFile.read() didn't reliably interleave
    two concurrent POST /api/upload calls to the same filename through
    TestClient/httpx's in-memory transport, unlike a real network
    connection's genuine I/O wait). Testing the lock's own guarantee
    directly is both deterministic and exactly what upload_notebook
    actually relies on.
    """

    import asyncio

    from backend.routes.upload import _upload_lock_for

    async def scenario():

        filename = "mutual_exclusion_test.ipynb"

        currently_inside = 0
        max_concurrent = 0
        events = []

        async def critical_section(tag):
            nonlocal currently_inside, max_concurrent

            async with _upload_lock_for(filename):

                currently_inside += 1
                max_concurrent = max(max_concurrent, currently_inside)
                events.append((tag, "enter"))

                # Stands in for upload_notebook's own streaming/validation
                # work while holding the lock -- long enough that, if the
                # lock weren't actually excluding the other coroutine, it
                # would have every opportunity to interleave its own
                # "enter" in between.
                await asyncio.sleep(0.05)

                events.append((tag, "exit"))
                currently_inside -= 1

        await asyncio.gather(critical_section("A"), critical_section("B"))

        return max_concurrent, events

    max_concurrent, events = asyncio.run(scenario())

    assert max_concurrent == 1

    # Whichever coroutine goes first must fully enter *and exit* before
    # the other ever enters -- not just start before the other starts.
    assert events in (
        [("A", "enter"), ("A", "exit"), ("B", "enter"), ("B", "exit")],
        [("B", "enter"), ("B", "exit"), ("A", "enter"), ("A", "exit")],
    )


def test_upload_lock_for_does_not_serialize_different_filenames():
    """The flip side of the mutual-exclusion test above: two coroutines
    holding *different* filenames' locks must be able to run fully
    concurrently, with neither waiting on the other at all.
    """

    import asyncio

    from backend.routes.upload import _upload_lock_for

    async def scenario():

        both_entered = asyncio.Event()
        entered_count = 0
        events = []

        async def critical_section(tag, filename):
            nonlocal entered_count

            async with _upload_lock_for(filename):

                events.append((tag, "enter"))
                entered_count += 1

                if entered_count == 2:
                    both_entered.set()

                # If these two were sharing a lock, this wait would never
                # be satisfied within the timeout below -- the second
                # coroutine couldn't have entered while the first still
                # holds a shared lock.
                await asyncio.wait_for(both_entered.wait(), timeout=1)

                events.append((tag, "exit"))

        await asyncio.gather(
            critical_section("A", "concurrent_lock_test_a.ipynb"),
            critical_section("B", "concurrent_lock_test_b.ipynb"),
        )

        return events

    events = asyncio.run(scenario())

    # Both must have entered before either exited -- true concurrency,
    # not one waiting for the other to finish first.
    assert events[0][1] == "enter"
    assert events[1][1] == "enter"


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


def test_list_notebooks_search_filters_by_a_case_insensitive_filename_substring():

    for filename in ("search_apple.ipynb", "search_banana.ipynb"):
        resp = client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                )
            },
        )
        assert resp.status_code == 200

    notebooks = client.get("/api/notebooks?search=APPLE").json()["notebooks"]
    filenames = {nb["filename"] for nb in notebooks}

    assert filenames == {"search_apple.ipynb"}


def test_list_notebooks_search_matching_nothing_returns_an_empty_list():

    resp = client.post(
        "/api/upload",
        files={
            "file": (
                "search_only_this.ipynb",
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200

    notebooks = client.get(
        "/api/notebooks?search=this_substring_matches_nothing_at_all"
    ).json()["notebooks"]

    assert notebooks == []


def test_list_notebooks_sorts_by_name_ascending_by_default():
    """Preserves the previous, and still default, behavior -- a plain GET
    /api/notebooks with no query string -- so an existing caller relying
    on alphabetical-by-filename order sees no change.
    """

    for filename in ("sort_default_b.ipynb", "sort_default_a.ipynb"):
        resp = client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                )
            },
        )
        assert resp.status_code == 200

    notebooks = client.get("/api/notebooks?search=sort_default_").json()["notebooks"]
    filenames = [nb["filename"] for nb in notebooks]

    assert filenames == ["sort_default_a.ipynb", "sort_default_b.ipynb"]


def test_list_notebooks_sorts_by_size_ascending_and_descending():

    for filename, function_source in (
        ("sort_size_small.ipynb", "def f() -> int:\n    return 1\n"),
        (
            "sort_size_large.ipynb",
            "def f() -> int:\n    return 1  # padding to make this cell bigger\n",
        ),
    ):
        resp = client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes(function_source)),
                    "application/json",
                )
            },
        )
        assert resp.status_code == 200

    asc = client.get("/api/notebooks?search=sort_size_&sort=size&order=asc").json()["notebooks"]
    assert [nb["filename"] for nb in asc] == ["sort_size_small.ipynb", "sort_size_large.ipynb"]

    desc = client.get("/api/notebooks?search=sort_size_&sort=size&order=desc").json()["notebooks"]
    assert [nb["filename"] for nb in desc] == ["sort_size_large.ipynb", "sort_size_small.ipynb"]


def test_list_notebooks_sorts_by_modified_descending_shows_the_newest_first():

    resp_older = client.post(
        "/api/upload",
        files={
            "file": (
                "sort_modified_older.ipynb",
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    assert resp_older.status_code == 200

    older_path = Path(UPLOAD_DIR) / "sort_modified_older.ipynb"
    older_stat = older_path.stat()
    os.utime(older_path, (older_stat.st_atime, older_stat.st_mtime - 3600))

    resp_newer = client.post(
        "/api/upload",
        files={
            "file": (
                "sort_modified_newer.ipynb",
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    assert resp_newer.status_code == 200

    notebooks = client.get(
        "/api/notebooks?search=sort_modified_&sort=modified&order=desc"
    ).json()["notebooks"]

    assert [nb["filename"] for nb in notebooks] == [
        "sort_modified_newer.ipynb",
        "sort_modified_older.ipynb",
    ]


def test_list_notebooks_rejects_an_invalid_sort_value():

    resp = client.get("/api/notebooks?sort=not_a_real_field")

    assert resp.status_code == 400


def test_list_notebooks_rejects_an_invalid_order_value():

    resp = client.get("/api/notebooks?order=sideways")

    assert resp.status_code == 400


def test_list_notebooks_paginates_with_limit_and_offset():

    for filename in (
        "page_a.ipynb",
        "page_b.ipynb",
        "page_c.ipynb",
    ):
        resp = client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                )
            },
        )
        assert resp.status_code == 200

    first_page = client.get("/api/notebooks?search=page_&limit=2&offset=0").json()
    assert [nb["filename"] for nb in first_page["notebooks"]] == [
        "page_a.ipynb",
        "page_b.ipynb",
    ]
    assert first_page["total_count"] == 3
    assert first_page["limit"] == 2
    assert first_page["offset"] == 0

    second_page = client.get("/api/notebooks?search=page_&limit=2&offset=2").json()
    assert [nb["filename"] for nb in second_page["notebooks"]] == ["page_c.ipynb"]
    assert second_page["total_count"] == 3
    assert second_page["limit"] == 2
    assert second_page["offset"] == 2


def test_list_notebooks_without_limit_returns_every_matching_notebook():
    """Preserves the previous, still-default behavior -- a plain GET
    /api/notebooks with no "limit" returns everything matching "search",
    not just some implicit page size.
    """

    for filename in ("nolimit_a.ipynb", "nolimit_b.ipynb"):
        resp = client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                )
            },
        )
        assert resp.status_code == 200

    body = client.get("/api/notebooks?search=nolimit_").json()

    assert [nb["filename"] for nb in body["notebooks"]] == [
        "nolimit_a.ipynb",
        "nolimit_b.ipynb",
    ]
    assert body["total_count"] == 2
    assert body["limit"] is None
    assert body["offset"] == 0


def test_list_notebooks_offset_past_the_end_returns_an_empty_list():

    resp = client.post(
        "/api/upload",
        files={
            "file": (
                "offset_only_one.ipynb",
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200

    body = client.get(
        "/api/notebooks?search=offset_only_one&offset=5"
    ).json()

    assert body["notebooks"] == []
    assert body["total_count"] == 1


def test_list_notebooks_rejects_a_negative_offset():

    resp = client.get("/api/notebooks?offset=-1")

    assert resp.status_code == 400


def test_list_notebooks_rejects_a_non_positive_limit():

    resp = client.get("/api/notebooks?limit=0")

    assert resp.status_code == 400

    resp = client.get("/api/notebooks?limit=-5")

    assert resp.status_code == 400


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


def test_get_notebook_rejects_a_filename_with_an_embedded_null_byte():
    """Confirmed exploitable before this fix: a null byte in the filename
    sailed past resolve_upload_path's absolute-path guard clause (a null
    byte isn't special to pathlib's own parsing), but the later
    .resolve() call raised a bare ValueError from the underlying
    os.path.realpath/lstat syscalls, an unhandled 500 instead of a clean
    400.
    """

    resp = client.get("/api/notebooks/nb%00.ipynb")

    assert resp.status_code == 400


def test_get_notebook_info_matches_the_notebooks_own_entry_in_the_list():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("info_test.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200
    client.put("/api/notebooks/info_test.ipynb/tags", json={"tags": ["scratch"]})

    info_resp = client.get("/api/notebooks/info_test.ipynb/info")
    assert info_resp.status_code == 200

    info_body = info_resp.json()
    assert info_body["status"] == "success"
    assert info_body["filename"] == "info_test.ipynb"
    assert info_body["tags"] == ["scratch"]
    assert info_body["currently_compiled"] is False
    assert "notebook_changed_since_compile" not in info_body
    assert "compiled_at" not in info_body

    list_entry = next(
        nb for nb in client.get("/api/notebooks").json()["notebooks"]
        if nb["filename"] == "info_test.ipynb"
    )
    assert {k: v for k, v in info_body.items() if k != "status"} == list_entry


def test_get_notebook_info_reports_currently_compiled_fields():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("info_compiled_test.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "info_compiled_test.ipynb"}
    )
    assert compile_resp.status_code == 200

    info_resp = client.get("/api/notebooks/info_compiled_test.ipynb/info")
    assert info_resp.status_code == 200

    info_body = info_resp.json()
    assert info_body["currently_compiled"] is True
    assert info_body["notebook_changed_since_compile"] is False
    assert info_body["compiled_at"] is not None


def test_get_notebook_info_returns_404_for_missing_file():

    resp = client.get("/api/notebooks/does_not_exist_at_all.ipynb/info")

    assert resp.status_code == 404


def test_get_notebook_info_rejects_absolute_filename():

    resp = client.get("/api/notebooks/%2Fetc%2Fpasswd/info")

    assert resp.status_code in (400, 404)
    assert "root:" not in resp.text


def test_get_notebooks_info_batch_matches_each_notebooks_own_info_entry():

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("info_batch_a.ipynb", content_a),
        ("info_batch_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    client.put("/api/notebooks/info_batch_a.ipynb/tags", json={"tags": ["scratch"]})

    resp = client.post(
        "/api/notebooks/info-batch",
        json={"filenames": ["info_batch_a.ipynb", "info_batch_b.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["info_batch_a.ipynb"]["status"] == "success"
    assert results_by_filename["info_batch_a.ipynb"]["tags"] == ["scratch"]

    single_info = client.get("/api/notebooks/info_batch_a.ipynb/info").json()
    assert results_by_filename["info_batch_a.ipynb"] == single_info


def test_get_notebooks_info_batch_reports_a_missing_filename_without_aborting_the_rest():

    _upload_sample_notebook("info_batch_partial.ipynb")

    resp = client.post(
        "/api/notebooks/info-batch",
        json={"filenames": ["info_batch_partial.ipynb", "does_not_exist.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["info_batch_partial.ipynb"]["status"] == "success"
    assert results_by_filename["does_not_exist.ipynb"]["status"] == "error"
    assert "not found" in results_by_filename["does_not_exist.ipynb"]["detail"]


def test_get_notebooks_info_batch_rejects_a_non_list_filenames_value():

    resp = client.post("/api/notebooks/info-batch", json={"filenames": "not-a-list"})

    assert resp.status_code == 400


def test_get_notebooks_info_batch_rejects_an_empty_filenames_list():

    resp = client.post("/api/notebooks/info-batch", json={"filenames": []})

    assert resp.status_code == 400


def test_export_notebooks_zips_the_named_filenames():

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("export_a.ipynb", content_a),
        ("export_b.ipynb", content_b),
        ("export_c.ipynb", content_a),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    export_resp = client.get(
        "/api/notebooks/export", params={"filenames": "export_a.ipynb,export_b.ipynb"}
    )

    assert export_resp.status_code == 200
    assert export_resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(export_resp.content)) as archive:
        assert sorted(archive.namelist()) == ["export_a.ipynb", "export_b.ipynb"]
        assert json.loads(archive.read("export_a.ipynb")) == json.loads(content_a)
        assert json.loads(archive.read("export_b.ipynb")) == json.loads(content_b)


def test_export_notebooks_without_filenames_exports_every_uploaded_notebook():

    client.delete("/api/notebooks?confirm=true")

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    for filename in ("export_all_a.ipynb", "export_all_b.ipynb"):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    export_resp = client.get("/api/notebooks/export")

    assert export_resp.status_code == 200

    with zipfile.ZipFile(io.BytesIO(export_resp.content)) as archive:
        assert sorted(archive.namelist()) == ["export_all_a.ipynb", "export_all_b.ipynb"]


def test_export_notebooks_returns_404_naming_every_missing_filename():

    _upload_sample_notebook("export_missing_present.ipynb")

    resp = client.get(
        "/api/notebooks/export",
        params={"filenames": "export_missing_present.ipynb,does_not_exist.ipynb"},
    )

    assert resp.status_code == 404
    assert "does_not_exist.ipynb" in resp.json()["detail"]


def test_export_notebooks_returns_404_when_nothing_uploaded():

    client.delete("/api/notebooks?confirm=true")

    resp = client.get("/api/notebooks/export")

    assert resp.status_code == 404


def test_export_notebooks_rejects_a_blank_filenames_value():

    resp = client.get("/api/notebooks/export", params={"filenames": " , , "})

    assert resp.status_code == 400


def test_export_notebooks_by_tag_bundles_only_matching_notebooks():

    client.delete("/api/notebooks?confirm=true")

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    for filename in ("export_tag_a.ipynb", "export_tag_b.ipynb", "export_tag_c.ipynb"):
        client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )

    client.put("/api/notebooks/export_tag_a.ipynb/tags", json={"tags": ["prod"]})
    client.put("/api/notebooks/export_tag_b.ipynb/tags", json={"tags": ["prod"]})
    client.put("/api/notebooks/export_tag_c.ipynb/tags", json={"tags": ["staging"]})

    resp = client.get("/api/notebooks/export", params={"tag": "prod"})

    assert resp.status_code == 200

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        assert sorted(archive.namelist()) == ["export_tag_a.ipynb", "export_tag_b.ipynb"]


def test_export_notebooks_by_tag_returns_404_when_nothing_matches():

    client.delete("/api/notebooks?confirm=true")

    _upload_sample_notebook("export_tag_unmatched.ipynb")

    resp = client.get("/api/notebooks/export", params={"tag": "does-not-exist"})

    assert resp.status_code == 404


def test_export_notebooks_rejects_both_filenames_and_tag():

    _upload_sample_notebook("export_both_a.ipynb")

    resp = client.get(
        "/api/notebooks/export",
        params={"filenames": "export_both_a.ipynb", "tag": "prod"},
    )

    assert resp.status_code == 400


def test_find_duplicate_notebooks_groups_byte_identical_uploads():

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("dup_a1.ipynb", content_a),
        ("dup_a2.ipynb", content_a),
        ("dup_a3.ipynb", content_a),
        ("dup_b1.ipynb", content_b),
        ("dup_unique.ipynb", content_b + b" "),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    resp = client.get("/api/notebooks/duplicates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["group_count"] == 1
    assert body["duplicate_notebook_count"] == 3

    group = body["duplicate_groups"][0]
    assert group["filenames"] == ["dup_a1.ipynb", "dup_a2.ipynb", "dup_a3.ipynb"]
    assert group["size_bytes"] == len(content_a)
    assert len(group["sha256"]) == 64


def test_find_duplicate_notebooks_reports_no_groups_when_nothing_duplicated():

    client.delete("/api/notebooks?confirm=true")

    _upload_sample_notebook("dup_none_a.ipynb")

    resp = client.get("/api/notebooks/duplicates")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "duplicate_groups": [],
        "group_count": 0,
        "duplicate_notebook_count": 0,
    }


def test_find_duplicate_notebooks_reports_multiple_independent_groups():

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("dup_group1_a.ipynb", content_a),
        ("dup_group1_b.ipynb", content_a),
        ("dup_group2_a.ipynb", content_b),
        ("dup_group2_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    resp = client.get("/api/notebooks/duplicates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["group_count"] == 2
    assert body["duplicate_notebook_count"] == 4


def test_resolve_duplicate_notebooks_keeps_alphabetically_first_by_default():

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    for filename in ("resolve_z.ipynb", "resolve_a.ipynb", "resolve_m.ipynb"):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content_a), "application/json")},
        )
        assert resp.status_code == 200

    resp = client.post("/api/notebooks/duplicates/resolve", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 0

    result = body["results"][0]
    assert result["status"] == "success"
    assert result["kept_filename"] == "resolve_a.ipynb"
    assert sorted(e["filename"] for e in result["deleted_filenames"]) == [
        "resolve_m.ipynb", "resolve_z.ipynb",
    ]

    remaining = {n["filename"] for n in client.get("/api/notebooks").json()["notebooks"]}
    assert remaining == {"resolve_a.ipynb"}


def test_resolve_duplicate_notebooks_honors_keep_override():

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    for filename in ("resolve_keep_a.ipynb", "resolve_keep_b.ipynb"):
        client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content_a), "application/json")},
        )

    sha256 = client.get("/api/notebooks/duplicates").json()["duplicate_groups"][0]["sha256"]

    resp = client.post(
        "/api/notebooks/duplicates/resolve",
        json={"keep": {sha256: "resolve_keep_b.ipynb"}},
    )

    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["kept_filename"] == "resolve_keep_b.ipynb"
    assert [e["filename"] for e in result["deleted_filenames"]] == ["resolve_keep_a.ipynb"]

    remaining = {n["filename"] for n in client.get("/api/notebooks").json()["notebooks"]}
    assert remaining == {"resolve_keep_b.ipynb"}


def test_resolve_duplicate_notebooks_reports_an_invalid_keep_filename_for_just_that_group():

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    for filename in ("resolve_bad_a.ipynb", "resolve_bad_b.ipynb"):
        client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content_a), "application/json")},
        )

    sha256 = client.get("/api/notebooks/duplicates").json()["duplicate_groups"][0]["sha256"]

    resp = client.post(
        "/api/notebooks/duplicates/resolve",
        json={"keep": {sha256: "not_in_this_group.ipynb"}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 0
    assert body["failed_count"] == 1
    assert body["results"][0]["status"] == "error"
    assert "not a member" in body["results"][0]["detail"]

    # Nothing was deleted -- both duplicates remain.
    remaining = {n["filename"] for n in client.get("/api/notebooks").json()["notebooks"]}
    assert remaining == {"resolve_bad_a.ipynb", "resolve_bad_b.ipynb"}


def test_resolve_duplicate_notebooks_is_a_no_op_success_when_nothing_is_duplicated():

    client.delete("/api/notebooks?confirm=true")

    _upload_sample_notebook("resolve_none.ipynb")

    resp = client.post("/api/notebooks/duplicates/resolve", json={})

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success", "results": [], "succeeded_count": 0, "failed_count": 0,
    }

    remaining = {n["filename"] for n in client.get("/api/notebooks").json()["notebooks"]}
    assert remaining == {"resolve_none.ipynb"}


def test_resolve_duplicate_notebooks_also_removes_tags_description_and_versions(
    tmp_path
):

    from backend.routes import upload as upload_module

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    for filename in ("resolve_cleanup_a.ipynb", "resolve_cleanup_z.ipynb"):
        client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content_a), "application/json")},
        )

    client.put(
        "/api/notebooks/resolve_cleanup_z.ipynb/tags", json={"tags": ["stale"]}
    )
    client.put(
        "/api/notebooks/resolve_cleanup_z.ipynb/description",
        json={"description": "about to be resolved away"},
    )

    resp = client.post("/api/notebooks/duplicates/resolve", json={})
    assert resp.status_code == 200

    assert not upload_module._tags_sidecar_path("resolve_cleanup_z.ipynb").is_file()
    assert not upload_module._description_sidecar_path(
        "resolve_cleanup_z.ipynb"
    ).is_file()


def test_resolve_duplicate_notebooks_rejects_a_non_object_keep_value():

    resp = client.post("/api/notebooks/duplicates/resolve", json={"keep": "not-an-object"})

    assert resp.status_code == 400


def test_search_notebook_content_finds_notebooks_with_a_matching_cell():

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes(
        "import pandas as pd\n\n"
        "def load() -> str:\n    df = pd.read_csv('data.csv')\n    return 'done'\n"
    )
    content_b = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    for filename, content in (
        ("search_content_a.ipynb", content_a),
        ("search_content_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    resp = client.get(
        "/api/notebooks/search-content", params={"search": "read_csv"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["search"] == "read_csv"
    assert body["notebook_count"] == 1
    assert body["matches"][0]["filename"] == "search_content_a.ipynb"

    cell_match = body["matches"][0]["matches"][0]
    assert cell_match["cell_index"] == 0
    assert "read_csv" in cell_match["snippet"]


def test_search_notebook_content_is_case_insensitive():

    client.delete("/api/notebooks?confirm=true")

    content = _notebook_bytes(
        "def f() -> int:\n    # TODO: fix this\n    return 1\n"
    )

    resp = client.post(
        "/api/upload",
        files={"file": ("search_content_case.ipynb", io.BytesIO(content), "application/json")},
    )
    assert resp.status_code == 200

    resp = client.get(
        "/api/notebooks/search-content", params={"search": "todo"}
    )

    assert resp.status_code == 200
    assert resp.json()["notebook_count"] == 1


def test_search_notebook_content_reports_no_matches():

    client.delete("/api/notebooks?confirm=true")

    resp = client.get(
        "/api/notebooks/search-content", params={"search": "nonexistent_xyz"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["matches"] == []
    assert body["notebook_count"] == 0


def test_search_notebook_content_requires_a_search_value():

    resp = client.get("/api/notebooks/search-content")

    assert resp.status_code == 400


def test_search_notebook_content_skips_a_malformed_notebook_file():

    client.delete("/api/notebooks?confirm=true")

    filename = "search_content_malformed.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.get(
        "/api/notebooks/search-content", params={"search": "anything"}
    )

    assert resp.status_code == 200
    assert resp.json()["matches"] == []


def test_search_notebook_content_reports_multiple_matching_cells_in_one_notebook():

    client.delete("/api/notebooks?confirm=true")

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(nbformat.v4.new_code_cell("MARKER = 1\n"))
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def f() -> int:\n    MARKER_VALUE = 2\n    return MARKER_VALUE\n"
        )
    )
    content = nbformat.writes(notebook).encode("utf-8")

    resp = client.post(
        "/api/upload",
        files={"file": ("search_content_multi.ipynb", io.BytesIO(content), "application/json")},
    )
    assert resp.status_code == 200

    resp = client.get(
        "/api/notebooks/search-content", params={"search": "MARKER"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["notebook_count"] == 1
    assert len(body["matches"][0]["matches"]) == 2
    assert [m["cell_index"] for m in body["matches"][0]["matches"]] == [0, 1]


def test_diff_notebooks_reports_added_removed_changed_and_unchanged():

    old_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def remove_me() -> int:\n    return 0\n\n"
        "def unchanged_fn() -> int:\n    return 1\n"
    )
    new_content = _notebook_bytes(
        "def add(a: int, b: int, c: int) -> int:\n    return a + b + c\n\n"
        "def add_me() -> int:\n    return 2\n\n"
        "def unchanged_fn() -> int:\n    return 1\n"
    )

    for filename, content in (
        ("diff_old.ipynb", old_content),
        ("diff_new.ipynb", new_content),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    resp = client.get(
        "/api/notebooks/diff", params={"old": "diff_old.ipynb", "new": "diff_new.ipynb"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["old"] == "diff_old.ipynb"
    assert body["new"] == "diff_new.ipynb"
    assert [f["name"] for f in body["added"]] == ["add_me"]
    assert [f["name"] for f in body["removed"]] == ["remove_me"]
    assert [c["name"] for c in body["changed"]] == ["add"]
    assert body["unchanged"] == ["unchanged_fn"]


def test_diff_notebooks_requires_both_filenames():

    resp = client.get("/api/notebooks/diff", params={"old": "a.ipynb"})

    assert resp.status_code == 400


def test_diff_notebooks_returns_404_naming_the_missing_old_notebook():

    _upload_sample_notebook("diff_missing_new_target.ipynb")

    resp = client.get(
        "/api/notebooks/diff",
        params={"old": "does_not_exist.ipynb", "new": "diff_missing_new_target.ipynb"},
    )

    assert resp.status_code == 404
    assert "does_not_exist.ipynb" in resp.json()["detail"]


def test_diff_notebooks_returns_404_naming_the_missing_new_notebook():

    _upload_sample_notebook("diff_missing_old_target.ipynb")

    resp = client.get(
        "/api/notebooks/diff",
        params={"old": "diff_missing_old_target.ipynb", "new": "does_not_exist.ipynb"},
    )

    assert resp.status_code == 404
    assert "does_not_exist.ipynb" in resp.json()["detail"]


def test_diff_notebooks_returns_400_for_a_malformed_notebook():

    _upload_sample_notebook("diff_malformed_valid_side.ipynb")

    filename = "diff_malformed_bad_side.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.get(
        "/api/notebooks/diff",
        params={"old": "diff_malformed_valid_side.ipynb", "new": filename},
    )

    assert resp.status_code == 400
    assert "'new' notebook" in resp.json()["detail"]


def test_delete_notebook_rejects_a_filename_with_an_embedded_null_byte():

    resp = client.delete("/api/notebooks/nb%00.ipynb")

    assert resp.status_code == 400


def test_notebook_storage_reports_per_notebook_and_total_bytes():

    client.delete("/api/notebooks?confirm=true")

    content_a = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")
    content_b = _notebook_bytes(
        "def subtract_two_numbers(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("storage_a.ipynb", content_a),
        ("storage_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    resp = client.get("/api/notebooks/storage")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["notebook_count"] == 2
    assert body["total_version_bytes"] == 0
    assert body["total_version_count"] == 0
    assert body["total_notebook_bytes"] == len(content_a) + len(content_b)
    assert body["total_bytes"] == body["total_notebook_bytes"]

    entries_by_filename = {n["filename"]: n for n in body["notebooks"]}
    assert entries_by_filename["storage_a.ipynb"] == {
        "filename": "storage_a.ipynb",
        "notebook_bytes": len(content_a),
        "version_bytes": 0,
        "version_count": 0,
        "total_bytes": len(content_a),
    }
    assert entries_by_filename["storage_b.ipynb"]["notebook_bytes"] == len(content_b)


def test_notebook_storage_includes_version_history_bytes():

    client.delete("/api/notebooks?confirm=true")

    filename = "storage_versions.ipynb"
    original_content = _notebook_bytes("def f() -> int:\n    return 1\n")
    new_content = _notebook_bytes(
        "def f() -> int:\n    return 1\n\ndef g() -> int:\n    return 2\n"
    )

    client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(original_content), "application/json")},
    )
    client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(new_content), "application/json")},
    )

    resp = client.get("/api/notebooks/storage")

    assert resp.status_code == 200
    body = resp.json()

    entry = body["notebooks"][0]
    assert entry["filename"] == filename
    assert entry["notebook_bytes"] == len(new_content)
    assert entry["version_bytes"] == len(original_content)
    assert entry["version_count"] == 1
    assert entry["total_bytes"] == len(new_content) + len(original_content)

    assert body["total_version_bytes"] == len(original_content)
    assert body["total_version_count"] == 1
    assert body["total_bytes"] == entry["total_bytes"]


def test_notebook_storage_sorts_by_total_bytes_descending():

    client.delete("/api/notebooks?confirm=true")

    small = _notebook_bytes("def f() -> int:\n    return 1\n")
    large = _notebook_bytes(
        "def a_much_longer_function_name_for_a_bigger_notebook() -> int:\n"
        "    return 1\n"
    )
    assert len(large) > len(small)

    for filename, content in (
        ("storage_small.ipynb", small),
        ("storage_large.ipynb", large),
    ):
        client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )

    resp = client.get("/api/notebooks/storage")

    assert resp.status_code == 200
    filenames_in_order = [n["filename"] for n in resp.json()["notebooks"]]
    assert filenames_in_order == ["storage_large.ipynb", "storage_small.ipynb"]


def test_notebook_storage_reports_zeros_for_an_empty_catalog():

    client.delete("/api/notebooks?confirm=true")

    resp = client.get("/api/notebooks/storage")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "notebooks": [],
        "notebook_count": 0,
        "total_notebook_bytes": 0,
        "total_version_bytes": 0,
        "total_version_count": 0,
        "total_bytes": 0,
    }


def test_delete_all_notebooks_requires_confirm_true():
    """A bulk delete with real, hard-to-undo consequences (the notebooks
    in UPLOAD_DIR are the only copy of a user's original uploaded source
    on this server) must not run without the same explicit opt-in
    /api/upload's own "overwrite" and /api/deploy's "force"/"push"
    already require elsewhere in this file.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("bulk_delete_no_confirm.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    resp = client.delete("/api/notebooks")

    assert resp.status_code == 400

    list_resp = client.get("/api/notebooks")
    filenames = {nb["filename"] for nb in list_resp.json()["notebooks"]}
    assert "bulk_delete_no_confirm.ipynb" in filenames


def test_delete_all_notebooks_removes_every_uploaded_notebook():

    content_a = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    content_b = _notebook_bytes(
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )

    for filename, content in (
        ("bulk_delete_a.ipynb", content_a),
        ("bulk_delete_b.ipynb", content_b),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    delete_resp = client.delete("/api/notebooks?confirm=true")

    assert delete_resp.status_code == 200
    body = delete_resp.json()
    assert body["deleted_count"] >= 2
    assert "bulk_delete_a.ipynb" in body["deleted_filenames"]
    assert "bulk_delete_b.ipynb" in body["deleted_filenames"]
    assert body["currently_compiled_notebook_deleted"] is False

    list_resp = client.get("/api/notebooks")
    filenames = {nb["filename"] for nb in list_resp.json()["notebooks"]}
    assert "bulk_delete_a.ipynb" not in filenames
    assert "bulk_delete_b.ipynb" not in filenames


def test_delete_all_notebooks_flags_currently_compiled_notebook_deleted():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "bulk_delete_currently_compiled.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "bulk_delete_currently_compiled.ipynb"},
    )
    assert compile_resp.status_code == 200

    delete_resp = client.delete("/api/notebooks?confirm=true")

    assert delete_resp.status_code == 200
    assert delete_resp.json()["currently_compiled_notebook_deleted"] is True


def test_delete_all_notebooks_does_not_touch_generated_dir():
    """Mirrors DELETE /api/notebooks/{filename}'s own behavior: the
    compiled app currently running must keep running exactly as before --
    this only ever clears UPLOAD_DIR, never GENERATED_DIR.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "bulk_delete_keeps_generated.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "bulk_delete_keeps_generated.ipynb"},
    )
    assert compile_resp.status_code == 200

    delete_resp = client.delete("/api/notebooks?confirm=true")
    assert delete_resp.status_code == 200

    generated_resp = client.get("/api/generated")
    assert generated_resp.status_code == 200
    assert "app.py" in generated_resp.json()["generated_files"]


def test_delete_all_notebooks_leaves_stale_part_files_alone():
    """Only ever removes ".ipynb" files directly inside UPLOAD_DIR -- the
    same set GET /api/notebooks already lists -- so an in-flight upload's
    own hidden ".part" temp file must never be touched by this.
    """

    stale_part_path = Path(UPLOAD_DIR) / ".bulk_delete_in_flight.ipynb.abc123.part"
    stale_part_path.write_bytes(b"not yet a real notebook")

    try:
        resp = client.delete("/api/notebooks?confirm=true")
        assert resp.status_code in (200, 400)
        assert stale_part_path.exists()
    finally:
        stale_part_path.unlink(missing_ok=True)


def test_delete_all_notebooks_returns_zero_when_nothing_uploaded():
    """UPLOAD_DIR isn't guaranteed empty at test time -- e.g. this repo
    ships uploads/sample.ipynb -- so this drains it first via the same
    endpoint under test rather than assuming a pristine directory, then
    confirms a second call against the now-genuinely-empty directory
    reports zero.
    """

    client.delete("/api/notebooks?confirm=true")

    resp = client.delete("/api/notebooks?confirm=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_count"] == 0
    assert body["deleted_filenames"] == []
    assert body["currently_compiled_notebook_deleted"] is False


def test_delete_notebooks_batch_removes_only_the_named_notebooks():

    _upload_sample_notebook("delete_batch_a.ipynb")
    _upload_sample_notebook("delete_batch_b.ipynb")
    _upload_sample_notebook("delete_batch_c.ipynb")

    resp = client.post(
        "/api/notebooks/delete-batch",
        json={"filenames": ["delete_batch_a.ipynb", "delete_batch_b.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["delete_batch_a.ipynb"]["status"] == "success"
    assert results_by_filename["delete_batch_b.ipynb"]["status"] == "success"

    assert client.get("/api/notebooks/delete_batch_a.ipynb").status_code == 404
    assert client.get("/api/notebooks/delete_batch_b.ipynb").status_code == 404
    # Untouched -- not named in the batch.
    assert client.get("/api/notebooks/delete_batch_c.ipynb").status_code == 200


def test_delete_notebooks_batch_reports_a_missing_filename_without_aborting_the_rest():

    _upload_sample_notebook("delete_batch_partial.ipynb")

    resp = client.post(
        "/api/notebooks/delete-batch",
        json={"filenames": ["delete_batch_partial.ipynb", "does_not_exist.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["delete_batch_partial.ipynb"]["status"] == "success"
    assert results_by_filename["does_not_exist.ipynb"]["status"] == "error"
    assert "not found" in results_by_filename["does_not_exist.ipynb"]["detail"]

    assert client.get("/api/notebooks/delete_batch_partial.ipynb").status_code == 404


def test_delete_notebooks_batch_flags_was_currently_compiled():

    _upload_sample_notebook("delete_batch_compiled.ipynb")

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "delete_batch_compiled.ipynb"}
    )
    assert compile_resp.status_code == 200

    resp = client.post(
        "/api/notebooks/delete-batch",
        json={"filenames": ["delete_batch_compiled.ipynb"]},
    )

    assert resp.status_code == 200
    assert resp.json()["results"][0]["was_currently_compiled"] is True


def test_delete_notebooks_batch_removes_tags_and_version_history():

    _upload_sample_notebook("delete_batch_cleanup.ipynb")
    client.put(
        "/api/notebooks/delete_batch_cleanup.ipynb/tags",
        json={"tags": ["scratch"]},
    )
    assert _tags_sidecar_path("delete_batch_cleanup.ipynb").is_file()

    resp = client.post(
        "/api/notebooks/delete-batch",
        json={"filenames": ["delete_batch_cleanup.ipynb"]},
    )

    assert resp.status_code == 200
    assert not _tags_sidecar_path("delete_batch_cleanup.ipynb").is_file()


def test_delete_notebooks_batch_rejects_a_non_list_filenames_value():

    resp = client.post(
        "/api/notebooks/delete-batch", json={"filenames": "not-a-list"}
    )

    assert resp.status_code == 400


def test_delete_notebooks_batch_rejects_an_empty_filenames_list():

    resp = client.post("/api/notebooks/delete-batch", json={"filenames": []})

    assert resp.status_code == 400


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


def test_rename_notebook_rejects_a_new_filename_with_an_embedded_null_byte():

    content = _notebook_bytes("def add(a, b):\n    return a + b\n")

    client.post(
        "/api/upload",
        files={"file": ("rename_null_byte_source.ipynb", io.BytesIO(content), "application/json")},
    )

    resp = client.patch(
        "/api/notebooks/rename_null_byte_source.ipynb",
        json={"new_filename": "evil\x00.ipynb"},
    )

    assert resp.status_code == 400
    assert (Path(UPLOAD_DIR) / "rename_null_byte_source.ipynb").is_file()


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


def test_rename_notebook_serializes_two_concurrent_renames_onto_the_same_destination():
    """Before _rename_lock_for existed, two concurrent renames of two
    different existing notebooks onto the same new_filename raced this
    endpoint's own check-then-write sequence: both requests' "does the
    destination already exist" check could observe "not yet" for both,
    since neither had written new_path yet when either checked -- and
    unlike upload_notebook (which at least re-checks immediately before
    its own swap), this endpoint had *no* re-check at all before
    os.replace(), so both proceeded straight through with no 409 raised
    by either, one silently clobbered the other's just-renamed file, and
    *both* callers saw "status": "success". Confirmed exploitable,
    reproduced directly before this fix: two threads racing this exact
    scenario against a live server produced two 200s in 19 of 20 single
    trials -- rename_notebook is a plain `def`, not `async def` (see
    test_blocking_endpoints_are_declared_as_plain_def_not_async_def), so
    FastAPI runs concurrent calls to it in its worker threadpool with
    genuine OS-thread parallelism, which is exactly why this reproduces
    far more reliably via plain `threading.Thread`s than the identical
    class of race in upload_notebook (an `async def` on a single event
    loop) needed a deterministic asyncio.gather-driven test for instead.
    Repeated here across several iterations (rather than a single trial)
    since even a ~95% single-trial hit rate leaves a real chance of a
    false pass; failing on any iteration is enough to catch a regression.
    """

    content_a = _notebook_bytes("def a():\n    return 1\n")
    content_b = _notebook_bytes("def b():\n    return 2\n")

    source_a = "rename_race_source_a.ipynb"
    source_b = "rename_race_source_b.ipynb"
    target = "rename_race_target.ipynb"
    target_path = Path(UPLOAD_DIR) / target

    try:

        for _ in range(15):

            for name, content in ((source_a, content_a), (source_b, content_b)):

                if not (Path(UPLOAD_DIR) / name).exists():
                    resp = client.post(
                        "/api/upload",
                        files={"file": (name, io.BytesIO(content), "application/json")},
                    )
                    assert resp.status_code == 200

            if target_path.exists():
                os.remove(target_path)

            results = []

            def do_rename(source_name, tag):
                resp = client.patch(
                    f"/api/notebooks/{source_name}",
                    json={"new_filename": target},
                )
                results.append((tag, resp.status_code))

            t1 = threading.Thread(target=do_rename, args=(source_a, "A"))
            t2 = threading.Thread(target=do_rename, args=(source_b, "B"))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            assert not t1.is_alive() and not t2.is_alive(), "a request never returned -- deadlock"
            assert len(results) == 2

            statuses = sorted(r[1] for r in results)
            # Exactly one must succeed and the other must be rejected with
            # the same 409 a sequential rename onto an existing filename
            # without "overwrite": true already gets -- never two silent
            # 200s.
            assert statuses == [200, 409], results

            # Whichever source lost the race is still sitting where it
            # started -- the collision was rejected outright, not
            # silently clobbered.
            remaining_sources = [
                name for name in (source_a, source_b)
                if (Path(UPLOAD_DIR) / name).exists()
            ]
            assert len(remaining_sources) == 1
            assert target_path.is_file()

    finally:
        for name in (source_a, source_b, target):
            path = Path(UPLOAD_DIR) / name
            if path.exists():
                os.remove(path)


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


def _upload_sample_notebook(filename):
    resp = client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200


def test_copy_notebook_duplicates_the_file_and_keeps_the_source():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("copy_source.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    copy_resp = client.post(
        "/api/notebooks/copy_source.ipynb/copy",
        json={"new_filename": "copy_target.ipynb"},
    )

    assert copy_resp.status_code == 200
    assert copy_resp.json() == {
        "status": "success",
        "filename": "copy_source.ipynb",
        "new_filename": "copy_target.ipynb",
    }

    assert (Path(UPLOAD_DIR) / "copy_source.ipynb").is_file()
    assert (Path(UPLOAD_DIR) / "copy_target.ipynb").is_file()
    assert (
        (Path(UPLOAD_DIR) / "copy_source.ipynb").read_bytes()
        == (Path(UPLOAD_DIR) / "copy_target.ipynb").read_bytes()
    )

    filenames = {nb["filename"] for nb in client.get("/api/notebooks").json()["notebooks"]}
    assert "copy_source.ipynb" in filenames
    assert "copy_target.ipynb" in filenames

    os.remove(Path(UPLOAD_DIR) / "copy_target.ipynb")


def test_copy_notebook_returns_404_for_missing_source():

    resp = client.post(
        "/api/notebooks/does_not_exist_at_all.ipynb/copy",
        json={"new_filename": "whatever.ipynb"},
    )

    assert resp.status_code == 404


def test_copy_notebook_requires_new_filename():

    _upload_sample_notebook("copy_missing_target.ipynb")

    resp = client.post(
        "/api/notebooks/copy_missing_target.ipynb/copy",
        json={},
    )

    assert resp.status_code == 400


def test_copy_notebook_rejects_a_non_ipynb_target_name():

    _upload_sample_notebook("copy_bad_ext.ipynb")

    resp = client.post(
        "/api/notebooks/copy_bad_ext.ipynb/copy",
        json={"new_filename": "copy_bad_ext.txt"},
    )

    assert resp.status_code == 400


def test_copy_notebook_rejects_a_traversal_target_name():

    _upload_sample_notebook("copy_traversal_source.ipynb")

    resp = client.post(
        "/api/notebooks/copy_traversal_source.ipynb/copy",
        json={"new_filename": "../../../../etc/passwd.ipynb"},
    )

    assert resp.status_code == 400


def test_copy_notebook_rejects_copying_onto_its_own_name():

    _upload_sample_notebook("copy_self.ipynb")

    resp = client.post(
        "/api/notebooks/copy_self.ipynb/copy",
        json={"new_filename": "copy_self.ipynb"},
    )

    assert resp.status_code == 400


def test_copy_notebook_rejects_collision_without_overwrite():

    _upload_sample_notebook("copy_collision_source.ipynb")
    _upload_sample_notebook("copy_collision_target.ipynb")

    resp = client.post(
        "/api/notebooks/copy_collision_source.ipynb/copy",
        json={"new_filename": "copy_collision_target.ipynb"},
    )

    assert resp.status_code == 409
    os.remove(Path(UPLOAD_DIR) / "copy_collision_target.ipynb")


def test_copy_notebook_overwrites_when_requested():

    content_a = _notebook_bytes("def a() -> int:\n    return 1\n")
    content_b = _notebook_bytes("def b() -> int:\n    return 2\n")

    client.post(
        "/api/upload",
        files={"file": ("copy_overwrite_source.ipynb", io.BytesIO(content_a), "application/json")},
    )
    client.post(
        "/api/upload",
        files={"file": ("copy_overwrite_target.ipynb", io.BytesIO(content_b), "application/json")},
    )

    copy_resp = client.post(
        "/api/notebooks/copy_overwrite_source.ipynb/copy",
        json={"new_filename": "copy_overwrite_target.ipynb", "overwrite": True},
    )

    assert copy_resp.status_code == 200
    assert (
        (Path(UPLOAD_DIR) / "copy_overwrite_target.ipynb").read_bytes() == content_a
    )

    os.remove(Path(UPLOAD_DIR) / "copy_overwrite_target.ipynb")


def test_copy_notebook_copies_tags_from_the_source():

    _upload_sample_notebook("copy_tags_source.ipynb")
    client.put(
        "/api/notebooks/copy_tags_source.ipynb/tags", json={"tags": ["bug"]}
    )

    copy_resp = client.post(
        "/api/notebooks/copy_tags_source.ipynb/copy",
        json={"new_filename": "copy_tags_target.ipynb"},
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/copy_tags_source.ipynb/tags"
    ).json()["tags"] == ["bug"]
    assert client.get(
        "/api/notebooks/copy_tags_target.ipynb/tags"
    ).json()["tags"] == ["bug"]

    os.remove(Path(UPLOAD_DIR) / "copy_tags_target.ipynb")
    _tags_sidecar_path("copy_tags_target.ipynb").unlink(missing_ok=True)


def test_copy_notebook_overwrite_discards_the_destinations_previous_tags():

    _upload_sample_notebook("copy_tags_overwrite_source.ipynb")
    _upload_sample_notebook("copy_tags_overwrite_target.ipynb")
    client.put(
        "/api/notebooks/copy_tags_overwrite_target.ipynb/tags",
        json={"tags": ["stale"]},
    )

    copy_resp = client.post(
        "/api/notebooks/copy_tags_overwrite_source.ipynb/copy",
        json={
            "new_filename": "copy_tags_overwrite_target.ipynb",
            "overwrite": True,
        },
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/copy_tags_overwrite_target.ipynb/tags"
    ).json()["tags"] == []

    os.remove(Path(UPLOAD_DIR) / "copy_tags_overwrite_target.ipynb")


def test_copy_notebook_does_not_copy_version_history():

    _upload_sample_notebook("copy_versions_source.ipynb")
    # Overwriting a notebook snapshots its previous content -- see
    # _snapshot_current_notebook_version -- giving copy_versions_source.ipynb
    # a non-empty version history to (deliberately) not copy.
    client.post(
        "/api/upload",
        files={
            "file": (
                "copy_versions_source.ipynb",
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 2\n")),
                "application/json",
            )
        },
        params={"overwrite": "true"},
    )
    assert client.get(
        "/api/notebooks/copy_versions_source.ipynb/versions"
    ).json()["versions"]

    copy_resp = client.post(
        "/api/notebooks/copy_versions_source.ipynb/copy",
        json={"new_filename": "copy_versions_target.ipynb"},
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/copy_versions_target.ipynb/versions"
    ).json()["versions"] == []

    os.remove(Path(UPLOAD_DIR) / "copy_versions_target.ipynb")


def test_copy_notebook_overwrite_discards_the_destinations_previous_version_history():

    _upload_sample_notebook("copy_versions_overwrite_source.ipynb")
    _upload_sample_notebook("copy_versions_overwrite_target.ipynb")
    client.post(
        "/api/upload",
        files={
            "file": (
                "copy_versions_overwrite_target.ipynb",
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 2\n")),
                "application/json",
            )
        },
        params={"overwrite": "true"},
    )
    assert client.get(
        "/api/notebooks/copy_versions_overwrite_target.ipynb/versions"
    ).json()["versions"]

    copy_resp = client.post(
        "/api/notebooks/copy_versions_overwrite_source.ipynb/copy",
        json={
            "new_filename": "copy_versions_overwrite_target.ipynb",
            "overwrite": True,
        },
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/copy_versions_overwrite_target.ipynb/versions"
    ).json()["versions"] == []

    os.remove(Path(UPLOAD_DIR) / "copy_versions_overwrite_target.ipynb")


def test_copy_notebook_does_not_affect_the_currently_compiled_source():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("copy_compiled_source.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile", json={"notebook_path": "copy_compiled_source.ipynb"}
    )
    assert compile_resp.status_code == 200

    copy_resp = client.post(
        "/api/notebooks/copy_compiled_source.ipynb/copy",
        json={"new_filename": "copy_compiled_target.ipynb"},
    )
    assert copy_resp.status_code == 200

    notebooks = {
        nb["filename"]: nb for nb in client.get("/api/notebooks").json()["notebooks"]
    }
    assert notebooks["copy_compiled_source.ipynb"]["currently_compiled"] is True
    assert notebooks["copy_compiled_target.ipynb"]["currently_compiled"] is False

    os.remove(Path(UPLOAD_DIR) / "copy_compiled_target.ipynb")


def test_copy_notebook_batch_duplicates_the_source_under_every_new_filename():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={"file": ("copy_batch_source.ipynb", io.BytesIO(content), "application/json")},
    )
    assert upload_resp.status_code == 200
    client.put(
        "/api/notebooks/copy_batch_source.ipynb/tags", json={"tags": ["template"]}
    )

    resp = client.post(
        "/api/notebooks/copy_batch_source.ipynb/copy-batch",
        json={"new_filenames": ["copy_batch_a.ipynb", "copy_batch_b.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["filename"] == "copy_batch_source.ipynb"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0
    assert [r["new_filename"] for r in body["results"]] == [
        "copy_batch_a.ipynb", "copy_batch_b.ipynb",
    ]
    assert all(r["status"] == "success" for r in body["results"])

    assert (Path(UPLOAD_DIR) / "copy_batch_a.ipynb").read_bytes() == content
    assert (Path(UPLOAD_DIR) / "copy_batch_b.ipynb").read_bytes() == content
    # Source's own tags are inherited by each copy.
    assert client.get(
        "/api/notebooks/copy_batch_a.ipynb/tags"
    ).json()["tags"] == ["template"]
    # Source itself is untouched.
    assert (Path(UPLOAD_DIR) / "copy_batch_source.ipynb").read_bytes() == content


def test_copy_notebook_batch_reports_a_collision_without_aborting_the_rest():

    _upload_sample_notebook("copy_batch_partial_source.ipynb")
    _upload_sample_notebook("copy_batch_partial_existing.ipynb")

    resp = client.post(
        "/api/notebooks/copy_batch_partial_source.ipynb/copy-batch",
        json={
            "new_filenames": [
                "copy_batch_partial_new.ipynb", "copy_batch_partial_existing.ipynb",
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["new_filename"]: r for r in body["results"]}
    assert results_by_filename["copy_batch_partial_new.ipynb"]["status"] == "success"
    assert results_by_filename["copy_batch_partial_existing.ipynb"]["status"] == "error"
    assert "already exists" in results_by_filename["copy_batch_partial_existing.ipynb"]["detail"]

    assert (Path(UPLOAD_DIR) / "copy_batch_partial_new.ipynb").is_file()


def test_copy_notebook_batch_overwrite_applies_to_every_destination():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    client.post(
        "/api/upload",
        files={"file": ("copy_batch_overwrite_source.ipynb", io.BytesIO(content), "application/json")},
    )
    _upload_sample_notebook("copy_batch_overwrite_existing.ipynb")

    resp = client.post(
        "/api/notebooks/copy_batch_overwrite_source.ipynb/copy-batch",
        json={
            "new_filenames": ["copy_batch_overwrite_existing.ipynb"],
            "overwrite": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert (Path(UPLOAD_DIR) / "copy_batch_overwrite_existing.ipynb").read_bytes() == content


def test_copy_notebook_batch_returns_404_for_missing_source():

    resp = client.post(
        "/api/notebooks/does_not_exist_at_all.ipynb/copy-batch",
        json={"new_filenames": ["whatever.ipynb"]},
    )

    assert resp.status_code == 404


def test_copy_notebook_batch_rejects_a_non_list_new_filenames_value():

    _upload_sample_notebook("copy_batch_bad_input.ipynb")

    resp = client.post(
        "/api/notebooks/copy_batch_bad_input.ipynb/copy-batch",
        json={"new_filenames": "not-a-list"},
    )

    assert resp.status_code == 400


def test_copy_notebook_batch_rejects_an_empty_new_filenames_list():

    _upload_sample_notebook("copy_batch_empty_input.ipynb")

    resp = client.post(
        "/api/notebooks/copy_batch_empty_input.ipynb/copy-batch",
        json={"new_filenames": []},
    )

    assert resp.status_code == 400


def test_copy_notebook_batch_does_not_copy_version_history():

    filename = "copy_batch_versions_source.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    assert len(client.get(f"/api/notebooks/{filename}/versions").json()["versions"]) == 1

    resp = client.post(
        f"/api/notebooks/{filename}/copy-batch",
        json={"new_filenames": ["copy_batch_versions_target.ipynb"]},
    )

    assert resp.status_code == 200
    assert client.get(
        "/api/notebooks/copy_batch_versions_target.ipynb/versions"
    ).json()["versions"] == []


def test_get_notebook_tags_is_empty_for_a_never_tagged_notebook():

    _upload_sample_notebook("tags_untagged.ipynb")

    resp = client.get("/api/notebooks/tags_untagged.ipynb/tags")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "filename": "tags_untagged.ipynb",
        "tags": [],
    }


def test_get_notebook_tags_returns_404_for_missing_file():

    resp = client.get("/api/notebooks/tags_does_not_exist.ipynb/tags")

    assert resp.status_code == 404


def test_set_notebook_tags_returns_404_for_missing_file():

    resp = client.put(
        "/api/notebooks/tags_does_not_exist.ipynb/tags",
        json={"tags": ["bug"]},
    )

    assert resp.status_code == 404


def test_set_notebook_tags_persists_and_is_readable_back():

    _upload_sample_notebook("tags_persist.ipynb")

    set_resp = client.put(
        "/api/notebooks/tags_persist.ipynb/tags",
        json={"tags": ["production", "bug"]},
    )

    assert set_resp.status_code == 200
    assert set_resp.json() == {
        "status": "success",
        "filename": "tags_persist.ipynb",
        "tags": ["bug", "production"],
    }

    get_resp = client.get("/api/notebooks/tags_persist.ipynb/tags")

    assert get_resp.json()["tags"] == ["bug", "production"]


def test_set_notebook_tags_strips_whitespace_and_deduplicates():

    _upload_sample_notebook("tags_dedupe.ipynb")

    resp = client.put(
        "/api/notebooks/tags_dedupe.ipynb/tags",
        json={"tags": ["bug", "  bug  ", "feature"]},
    )

    assert resp.status_code == 200
    assert resp.json()["tags"] == ["bug", "feature"]


def test_set_notebook_tags_with_empty_list_clears_tags_and_removes_the_sidecar_file():

    _upload_sample_notebook("tags_clear.ipynb")

    client.put("/api/notebooks/tags_clear.ipynb/tags", json={"tags": ["bug"]})
    assert _tags_sidecar_path("tags_clear.ipynb").is_file()

    clear_resp = client.put("/api/notebooks/tags_clear.ipynb/tags", json={"tags": []})

    assert clear_resp.status_code == 200
    assert clear_resp.json()["tags"] == []
    assert not _tags_sidecar_path("tags_clear.ipynb").is_file()


def test_set_notebook_tags_rejects_a_non_list_tags_value():

    _upload_sample_notebook("tags_not_a_list.ipynb")

    resp = client.put(
        "/api/notebooks/tags_not_a_list.ipynb/tags",
        json={"tags": "bug"},
    )

    assert resp.status_code == 400


def test_set_notebook_tags_rejects_a_non_string_tag():

    _upload_sample_notebook("tags_non_string.ipynb")

    resp = client.put(
        "/api/notebooks/tags_non_string.ipynb/tags",
        json={"tags": ["bug", 5]},
    )

    assert resp.status_code == 400


def test_set_notebook_tags_rejects_an_empty_or_whitespace_only_tag():

    _upload_sample_notebook("tags_blank.ipynb")

    resp = client.put(
        "/api/notebooks/tags_blank.ipynb/tags",
        json={"tags": ["   "]},
    )

    assert resp.status_code == 400


def test_set_notebook_tags_rejects_a_tag_over_the_max_length():

    _upload_sample_notebook("tags_too_long.ipynb")

    resp = client.put(
        "/api/notebooks/tags_too_long.ipynb/tags",
        json={"tags": ["x" * 51]},
    )

    assert resp.status_code == 400


def test_set_notebook_tags_rejects_more_than_the_max_distinct_tags():

    _upload_sample_notebook("tags_too_many.ipynb")

    resp = client.put(
        "/api/notebooks/tags_too_many.ipynb/tags",
        json={"tags": [f"tag{i}" for i in range(21)]},
    )

    assert resp.status_code == 400


def test_set_notebook_tags_batch_sets_each_notebooks_own_distinct_tags():

    _upload_sample_notebook("tags_batch_a.ipynb")
    _upload_sample_notebook("tags_batch_b.ipynb")
    client.put(
        "/api/notebooks/tags_batch_a.ipynb/tags", json={"tags": ["stale"]}
    )

    resp = client.post(
        "/api/notebooks/tags-batch",
        json={
            "entries": [
                {"filename": "tags_batch_a.ipynb", "tags": ["production", "v2"]},
                {"filename": "tags_batch_b.ipynb", "tags": ["bug"]},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["tags_batch_a.ipynb"]["status"] == "success"
    assert results_by_filename["tags_batch_a.ipynb"]["tags"] == ["production", "v2"]
    assert results_by_filename["tags_batch_b.ipynb"]["tags"] == ["bug"]

    # A full replace, not a merge -- "stale" is gone.
    assert client.get(
        "/api/notebooks/tags_batch_a.ipynb/tags"
    ).json()["tags"] == ["production", "v2"]


def test_set_notebook_tags_batch_with_an_empty_tags_list_clears_that_entry():

    _upload_sample_notebook("tags_batch_clear.ipynb")
    client.put("/api/notebooks/tags_batch_clear.ipynb/tags", json={"tags": ["bug"]})

    resp = client.post(
        "/api/notebooks/tags-batch",
        json={"entries": [{"filename": "tags_batch_clear.ipynb", "tags": []}]},
    )

    assert resp.status_code == 200
    assert resp.json()["results"][0]["tags"] == []
    assert client.get(
        "/api/notebooks/tags_batch_clear.ipynb/tags"
    ).json()["tags"] == []


def test_set_notebook_tags_batch_reports_a_missing_filename_without_aborting_the_rest():

    _upload_sample_notebook("tags_batch_partial.ipynb")

    resp = client.post(
        "/api/notebooks/tags-batch",
        json={
            "entries": [
                {"filename": "tags_batch_partial.ipynb", "tags": ["urgent"]},
                {"filename": "does_not_exist.ipynb", "tags": ["urgent"]},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["tags_batch_partial.ipynb"]["status"] == "success"
    assert results_by_filename["does_not_exist.ipynb"]["status"] == "error"
    assert "not found" in results_by_filename["does_not_exist.ipynb"]["detail"]


def test_set_notebook_tags_batch_reports_an_invalid_tags_value_for_just_that_entry():

    _upload_sample_notebook("tags_batch_bad_tag_a.ipynb")
    _upload_sample_notebook("tags_batch_bad_tag_b.ipynb")

    resp = client.post(
        "/api/notebooks/tags-batch",
        json={
            "entries": [
                {"filename": "tags_batch_bad_tag_a.ipynb", "tags": ["ok"]},
                {"filename": "tags_batch_bad_tag_b.ipynb", "tags": ["   "]},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["tags_batch_bad_tag_a.ipynb"]["status"] == "success"
    assert results_by_filename["tags_batch_bad_tag_b.ipynb"]["status"] == "error"
    assert "whitespace-only" in results_by_filename["tags_batch_bad_tag_b.ipynb"]["detail"]

    # The failing entry never got its tags touched.
    assert client.get(
        "/api/notebooks/tags_batch_bad_tag_b.ipynb/tags"
    ).json()["tags"] == []


def test_set_notebook_tags_batch_rejects_a_non_list_entries_value():

    resp = client.post("/api/notebooks/tags-batch", json={"entries": "not-a-list"})

    assert resp.status_code == 400


def test_set_notebook_tags_batch_rejects_an_empty_entries_list():

    resp = client.post("/api/notebooks/tags-batch", json={"entries": []})

    assert resp.status_code == 400


def test_set_notebook_tags_batch_rejects_an_entry_missing_a_filename():

    resp = client.post(
        "/api/notebooks/tags-batch",
        json={"entries": [{"tags": ["bug"]}]},
    )

    assert resp.status_code == 400


def test_get_notebook_description_is_empty_for_a_never_described_notebook():

    _upload_sample_notebook("description_unset.ipynb")

    resp = client.get("/api/notebooks/description_unset.ipynb/description")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "filename": "description_unset.ipynb",
        "description": "",
    }


def test_get_notebook_description_returns_404_for_missing_file():

    resp = client.get("/api/notebooks/description_does_not_exist.ipynb/description")

    assert resp.status_code == 404


def test_set_notebook_description_returns_404_for_missing_file():

    resp = client.put(
        "/api/notebooks/description_does_not_exist.ipynb/description",
        json={"description": "hello"},
    )

    assert resp.status_code == 404


def test_set_notebook_description_persists_and_is_readable_back():

    _upload_sample_notebook("description_persist.ipynb")

    set_resp = client.put(
        "/api/notebooks/description_persist.ipynb/description",
        json={"description": "The quarterly churn model, retrained monthly."},
    )

    assert set_resp.status_code == 200
    assert set_resp.json() == {
        "status": "success",
        "filename": "description_persist.ipynb",
        "description": "The quarterly churn model, retrained monthly.",
    }

    get_resp = client.get("/api/notebooks/description_persist.ipynb/description")
    assert get_resp.json()["description"] == "The quarterly churn model, retrained monthly."


def test_set_notebook_description_strips_surrounding_whitespace():

    _upload_sample_notebook("description_strip.ipynb")

    resp = client.put(
        "/api/notebooks/description_strip.ipynb/description",
        json={"description": "   needs whitespace stripped   "},
    )

    assert resp.status_code == 200
    assert resp.json()["description"] == "needs whitespace stripped"


def test_set_notebook_description_with_empty_string_clears_it_and_removes_the_sidecar_file():

    _upload_sample_notebook("description_clear.ipynb")

    client.put(
        "/api/notebooks/description_clear.ipynb/description",
        json={"description": "temporary"},
    )
    assert _description_sidecar_path("description_clear.ipynb").is_file()

    clear_resp = client.put(
        "/api/notebooks/description_clear.ipynb/description",
        json={"description": ""},
    )

    assert clear_resp.status_code == 200
    assert clear_resp.json()["description"] == ""
    assert not _description_sidecar_path("description_clear.ipynb").is_file()


def test_set_notebook_description_defaults_to_clearing_when_omitted():

    _upload_sample_notebook("description_omitted.ipynb")

    client.put(
        "/api/notebooks/description_omitted.ipynb/description",
        json={"description": "temporary"},
    )

    resp = client.put("/api/notebooks/description_omitted.ipynb/description", json={})

    assert resp.status_code == 200
    assert resp.json()["description"] == ""


def test_set_notebook_description_rejects_a_non_string_value():

    _upload_sample_notebook("description_not_a_string.ipynb")

    resp = client.put(
        "/api/notebooks/description_not_a_string.ipynb/description",
        json={"description": 5},
    )

    assert resp.status_code == 400


def test_set_notebook_description_rejects_a_description_over_the_max_length():

    _upload_sample_notebook("description_too_long.ipynb")

    resp = client.put(
        "/api/notebooks/description_too_long.ipynb/description",
        json={"description": "x" * 2001},
    )

    assert resp.status_code == 400


def test_notebook_list_and_info_include_the_description_field():

    _upload_sample_notebook("description_in_list.ipynb")
    client.put(
        "/api/notebooks/description_in_list.ipynb/description",
        json={"description": "shown in listings"},
    )

    list_entry = next(
        nb for nb in client.get("/api/notebooks").json()["notebooks"]
        if nb["filename"] == "description_in_list.ipynb"
    )
    assert list_entry["description"] == "shown in listings"

    info_resp = client.get("/api/notebooks/description_in_list.ipynb/info")
    assert info_resp.json()["description"] == "shown in listings"


def test_set_notebook_description_batch_sets_each_notebooks_own_distinct_description():

    _upload_sample_notebook("description_batch_a.ipynb")
    _upload_sample_notebook("description_batch_b.ipynb")
    client.put(
        "/api/notebooks/description_batch_a.ipynb/description",
        json={"description": "stale"},
    )

    resp = client.post(
        "/api/notebooks/description-batch",
        json={
            "entries": [
                {"filename": "description_batch_a.ipynb", "description": "The churn model."},
                {"filename": "description_batch_b.ipynb", "description": "The pricing model."},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["description_batch_a.ipynb"]["status"] == "success"
    assert results_by_filename["description_batch_a.ipynb"]["description"] == "The churn model."
    assert results_by_filename["description_batch_b.ipynb"]["description"] == "The pricing model."

    # A full replace -- "stale" is gone.
    assert client.get(
        "/api/notebooks/description_batch_a.ipynb/description"
    ).json()["description"] == "The churn model."


def test_set_notebook_description_batch_with_an_empty_description_clears_that_entry():

    _upload_sample_notebook("description_batch_clear.ipynb")
    client.put(
        "/api/notebooks/description_batch_clear.ipynb/description",
        json={"description": "temporary"},
    )

    resp = client.post(
        "/api/notebooks/description-batch",
        json={"entries": [{"filename": "description_batch_clear.ipynb", "description": ""}]},
    )

    assert resp.status_code == 200
    assert resp.json()["results"][0]["description"] == ""
    assert client.get(
        "/api/notebooks/description_batch_clear.ipynb/description"
    ).json()["description"] == ""


def test_set_notebook_description_batch_defaults_to_clearing_when_omitted():

    _upload_sample_notebook("description_batch_omitted.ipynb")
    client.put(
        "/api/notebooks/description_batch_omitted.ipynb/description",
        json={"description": "temporary"},
    )

    resp = client.post(
        "/api/notebooks/description-batch",
        json={"entries": [{"filename": "description_batch_omitted.ipynb"}]},
    )

    assert resp.status_code == 200
    assert resp.json()["results"][0]["description"] == ""


def test_set_notebook_description_batch_strips_surrounding_whitespace():

    _upload_sample_notebook("description_batch_strip.ipynb")

    resp = client.post(
        "/api/notebooks/description-batch",
        json={
            "entries": [
                {"filename": "description_batch_strip.ipynb", "description": "  padded  "},
            ],
        },
    )

    assert resp.status_code == 200
    assert resp.json()["results"][0]["description"] == "padded"


def test_set_notebook_description_batch_reports_a_missing_filename_without_aborting_the_rest():

    _upload_sample_notebook("description_batch_partial.ipynb")

    resp = client.post(
        "/api/notebooks/description-batch",
        json={
            "entries": [
                {"filename": "description_batch_partial.ipynb", "description": "ok"},
                {"filename": "does_not_exist.ipynb", "description": "ok"},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["description_batch_partial.ipynb"]["status"] == "success"
    assert results_by_filename["does_not_exist.ipynb"]["status"] == "error"
    assert "not found" in results_by_filename["does_not_exist.ipynb"]["detail"]


def test_set_notebook_description_batch_reports_an_invalid_description_for_just_that_entry():

    _upload_sample_notebook("description_batch_bad_a.ipynb")
    _upload_sample_notebook("description_batch_bad_b.ipynb")

    resp = client.post(
        "/api/notebooks/description-batch",
        json={
            "entries": [
                {"filename": "description_batch_bad_a.ipynb", "description": "ok"},
                {"filename": "description_batch_bad_b.ipynb", "description": 5},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["description_batch_bad_a.ipynb"]["status"] == "success"
    assert results_by_filename["description_batch_bad_b.ipynb"]["status"] == "error"
    assert "must be a string" in results_by_filename["description_batch_bad_b.ipynb"]["detail"]

    # The failing entry never got its description touched.
    assert client.get(
        "/api/notebooks/description_batch_bad_b.ipynb/description"
    ).json()["description"] == ""


def test_set_notebook_description_batch_reports_a_too_long_description_for_just_that_entry():

    _upload_sample_notebook("description_batch_too_long.ipynb")

    resp = client.post(
        "/api/notebooks/description-batch",
        json={
            "entries": [
                {"filename": "description_batch_too_long.ipynb", "description": "x" * 2001},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["failed_count"] == 1
    assert "at most" in body["results"][0]["detail"]


def test_set_notebook_description_batch_rejects_a_non_list_entries_value():

    resp = client.post("/api/notebooks/description-batch", json={"entries": "not-a-list"})

    assert resp.status_code == 400


def test_set_notebook_description_batch_rejects_an_empty_entries_list():

    resp = client.post("/api/notebooks/description-batch", json={"entries": []})

    assert resp.status_code == 400


def test_set_notebook_description_batch_rejects_an_entry_missing_a_filename():

    resp = client.post(
        "/api/notebooks/description-batch",
        json={"entries": [{"description": "ok"}]},
    )

    assert resp.status_code == 400


def test_rename_notebook_moves_its_description():

    _upload_sample_notebook("description_rename_source.ipynb")
    client.put(
        "/api/notebooks/description_rename_source.ipynb/description",
        json={"description": "moves with the rename"},
    )

    rename_resp = client.patch(
        "/api/notebooks/description_rename_source.ipynb",
        json={"new_filename": "description_rename_target.ipynb"},
    )
    assert rename_resp.status_code == 200

    assert client.get(
        "/api/notebooks/description_rename_target.ipynb/description"
    ).json()["description"] == "moves with the rename"
    assert not _description_sidecar_path("description_rename_source.ipynb").is_file()

    os.remove(Path(UPLOAD_DIR) / "description_rename_target.ipynb")
    _description_sidecar_path("description_rename_target.ipynb").unlink(missing_ok=True)


def test_copy_notebook_copies_the_description_from_the_source():

    _upload_sample_notebook("description_copy_source.ipynb")
    client.put(
        "/api/notebooks/description_copy_source.ipynb/description",
        json={"description": "copied along with the content"},
    )

    copy_resp = client.post(
        "/api/notebooks/description_copy_source.ipynb/copy",
        json={"new_filename": "description_copy_target.ipynb"},
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/description_copy_target.ipynb/description"
    ).json()["description"] == "copied along with the content"

    os.remove(Path(UPLOAD_DIR) / "description_copy_target.ipynb")
    _description_sidecar_path("description_copy_target.ipynb").unlink(missing_ok=True)


def test_copy_notebook_version_does_not_inherit_the_current_description():

    filename = "description_versions_copy_source.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    client.put(
        f"/api/notebooks/{filename}/description",
        json={"description": "the live notebook's own description"},
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    copy_resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": "description_versions_copy_target.ipynb"},
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/description_versions_copy_target.ipynb/description"
    ).json()["description"] == ""

    os.remove(Path(UPLOAD_DIR) / "description_versions_copy_target.ipynb")


def test_delete_notebook_removes_its_description_sidecar_file():

    _upload_sample_notebook("description_delete.ipynb")
    client.put(
        "/api/notebooks/description_delete.ipynb/description",
        json={"description": "goes away with the notebook"},
    )
    assert _description_sidecar_path("description_delete.ipynb").is_file()

    delete_resp = client.delete("/api/notebooks/description_delete.ipynb")
    assert delete_resp.status_code == 200

    assert not _description_sidecar_path("description_delete.ipynb").is_file()


def test_list_tags_response_has_the_expected_shape():

    resp = client.get("/api/tags")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert isinstance(body["tags"], list)
    for entry in body["tags"]:
        assert set(entry) == {"tag", "notebook_count"}


def test_list_tags_reports_distinct_tags_with_notebook_counts():

    _upload_sample_notebook("tags_catalog_one.ipynb")
    _upload_sample_notebook("tags_catalog_two.ipynb")
    _upload_sample_notebook("tags_catalog_three.ipynb")

    client.put(
        "/api/notebooks/tags_catalog_one.ipynb/tags",
        json={"tags": ["production", "bug"]},
    )
    client.put(
        "/api/notebooks/tags_catalog_two.ipynb/tags",
        json={"tags": ["production"]},
    )
    # tags_catalog_three.ipynb is left untagged -- it should contribute
    # nothing to the catalog.

    resp = client.get("/api/tags")

    assert resp.status_code == 200

    by_tag = {entry["tag"]: entry["notebook_count"] for entry in resp.json()["tags"]}

    assert by_tag["production"] == 2
    assert by_tag["bug"] == 1


def test_list_tags_reflects_a_tag_set_being_cleared():

    _upload_sample_notebook("tags_catalog_cleared.ipynb")

    client.put(
        "/api/notebooks/tags_catalog_cleared.ipynb/tags",
        json={"tags": ["scratch"]},
    )
    assert "scratch" in {
        entry["tag"] for entry in client.get("/api/tags").json()["tags"]
    }

    client.put(
        "/api/notebooks/tags_catalog_cleared.ipynb/tags",
        json={"tags": []},
    )

    assert "scratch" not in {
        entry["tag"] for entry in client.get("/api/tags").json()["tags"]
    }


def test_list_tags_are_sorted_alphabetically():

    _upload_sample_notebook("tags_catalog_sort.ipynb")

    client.put(
        "/api/notebooks/tags_catalog_sort.ipynb/tags",
        json={"tags": ["zeta", "alpha", "mu"]},
    )

    resp = client.get("/api/tags")

    tag_names = [entry["tag"] for entry in resp.json()["tags"]]

    assert tag_names == sorted(tag_names)
    assert {"zeta", "alpha", "mu"}.issubset(set(tag_names))


def test_delete_tag_removes_it_from_every_notebook_that_has_it():

    _upload_sample_notebook("tags_bulk_delete_a.ipynb")
    _upload_sample_notebook("tags_bulk_delete_b.ipynb")
    _upload_sample_notebook("tags_bulk_delete_c.ipynb")

    client.put(
        "/api/notebooks/tags_bulk_delete_a.ipynb/tags",
        json={"tags": ["scratch", "bug"]},
    )
    client.put(
        "/api/notebooks/tags_bulk_delete_b.ipynb/tags",
        json={"tags": ["scratch"]},
    )
    client.put(
        "/api/notebooks/tags_bulk_delete_c.ipynb/tags",
        json={"tags": ["production"]},
    )

    delete_resp = client.delete("/api/tags/scratch")

    assert delete_resp.status_code == 200
    body = delete_resp.json()
    assert body["status"] == "success"
    assert body["tag"] == "scratch"
    assert sorted(body["affected_notebooks"]) == [
        "tags_bulk_delete_a.ipynb", "tags_bulk_delete_b.ipynb",
    ]
    assert body["notebook_count"] == 2

    assert client.get(
        "/api/notebooks/tags_bulk_delete_a.ipynb/tags"
    ).json()["tags"] == ["bug"]
    assert client.get(
        "/api/notebooks/tags_bulk_delete_b.ipynb/tags"
    ).json()["tags"] == []
    # Untouched -- never carried "scratch" at all.
    assert client.get(
        "/api/notebooks/tags_bulk_delete_c.ipynb/tags"
    ).json()["tags"] == ["production"]

    assert "scratch" not in {
        entry["tag"] for entry in client.get("/api/tags").json()["tags"]
    }


def test_delete_tag_is_a_no_op_success_when_nothing_carries_it():

    resp = client.delete("/api/tags/this-tag-does-not-exist-anywhere")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "tag": "this-tag-does-not-exist-anywhere",
        "affected_notebooks": [],
        "notebook_count": 0,
    }


def test_delete_tag_removes_the_sidecar_file_when_it_was_the_only_tag():

    _upload_sample_notebook("tags_bulk_delete_only_tag.ipynb")
    client.put(
        "/api/notebooks/tags_bulk_delete_only_tag.ipynb/tags",
        json={"tags": ["temporary"]},
    )
    assert _tags_sidecar_path("tags_bulk_delete_only_tag.ipynb").is_file()

    delete_resp = client.delete("/api/tags/temporary")

    assert delete_resp.status_code == 200
    assert delete_resp.json()["affected_notebooks"] == [
        "tags_bulk_delete_only_tag.ipynb"
    ]
    assert not _tags_sidecar_path("tags_bulk_delete_only_tag.ipynb").is_file()


def test_apply_tag_adds_it_to_every_named_notebook_preserving_existing_tags():

    _upload_sample_notebook("tags_apply_a.ipynb")
    _upload_sample_notebook("tags_apply_b.ipynb")
    client.put(
        "/api/notebooks/tags_apply_a.ipynb/tags", json={"tags": ["bug"]}
    )

    resp = client.post(
        "/api/tags/production/apply",
        json={"filenames": ["tags_apply_a.ipynb", "tags_apply_b.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["tag"] == "production"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["tags_apply_a.ipynb"]["status"] == "success"
    assert results_by_filename["tags_apply_a.ipynb"]["tags"] == ["bug", "production"]
    assert results_by_filename["tags_apply_b.ipynb"]["tags"] == ["production"]

    # Existing tags weren't clobbered -- "bug" survives alongside the newly
    # applied "production".
    assert client.get(
        "/api/notebooks/tags_apply_a.ipynb/tags"
    ).json()["tags"] == ["bug", "production"]


def test_apply_tag_reports_a_missing_filename_without_aborting_the_rest():

    _upload_sample_notebook("tags_apply_partial.ipynb")

    resp = client.post(
        "/api/tags/urgent/apply",
        json={"filenames": ["tags_apply_partial.ipynb", "does_not_exist.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["tags_apply_partial.ipynb"]["status"] == "success"
    assert results_by_filename["does_not_exist.ipynb"]["status"] == "error"
    assert "not found" in results_by_filename["does_not_exist.ipynb"]["detail"]

    assert client.get(
        "/api/notebooks/tags_apply_partial.ipynb/tags"
    ).json()["tags"] == ["urgent"]


def test_apply_tag_is_idempotent_for_a_notebook_that_already_has_it():

    _upload_sample_notebook("tags_apply_idempotent.ipynb")
    client.put(
        "/api/notebooks/tags_apply_idempotent.ipynb/tags",
        json={"tags": ["production"]},
    )

    resp = client.post(
        "/api/tags/production/apply",
        json={"filenames": ["tags_apply_idempotent.ipynb"]},
    )

    assert resp.status_code == 200
    assert resp.json()["results"][0]["tags"] == ["production"]


def test_apply_tag_rejects_a_non_list_filenames_value():

    resp = client.post("/api/tags/production/apply", json={"filenames": "not-a-list"})

    assert resp.status_code == 400


def test_apply_tag_rejects_an_empty_filenames_list():

    resp = client.post("/api/tags/production/apply", json={"filenames": []})

    assert resp.status_code == 400


def test_apply_tag_rejects_an_empty_tag():

    _upload_sample_notebook("tags_apply_empty_tag.ipynb")

    resp = client.post(
        "/api/tags/%20/apply",
        json={"filenames": ["tags_apply_empty_tag.ipynb"]},
    )

    assert resp.status_code == 400


def test_remove_tag_batch_removes_it_from_named_notebooks_only():

    _upload_sample_notebook("tags_remove_a.ipynb")
    _upload_sample_notebook("tags_remove_b.ipynb")
    _upload_sample_notebook("tags_remove_untouched.ipynb")

    client.put(
        "/api/notebooks/tags_remove_a.ipynb/tags",
        json={"tags": ["production", "bug"]},
    )
    client.put(
        "/api/notebooks/tags_remove_b.ipynb/tags",
        json={"tags": ["production"]},
    )
    client.put(
        "/api/notebooks/tags_remove_untouched.ipynb/tags",
        json={"tags": ["production"]},
    )

    resp = client.post(
        "/api/tags/production/remove",
        json={"filenames": ["tags_remove_a.ipynb", "tags_remove_b.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["tag"] == "production"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["tags_remove_a.ipynb"]["status"] == "success"
    assert results_by_filename["tags_remove_a.ipynb"]["tags"] == ["bug"]
    assert results_by_filename["tags_remove_b.ipynb"]["tags"] == []

    # A notebook not named in "filenames" keeps the tag untouched, even
    # though it also carries it.
    assert client.get(
        "/api/notebooks/tags_remove_untouched.ipynb/tags"
    ).json()["tags"] == ["production"]


def test_remove_tag_batch_reports_a_missing_filename_without_aborting_the_rest():

    _upload_sample_notebook("tags_remove_partial.ipynb")
    client.put(
        "/api/notebooks/tags_remove_partial.ipynb/tags",
        json={"tags": ["urgent"]},
    )

    resp = client.post(
        "/api/tags/urgent/remove",
        json={"filenames": ["tags_remove_partial.ipynb", "does_not_exist.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["tags_remove_partial.ipynb"]["status"] == "success"
    assert results_by_filename["does_not_exist.ipynb"]["status"] == "error"
    assert "not found" in results_by_filename["does_not_exist.ipynb"]["detail"]

    assert client.get(
        "/api/notebooks/tags_remove_partial.ipynb/tags"
    ).json()["tags"] == []


def test_remove_tag_batch_is_idempotent_for_a_notebook_that_never_had_it():

    _upload_sample_notebook("tags_remove_idempotent.ipynb")

    resp = client.post(
        "/api/tags/production/remove",
        json={"filenames": ["tags_remove_idempotent.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["results"][0]["status"] == "success"
    assert body["results"][0]["tags"] == []


def test_remove_tag_batch_rejects_a_non_list_filenames_value():

    resp = client.post("/api/tags/production/remove", json={"filenames": "not-a-list"})

    assert resp.status_code == 400


def test_remove_tag_batch_rejects_an_empty_filenames_list():

    resp = client.post("/api/tags/production/remove", json={"filenames": []})

    assert resp.status_code == 400


def test_remove_tag_batch_rejects_an_empty_tag():

    _upload_sample_notebook("tags_remove_empty_tag.ipynb")

    resp = client.post(
        "/api/tags/%20/remove",
        json={"filenames": ["tags_remove_empty_tag.ipynb"]},
    )

    assert resp.status_code == 400


def test_rename_tag_renames_it_on_every_notebook_that_has_it():

    _upload_sample_notebook("tags_rename_a.ipynb")
    _upload_sample_notebook("tags_rename_b.ipynb")
    _upload_sample_notebook("tags_rename_c.ipynb")

    client.put(
        "/api/notebooks/tags_rename_a.ipynb/tags",
        json={"tags": ["prod", "bug"]},
    )
    client.put(
        "/api/notebooks/tags_rename_b.ipynb/tags",
        json={"tags": ["prod"]},
    )
    client.put(
        "/api/notebooks/tags_rename_c.ipynb/tags",
        json={"tags": ["staging"]},
    )

    rename_resp = client.patch(
        "/api/tags/prod", json={"new_tag": "production"}
    )

    assert rename_resp.status_code == 200
    body = rename_resp.json()
    assert body["status"] == "success"
    assert body["tag"] == "prod"
    assert body["new_tag"] == "production"
    assert sorted(body["affected_notebooks"]) == [
        "tags_rename_a.ipynb", "tags_rename_b.ipynb",
    ]
    assert body["notebook_count"] == 2

    assert client.get(
        "/api/notebooks/tags_rename_a.ipynb/tags"
    ).json()["tags"] == ["bug", "production"]
    assert client.get(
        "/api/notebooks/tags_rename_b.ipynb/tags"
    ).json()["tags"] == ["production"]
    # Untouched -- never carried "prod" at all.
    assert client.get(
        "/api/notebooks/tags_rename_c.ipynb/tags"
    ).json()["tags"] == ["staging"]

    tag_names = {entry["tag"] for entry in client.get("/api/tags").json()["tags"]}
    assert "prod" not in tag_names
    assert "production" in tag_names


def test_rename_tag_is_a_no_op_success_when_nothing_carries_it():

    resp = client.patch(
        "/api/tags/this-tag-does-not-exist-anywhere",
        json={"new_tag": "something-else"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "tag": "this-tag-does-not-exist-anywhere",
        "new_tag": "something-else",
        "affected_notebooks": [],
        "notebook_count": 0,
    }


def test_rename_tag_merges_into_an_existing_new_tag_without_duplicating():

    _upload_sample_notebook("tags_rename_merge.ipynb")
    client.put(
        "/api/notebooks/tags_rename_merge.ipynb/tags",
        json={"tags": ["prod", "production"]},
    )

    resp = client.patch("/api/tags/prod", json={"new_tag": "production"})

    assert resp.status_code == 200
    assert resp.json()["affected_notebooks"] == ["tags_rename_merge.ipynb"]

    assert client.get(
        "/api/notebooks/tags_rename_merge.ipynb/tags"
    ).json()["tags"] == ["production"]


def test_rename_tag_rejects_a_missing_new_tag():

    resp = client.patch("/api/tags/prod", json={})

    assert resp.status_code == 400


def test_rename_tag_rejects_an_empty_new_tag():

    resp = client.patch("/api/tags/prod", json={"new_tag": "   "})

    assert resp.status_code == 400


def test_rename_tag_rejects_renaming_a_tag_to_itself():

    resp = client.patch("/api/tags/prod", json={"new_tag": "prod"})

    assert resp.status_code == 400


def test_search_functions_finds_notebooks_with_a_matching_function_name():

    content_a = _notebook_bytes(
        "def train_model(epochs: int) -> str:\n    return 'done'\n"
    )
    content_b = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    client.post(
        "/api/upload",
        files={"file": ("search_functions_a.ipynb", io.BytesIO(content_a), "application/json")},
    )
    client.post(
        "/api/upload",
        files={"file": ("search_functions_b.ipynb", io.BytesIO(content_b), "application/json")},
    )

    resp = client.get("/api/functions?search=train")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["search"] == "train"
    assert body["notebook_count"] == 1
    assert body["matches"][0]["filename"] == "search_functions_a.ipynb"
    assert [f["name"] for f in body["matches"][0]["functions"]] == ["train_model"]


def test_search_functions_is_case_insensitive():

    content = _notebook_bytes(
        "def TrainModel(epochs: int) -> str:\n    return 'done'\n"
    )

    client.post(
        "/api/upload",
        files={"file": ("search_functions_case.ipynb", io.BytesIO(content), "application/json")},
    )

    resp = client.get("/api/functions?search=trainmodel")

    assert resp.status_code == 200
    assert [m["filename"] for m in resp.json()["matches"]] == ["search_functions_case.ipynb"]


def test_search_functions_reports_no_matches():

    resp = client.get("/api/functions?search=this_function_name_does_not_exist_anywhere")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "search": "this_function_name_does_not_exist_anywhere",
        "matches": [],
        "notebook_count": 0,
    }


def test_search_functions_requires_a_search_value():

    resp = client.get("/api/functions")

    assert resp.status_code == 400


def test_search_functions_skips_a_malformed_notebook_file():

    filename = "search_functions_malformed.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    content = _notebook_bytes(
        "def search_functions_clean_marker_fn(a: int, b: int) -> int:\n    return a + b\n"
    )
    client.post(
        "/api/upload",
        files={"file": ("search_functions_clean.ipynb", io.BytesIO(content), "application/json")},
    )

    resp = client.get("/api/functions?search=search_functions_clean_marker_fn")

    assert resp.status_code == 200
    assert [m["filename"] for m in resp.json()["matches"]] == ["search_functions_clean.ipynb"]

    os.remove(file_path)


def test_list_notebooks_reports_tags_for_each_entry():

    _upload_sample_notebook("tags_in_list.ipynb")

    client.put("/api/notebooks/tags_in_list.ipynb/tags", json={"tags": ["demo"]})

    notebooks = {
        nb["filename"]: nb for nb in client.get("/api/notebooks").json()["notebooks"]
    }

    assert notebooks["tags_in_list.ipynb"]["tags"] == ["demo"]


def test_list_notebooks_filters_by_tag():

    _upload_sample_notebook("tags_filter_a.ipynb")
    _upload_sample_notebook("tags_filter_b.ipynb")

    client.put("/api/notebooks/tags_filter_a.ipynb/tags", json={"tags": ["keepme"]})

    notebooks = client.get(
        "/api/notebooks?search=tags_filter_&tag=keepme"
    ).json()["notebooks"]

    assert [nb["filename"] for nb in notebooks] == ["tags_filter_a.ipynb"]


def test_delete_notebook_removes_its_tags_sidecar_file():

    _upload_sample_notebook("tags_delete_single.ipynb")
    client.put("/api/notebooks/tags_delete_single.ipynb/tags", json={"tags": ["bug"]})
    assert _tags_sidecar_path("tags_delete_single.ipynb").is_file()

    delete_resp = client.delete("/api/notebooks/tags_delete_single.ipynb")
    assert delete_resp.status_code == 200

    assert not _tags_sidecar_path("tags_delete_single.ipynb").is_file()

    # A notebook re-uploaded under the same name afterward must not
    # silently inherit the deleted notebook's old tags.
    _upload_sample_notebook("tags_delete_single.ipynb")
    assert client.get(
        "/api/notebooks/tags_delete_single.ipynb/tags"
    ).json()["tags"] == []


def test_delete_all_notebooks_removes_tags_sidecar_files():

    _upload_sample_notebook("tags_delete_all.ipynb")
    client.put("/api/notebooks/tags_delete_all.ipynb/tags", json={"tags": ["bug"]})
    assert _tags_sidecar_path("tags_delete_all.ipynb").is_file()

    resp = client.delete("/api/notebooks?confirm=true")
    assert resp.status_code == 200

    assert not _tags_sidecar_path("tags_delete_all.ipynb").is_file()


def test_rename_notebook_moves_its_tags_to_the_new_name():

    _upload_sample_notebook("tags_rename_source.ipynb")
    client.put(
        "/api/notebooks/tags_rename_source.ipynb/tags", json={"tags": ["bug"]}
    )

    rename_resp = client.patch(
        "/api/notebooks/tags_rename_source.ipynb",
        json={"new_filename": "tags_rename_target.ipynb"},
    )
    assert rename_resp.status_code == 200

    assert not _tags_sidecar_path("tags_rename_source.ipynb").is_file()
    assert client.get(
        "/api/notebooks/tags_rename_target.ipynb/tags"
    ).json()["tags"] == ["bug"]

    os.remove(Path(UPLOAD_DIR) / "tags_rename_target.ipynb")
    _tags_sidecar_path("tags_rename_target.ipynb").unlink(missing_ok=True)


def test_rename_notebook_overwrite_discards_the_destinations_previous_tags():

    _upload_sample_notebook("tags_rename_overwrite_source.ipynb")
    _upload_sample_notebook("tags_rename_overwrite_target.ipynb")
    client.put(
        "/api/notebooks/tags_rename_overwrite_target.ipynb/tags",
        json={"tags": ["stale"]},
    )

    rename_resp = client.patch(
        "/api/notebooks/tags_rename_overwrite_source.ipynb",
        json={
            "new_filename": "tags_rename_overwrite_target.ipynb",
            "overwrite": True,
        },
    )
    assert rename_resp.status_code == 200

    assert client.get(
        "/api/notebooks/tags_rename_overwrite_target.ipynb/tags"
    ).json()["tags"] == []

    os.remove(Path(UPLOAD_DIR) / "tags_rename_overwrite_target.ipynb")
    _tags_sidecar_path("tags_rename_overwrite_target.ipynb").unlink(missing_ok=True)


def test_list_notebook_versions_is_empty_for_a_notebook_never_overwritten():

    _upload_sample_notebook("versions_never_overwritten.ipynb")

    resp = client.get("/api/notebooks/versions_never_overwritten.ipynb/versions")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "filename": "versions_never_overwritten.ipynb",
        "versions": [],
    }


def test_list_notebook_versions_returns_404_for_missing_notebook():

    resp = client.get("/api/notebooks/versions_does_not_exist.ipynb/versions")

    assert resp.status_code == 404


def test_overwriting_a_notebook_snapshots_the_previous_content():

    filename = "versions_overwrite_snapshots.ipynb"
    original_content = _notebook_bytes("def f() -> int:\n    return 1\n")

    resp = client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(original_content), "application/json")},
    )
    assert resp.status_code == 200

    resp = client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200
    assert resp.json()["overwritten"] is True

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]

    assert len(versions) == 1
    assert versions[0]["size_bytes"] == len(original_content)
    assert "saved_at" in versions[0]

    downloaded = client.get(
        f"/api/notebooks/{filename}/versions/{versions[0]['version_id']}"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == original_content


def test_uploading_a_brand_new_notebook_does_not_snapshot_anything():

    filename = "versions_brand_new.ipynb"
    _upload_sample_notebook(filename)

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]

    assert versions == []


def test_get_notebook_version_returns_404_for_an_unknown_version_id():

    _upload_sample_notebook("versions_unknown_id.ipynb")

    resp = client.get(
        "/api/notebooks/versions_unknown_id.ipynb/versions/not_a_real_version.ipynb"
    )

    assert resp.status_code == 404


def test_get_notebook_version_rejects_an_absolute_version_id():
    """Same protection resolve_upload_path/resolve_generated_path already
    apply to their own respective root directories (see
    test_get_notebook_rejects_absolute_filename), applied here to
    version_id's own root -- this notebook's version directory.
    """

    _upload_sample_notebook("versions_traversal.ipynb")

    resp = client.get("/api/notebooks/versions_traversal.ipynb/versions/%2Fetc%2Fpasswd")

    assert resp.status_code in (400, 404)
    assert "root:" not in resp.text


def test_export_notebook_versions_bundles_current_content_and_every_version():

    filename = "versions_export_bundle.ipynb"
    original_content = _notebook_bytes("def f() -> int:\n    return 1\n")
    middle_content = _notebook_bytes("def g() -> int:\n    return 2\n")
    current_content = _notebook_bytes("def h() -> int:\n    return 3\n")

    client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(original_content), "application/json")},
    )
    client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(middle_content), "application/json")},
    )
    client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(current_content), "application/json")},
    )

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert len(versions) == 2
    version_ids = {v["version_id"] for v in versions}

    export_resp = client.get(f"/api/notebooks/{filename}/versions/export")

    assert export_resp.status_code == 200
    assert export_resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(export_resp.content)) as archive:

        names = set(archive.namelist())
        assert filename in names
        assert names - {filename} == {f"versions/{vid}" for vid in version_ids}

        assert archive.read(filename) == current_content

        version_contents = {archive.read(f"versions/{vid}") for vid in version_ids}
        assert version_contents == {original_content, middle_content}


def test_export_notebook_versions_succeeds_with_no_version_history():

    _upload_sample_notebook("versions_export_no_history.ipynb")

    export_resp = client.get(
        "/api/notebooks/versions_export_no_history.ipynb/versions/export"
    )

    assert export_resp.status_code == 200

    with zipfile.ZipFile(io.BytesIO(export_resp.content)) as archive:
        assert archive.namelist() == ["versions_export_no_history.ipynb"]


def test_export_notebook_versions_returns_404_for_missing_notebook():

    resp = client.get(
        "/api/notebooks/versions_export_does_not_exist.ipynb/versions/export"
    )

    assert resp.status_code == 404


def test_inspect_notebook_version_reports_functions_and_dependencies_for_that_snapshot():

    filename = "versions_inspect_snapshot.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes(
                    "import pandas\n\n"
                    "def add(a: int, b: int) -> int:\n    return a + b\n"
                )),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.get(f"/api/notebooks/{filename}/versions/{version_id}/inspect")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["filename"] == filename
    assert body["version_id"] == version_id
    assert [f["name"] for f in body["functions"]] == ["add"]
    assert any(dep.startswith("pandas") for dep in body["dependencies"])
    assert body["endpoints"] == [
        {"path": "/add", "method": "POST", "is_async": False}
    ]
    assert body["reserved_name_conflicts"] == []
    assert body["skipped_functions"] == []


def test_inspect_notebook_version_reflects_the_old_snapshot_not_current_content():

    filename = "versions_inspect_not_current.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def old_fn() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def new_fn() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.get(f"/api/notebooks/{filename}/versions/{version_id}/inspect")

    assert resp.status_code == 200
    function_names = [f["name"] for f in resp.json()["functions"]]
    assert function_names == ["old_fn"]
    assert "new_fn" not in function_names


def test_inspect_notebook_version_reports_reserved_name_conflicts():

    filename = "versions_inspect_reserved_name.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def health_check() -> dict:\n    return {}\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def fine() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.get(f"/api/notebooks/{filename}/versions/{version_id}/inspect")

    assert resp.status_code == 200
    assert resp.json()["reserved_name_conflicts"] == ["health_check"]


def test_inspect_notebook_version_returns_404_for_missing_notebook():

    resp = client.get(
        "/api/notebooks/versions_inspect_missing_notebook.ipynb/versions/x.ipynb/inspect"
    )

    assert resp.status_code == 404


def test_inspect_notebook_version_returns_404_for_an_unknown_version_id():

    _upload_sample_notebook("versions_inspect_unknown_id.ipynb")

    resp = client.get(
        "/api/notebooks/versions_inspect_unknown_id.ipynb/versions/not_real.ipynb/inspect"
    )

    assert resp.status_code == 404


def test_diff_notebook_version_against_current_live_content():

    filename = "versions_diff_against_live.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes(
                    "def add(a: int, b: int) -> int:\n    return a + b\n\n"
                    "def remove_me() -> int:\n    return 0\n\n"
                    "def unchanged_fn() -> int:\n    return 1\n"
                )),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes(
                    "def add(a: int, b: int, c: int) -> int:\n    return a + b + c\n\n"
                    "def add_me() -> int:\n    return 2\n\n"
                    "def unchanged_fn() -> int:\n    return 1\n"
                )),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.get(f"/api/notebooks/{filename}/versions/{version_id}/diff")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["filename"] == filename
    assert body["version_id"] == version_id
    assert body["against"] is None
    assert [f["name"] for f in body["added"]] == ["add_me"]
    assert [f["name"] for f in body["removed"]] == ["remove_me"]
    assert [c["name"] for c in body["changed"]] == ["add"]
    assert body["unchanged"] == ["unchanged_fn"]


def test_diff_notebook_version_against_another_version():

    filename = "versions_diff_against_version.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes(
                    "def f() -> int:\n    return 1\n\ndef g() -> int:\n    return 2\n"
                )),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )

    # Newest first (see list_notebook_versions), so index 0 is the
    # second-uploaded content (with `g`) and index 1 is the very first.
    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    newer_version_id = versions[0]["version_id"]
    older_version_id = versions[1]["version_id"]

    resp = client.get(
        f"/api/notebooks/{filename}/versions/{older_version_id}/diff",
        params={"against": newer_version_id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["against"] == newer_version_id
    assert [f["name"] for f in body["added"]] == ["g"]
    assert body["removed"] == []
    assert body["unchanged"] == ["f"]


def test_diff_notebook_version_returns_404_for_missing_notebook():

    resp = client.get(
        "/api/notebooks/versions_diff_missing_notebook.ipynb/versions/x.ipynb/diff"
    )

    assert resp.status_code == 404


def test_diff_notebook_version_returns_404_for_an_unknown_version_id():

    _upload_sample_notebook("versions_diff_unknown_id.ipynb")

    resp = client.get(
        "/api/notebooks/versions_diff_unknown_id.ipynb/versions/not_real.ipynb/diff"
    )

    assert resp.status_code == 404


def test_diff_notebook_version_returns_404_for_an_unknown_against_version_id():

    filename = "versions_diff_unknown_against.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.get(
        f"/api/notebooks/{filename}/versions/{version_id}/diff",
        params={"against": "not_real.ipynb"},
    )

    assert resp.status_code == 404


def test_copy_notebook_version_duplicates_a_past_version_into_a_new_notebook():

    filename = "versions_copy_source.ipynb"
    original_content = _notebook_bytes("def f() -> int:\n    return 1\n")

    client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(original_content), "application/json")},
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    copy_resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": "versions_copy_target.ipynb"},
    )

    assert copy_resp.status_code == 200
    assert copy_resp.json() == {
        "status": "success",
        "filename": filename,
        "version_id": version_id,
        "new_filename": "versions_copy_target.ipynb",
    }

    assert (
        Path(UPLOAD_DIR) / "versions_copy_target.ipynb"
    ).read_bytes() == original_content

    # The source notebook's own current content is untouched -- still the
    # *second* upload's content, not the version that was just copied.
    assert (Path(UPLOAD_DIR) / filename).read_bytes() != original_content

    os.remove(Path(UPLOAD_DIR) / "versions_copy_target.ipynb")


def test_copy_notebook_version_does_not_inherit_tags():

    filename = "versions_copy_tags_source.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    client.put(f"/api/notebooks/{filename}/tags", json={"tags": ["production"]})

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    copy_resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": "versions_copy_tags_target.ipynb"},
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/versions_copy_tags_target.ipynb/tags"
    ).json()["tags"] == []

    os.remove(Path(UPLOAD_DIR) / "versions_copy_tags_target.ipynb")


def test_copy_notebook_version_does_not_copy_source_version_history():

    filename = "versions_copy_history_source.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    copy_resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": "versions_copy_history_target.ipynb"},
    )
    assert copy_resp.status_code == 200

    assert client.get(
        "/api/notebooks/versions_copy_history_target.ipynb/versions"
    ).json()["versions"] == []

    os.remove(Path(UPLOAD_DIR) / "versions_copy_history_target.ipynb")


def test_copy_notebook_version_returns_404_for_missing_notebook():

    resp = client.post(
        "/api/notebooks/versions_copy_missing_notebook.ipynb/versions/x.ipynb/copy",
        json={"new_filename": "whatever.ipynb"},
    )

    assert resp.status_code == 404


def test_copy_notebook_version_returns_404_for_an_unknown_version_id():

    _upload_sample_notebook("versions_copy_unknown_id.ipynb")

    resp = client.post(
        "/api/notebooks/versions_copy_unknown_id.ipynb/versions/not_real.ipynb/copy",
        json={"new_filename": "whatever.ipynb"},
    )

    assert resp.status_code == 404


def test_copy_notebook_version_requires_new_filename():

    filename = "versions_copy_missing_target.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={},
    )

    assert resp.status_code == 400


def test_copy_notebook_version_rejects_a_non_ipynb_target_name():

    filename = "versions_copy_bad_ext.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": "versions_copy_bad_ext.txt"},
    )

    assert resp.status_code == 400


def test_copy_notebook_version_rejects_copying_onto_its_own_source_filename():

    filename = "versions_copy_onto_self.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": filename},
    )

    assert resp.status_code == 400
    assert "restore" in resp.json()["detail"]


def test_copy_notebook_version_rejects_collision_without_overwrite():

    filename = "versions_copy_collision_source.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    _upload_sample_notebook("versions_copy_collision_target.ipynb")

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": "versions_copy_collision_target.ipynb"},
    )

    assert resp.status_code == 409
    os.remove(Path(UPLOAD_DIR) / "versions_copy_collision_target.ipynb")


def test_copy_notebook_version_overwrite_discards_the_destinations_previous_tags_and_history():

    filename = "versions_copy_overwrite_source.ipynb"
    original_content = _notebook_bytes("def f() -> int:\n    return 1\n")

    client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(original_content), "application/json")},
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    target = "versions_copy_overwrite_target.ipynb"
    _upload_sample_notebook(target)
    client.put(f"/api/notebooks/{target}/tags", json={"tags": ["stale"]})
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                target,
                io.BytesIO(_notebook_bytes("def stale_history() -> int:\n    return 0\n")),
                "application/json",
            )
        },
    )
    assert client.get(f"/api/notebooks/{target}/versions").json()["versions"]

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/copy",
        json={"new_filename": target, "overwrite": True},
    )

    assert resp.status_code == 200
    assert (Path(UPLOAD_DIR) / target).read_bytes() == original_content
    assert client.get(f"/api/notebooks/{target}/tags").json()["tags"] == []
    assert client.get(f"/api/notebooks/{target}/versions").json()["versions"] == []

    os.remove(Path(UPLOAD_DIR) / target)


def test_restore_notebook_version_makes_it_the_current_content_again():

    filename = "versions_restore.ipynb"
    original_content = _notebook_bytes("def f() -> int:\n    return 1\n")

    client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(original_content), "application/json")},
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    version_id = versions[0]["version_id"]

    restore_resp = client.post(
        f"/api/notebooks/{filename}/versions/{version_id}/restore"
    )

    assert restore_resp.status_code == 200
    assert restore_resp.json() == {
        "status": "success",
        "filename": filename,
        "restored_version_id": version_id,
    }

    assert (Path(UPLOAD_DIR) / filename).read_bytes() == original_content


def test_restore_notebook_version_itself_snapshots_the_content_it_replaces():
    """Restoring must be undoable too -- otherwise picking the wrong
    version_id would be exactly as destructive as the plain overwrite this
    whole feature exists to make recoverable.
    """

    filename = "versions_restore_is_undoable.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    second_content = _notebook_bytes("def g() -> int:\n    return 2\n")
    client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(second_content), "application/json")},
    )

    first_version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    client.post(f"/api/notebooks/{filename}/versions/{first_version_id}/restore")

    versions_after_restore = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"]

    assert len(versions_after_restore) == 2

    saved_second_content = next(
        v for v in versions_after_restore if v["version_id"] != first_version_id
    )
    downloaded = client.get(
        f"/api/notebooks/{filename}/versions/{saved_second_content['version_id']}"
    )
    assert downloaded.content == second_content


def test_restore_notebook_version_returns_404_for_missing_notebook():

    resp = client.post(
        "/api/notebooks/versions_missing_notebook.ipynb/versions/whatever.ipynb/restore"
    )

    assert resp.status_code == 404


def test_restore_notebook_version_returns_404_for_an_unknown_version_id():

    _upload_sample_notebook("versions_restore_unknown_id.ipynb")

    resp = client.post(
        "/api/notebooks/versions_restore_unknown_id.ipynb/versions/nope.ipynb/restore"
    )

    assert resp.status_code == 404


def test_delete_notebook_version_removes_only_that_snapshot():

    filename = "versions_delete.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def h() -> int:\n    return 3\n")),
                "application/json",
            )
        },
    )

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert len(versions) == 2
    version_id_to_delete = versions[0]["version_id"]
    version_id_to_keep = versions[1]["version_id"]

    delete_resp = client.delete(
        f"/api/notebooks/{filename}/versions/{version_id_to_delete}"
    )

    assert delete_resp.status_code == 200
    assert delete_resp.json() == {
        "status": "success",
        "filename": filename,
        "deleted_version_id": version_id_to_delete,
    }

    remaining = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert [v["version_id"] for v in remaining] == [version_id_to_keep]

    assert client.get(
        f"/api/notebooks/{filename}/versions/{version_id_to_delete}"
    ).status_code == 404


def test_delete_notebook_versions_batch_removes_only_the_named_versions():

    filename = "versions_delete_batch.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    for i in range(3):
        client.post(
            "/api/upload?overwrite=true",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes(f"def g{i}() -> int:\n    return {i}\n")),
                    "application/json",
                )
            },
        )

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert len(versions) == 3
    to_delete = [versions[0]["version_id"], versions[1]["version_id"]]
    to_keep = versions[2]["version_id"]

    resp = client.post(
        f"/api/notebooks/{filename}/versions/delete-batch",
        json={"version_ids": to_delete},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["filename"] == filename
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0

    results_by_id = {r["version_id"]: r for r in body["results"]}
    assert results_by_id[to_delete[0]]["status"] == "success"
    assert results_by_id[to_delete[1]]["status"] == "success"

    remaining = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert [v["version_id"] for v in remaining] == [to_keep]


def test_delete_notebook_versions_batch_reports_a_missing_version_id_without_aborting_the_rest():

    filename = "versions_delete_batch_partial.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]

    resp = client.post(
        f"/api/notebooks/{filename}/versions/delete-batch",
        json={"version_ids": [version_id, "does_not_exist.ipynb"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded_count"] == 1
    assert body["failed_count"] == 1

    results_by_id = {r["version_id"]: r for r in body["results"]}
    assert results_by_id[version_id]["status"] == "success"
    assert results_by_id["does_not_exist.ipynb"]["status"] == "error"
    assert "not found" in results_by_id["does_not_exist.ipynb"]["detail"]

    assert client.get(f"/api/notebooks/{filename}/versions").json()["versions"] == []


def test_delete_notebook_versions_batch_returns_404_for_missing_notebook():

    resp = client.post(
        "/api/notebooks/versions_delete_batch_missing_notebook.ipynb/versions/delete-batch",
        json={"version_ids": ["whatever.ipynb"]},
    )

    assert resp.status_code == 404


def test_delete_notebook_versions_batch_rejects_a_non_list_version_ids_value():

    _upload_sample_notebook("versions_delete_batch_bad_value.ipynb")

    resp = client.post(
        "/api/notebooks/versions_delete_batch_bad_value.ipynb/versions/delete-batch",
        json={"version_ids": "not-a-list"},
    )

    assert resp.status_code == 400


def test_delete_notebook_versions_batch_rejects_an_empty_version_ids_list():

    _upload_sample_notebook("versions_delete_batch_empty.ipynb")

    resp = client.post(
        "/api/notebooks/versions_delete_batch_empty.ipynb/versions/delete-batch",
        json={"version_ids": []},
    )

    assert resp.status_code == 400


def test_delete_notebook_version_returns_404_for_missing_notebook():

    resp = client.delete(
        "/api/notebooks/versions_delete_missing_notebook.ipynb/versions/whatever.ipynb"
    )

    assert resp.status_code == 404


def test_delete_notebook_version_returns_404_for_an_unknown_version_id():

    _upload_sample_notebook("versions_delete_unknown_id.ipynb")

    resp = client.delete(
        "/api/notebooks/versions_delete_unknown_id.ipynb/versions/nope.ipynb"
    )

    assert resp.status_code == 404


def test_delete_notebook_version_rejects_an_absolute_version_id():

    _upload_sample_notebook("versions_delete_traversal.ipynb")

    resp = client.delete(
        "/api/notebooks/versions_delete_traversal.ipynb/versions/%2Fetc%2Fpasswd"
    )

    assert resp.status_code in (400, 404)
    assert "root:" not in resp.text


def test_clear_notebook_versions_removes_every_snapshot():

    filename = "versions_clear.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    current_content = _notebook_bytes("def h() -> int:\n    return 3\n")
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(current_content),
                "application/json",
            )
        },
    )

    versions_before = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert len(versions_before) == 2

    clear_resp = client.delete(f"/api/notebooks/{filename}/versions")

    assert clear_resp.status_code == 200
    body = clear_resp.json()
    assert body["status"] == "success"
    assert body["filename"] == filename
    assert body["deleted_count"] == 2
    assert sorted(body["deleted_version_ids"]) == sorted(
        v["version_id"] for v in versions_before
    )

    assert client.get(f"/api/notebooks/{filename}/versions").json()["versions"] == []

    # The notebook's own current content is completely untouched.
    get_resp = client.get(f"/api/notebooks/{filename}")
    assert get_resp.status_code == 200
    assert get_resp.content == current_content


def test_clear_notebook_versions_is_a_no_op_success_for_a_notebook_with_no_history():

    _upload_sample_notebook("versions_clear_none.ipynb")

    resp = client.delete("/api/notebooks/versions_clear_none.ipynb/versions")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "filename": "versions_clear_none.ipynb",
        "deleted_version_ids": [],
        "deleted_count": 0,
    }


def test_clear_notebook_versions_returns_404_for_missing_notebook():

    resp = client.delete("/api/notebooks/versions_clear_missing.ipynb/versions")

    assert resp.status_code == 404


def test_clear_notebook_versions_does_not_affect_a_different_notebooks_history():

    filename_a = "versions_clear_a.ipynb"
    filename_b = "versions_clear_b.ipynb"

    for filename in (filename_a, filename_b):
        client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                )
            },
        )
        client.post(
            "/api/upload?overwrite=true",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                    "application/json",
                )
            },
        )

    clear_resp = client.delete(f"/api/notebooks/{filename_a}/versions")
    assert clear_resp.status_code == 200
    assert clear_resp.json()["deleted_count"] == 1

    assert client.get(f"/api/notebooks/{filename_a}/versions").json()["versions"] == []
    assert len(client.get(f"/api/notebooks/{filename_b}/versions").json()["versions"]) == 1


def test_notebook_versions_are_pruned_beyond_the_configured_maximum():

    filename = "versions_pruned.ipynb"
    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f0() -> int:\n    return 0\n")),
                "application/json",
            )
        },
    )

    for i in range(1, MAX_NOTEBOOK_VERSIONS + 3):
        client.post(
            "/api/upload?overwrite=true",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes(f"def f{i}() -> int:\n    return {i}\n")),
                    "application/json",
                )
            },
        )

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]

    assert len(versions) == MAX_NOTEBOOK_VERSIONS


def _backdate_notebook_version(filename, version_id, days_ago):
    version_path = Path(UPLOAD_DIR) / ".versions" / filename / version_id
    old_time = version_path.stat().st_mtime - (days_ago * 86400)
    os.utime(version_path, (old_time, old_time))


def test_prune_all_notebook_versions_deletes_only_versions_older_than_cutoff():

    filename = "prune_versions_a.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def h() -> int:\n    return 3\n")),
                "application/json",
            )
        },
    )

    versions = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert len(versions) == 2

    old_version_id = versions[1]["version_id"]
    recent_version_id = versions[0]["version_id"]
    _backdate_notebook_version(filename, old_version_id, days_ago=40)

    resp = client.delete("/api/notebooks/versions", params={"older_than_days": 30})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["older_than_days"] == 30
    assert body["notebook_count_affected"] == 1
    assert body["total_deleted_count"] == 1
    assert body["results"] == [{
        "filename": filename,
        "deleted_version_ids": [old_version_id],
        "deleted_count": 1,
    }]

    remaining = client.get(f"/api/notebooks/{filename}/versions").json()["versions"]
    assert [v["version_id"] for v in remaining] == [recent_version_id]


def test_prune_all_notebook_versions_spans_multiple_notebooks():

    for filename in ("prune_versions_multi_a.ipynb", "prune_versions_multi_b.ipynb"):

        client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                )
            },
        )
        client.post(
            "/api/upload?overwrite=true",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                    "application/json",
                )
            },
        )
        version_id = client.get(
            f"/api/notebooks/{filename}/versions"
        ).json()["versions"][0]["version_id"]
        _backdate_notebook_version(filename, version_id, days_ago=40)

    resp = client.delete("/api/notebooks/versions", params={"older_than_days": 30})

    assert resp.status_code == 200
    body = resp.json()
    assert body["notebook_count_affected"] == 2
    assert body["total_deleted_count"] == 2

    for filename in ("prune_versions_multi_a.ipynb", "prune_versions_multi_b.ipynb"):
        assert client.get(f"/api/notebooks/{filename}/versions").json()["versions"] == []


def test_prune_all_notebook_versions_is_a_no_op_when_nothing_is_old_enough():

    filename = "prune_versions_recent.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    resp = client.delete("/api/notebooks/versions", params={"older_than_days": 30})

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["notebook_count_affected"] == 0
    assert body["total_deleted_count"] == 0

    assert len(client.get(f"/api/notebooks/{filename}/versions").json()["versions"]) == 1


def test_prune_all_notebook_versions_leaves_current_content_and_tags_untouched():

    filename = "prune_versions_untouched.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.put(f"/api/notebooks/{filename}/tags", json={"tags": ["production"]})
    current_content = _notebook_bytes("def g() -> int:\n    return 2\n")
    client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(current_content), "application/json")},
    )

    version_id = client.get(
        f"/api/notebooks/{filename}/versions"
    ).json()["versions"][0]["version_id"]
    _backdate_notebook_version(filename, version_id, days_ago=40)

    resp = client.delete("/api/notebooks/versions", params={"older_than_days": 30})
    assert resp.status_code == 200
    assert resp.json()["total_deleted_count"] == 1

    assert client.get(f"/api/notebooks/{filename}").content == current_content
    assert client.get(f"/api/notebooks/{filename}/tags").json()["tags"] == ["production"]


def test_prune_all_notebook_versions_requires_a_positive_older_than_days():

    resp = client.delete("/api/notebooks/versions")
    assert resp.status_code == 400

    resp = client.delete("/api/notebooks/versions", params={"older_than_days": 0})
    assert resp.status_code == 400

    resp = client.delete("/api/notebooks/versions", params={"older_than_days": -5})
    assert resp.status_code == 400


def test_delete_notebook_removes_its_version_history():

    filename = "versions_delete_single.ipynb"
    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    assert _notebook_versions_dir(filename).is_dir()

    delete_resp = client.delete(f"/api/notebooks/{filename}")
    assert delete_resp.status_code == 200

    assert not _notebook_versions_dir(filename).is_dir()


def test_delete_all_notebooks_removes_version_history():

    filename = "versions_delete_all.ipynb"
    client.post(
        "/api/upload",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                filename,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    assert _notebook_versions_dir(filename).is_dir()

    resp = client.delete("/api/notebooks?confirm=true")
    assert resp.status_code == 200

    assert not _notebook_versions_dir(filename).is_dir()


def test_rename_notebook_moves_its_version_history_to_the_new_name():

    source = "versions_rename_source.ipynb"
    target = "versions_rename_target.ipynb"

    client.post(
        "/api/upload",
        files={
            "file": (
                source,
                io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                "application/json",
            )
        },
    )
    client.post(
        "/api/upload?overwrite=true",
        files={
            "file": (
                source,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )

    rename_resp = client.patch(
        f"/api/notebooks/{source}", json={"new_filename": target}
    )
    assert rename_resp.status_code == 200

    assert not _notebook_versions_dir(source).is_dir()

    versions = client.get(f"/api/notebooks/{target}/versions").json()["versions"]
    assert len(versions) == 1

    os.remove(Path(UPLOAD_DIR) / target)
    shutil.rmtree(_notebook_versions_dir(target), ignore_errors=True)


def test_rename_notebook_overwrite_discards_the_destinations_previous_version_history():

    source = "versions_rename_overwrite_source.ipynb"
    target = "versions_rename_overwrite_target.ipynb"

    for filename in (source, target):
        client.post(
            "/api/upload",
            files={
                "file": (
                    filename,
                    io.BytesIO(_notebook_bytes("def f() -> int:\n    return 1\n")),
                    "application/json",
                )
            },
        )
    client.post(
        f"/api/upload?overwrite=true",
        files={
            "file": (
                target,
                io.BytesIO(_notebook_bytes("def g() -> int:\n    return 2\n")),
                "application/json",
            )
        },
    )
    assert client.get(f"/api/notebooks/{target}/versions").json()["versions"]

    rename_resp = client.patch(
        f"/api/notebooks/{source}",
        json={"new_filename": target, "overwrite": True},
    )
    assert rename_resp.status_code == 200

    assert client.get(f"/api/notebooks/{target}/versions").json()["versions"] == []

    os.remove(Path(UPLOAD_DIR) / target)
    shutil.rmtree(_notebook_versions_dir(target), ignore_errors=True)


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


def test_inspect_rejects_a_notebook_path_with_an_embedded_null_byte():
    """Confirmed exploitable before this fix: a null byte in
    "notebook_path" sailed past resolve_upload_path's absolute-path guard
    clause (a null byte isn't special to pathlib's own parsing), but the
    later .resolve() call raised a bare ValueError from the underlying
    os.path.realpath/lstat syscalls, an unhandled 500 instead of the same
    clean 400 an absolute or traversal path already gets above.
    """

    resp = client.post("/api/inspect", json={"notebook_path": "nb\x00.ipynb"})

    assert resp.status_code == 400


def test_compile_rejects_a_notebook_path_with_an_embedded_null_byte():

    resp = client.post("/api/compile", json={"notebook_path": "nb\x00.ipynb"})

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


def test_compile_only_restricts_endpoints_to_the_named_functions():
    """POST /api/compile's "only" field mirrors the CLI's own local
    --only: only the named function(s) should become endpoints, and the
    response's "functions"/"endpoints" should reflect that restriction --
    not just the compiled app on disk.
    """

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_only_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "compile_only_test.ipynb", "only": ["add"]},
    )
    assert compile_resp.status_code == 200

    body = compile_resp.json()
    assert [f["name"] for f in body["functions"]] == ["add"]
    assert [e["path"] for e in body["endpoints"]] == ["/add"]

    generated_app_source = (Path("generated") / "app.py").read_text()
    assert "def add(" in generated_app_source
    assert "def subtract(" not in generated_app_source


def test_compile_exclude_removes_the_named_functions_endpoints():
    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_exclude_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "compile_exclude_test.ipynb", "exclude": ["subtract"]},
    )
    assert compile_resp.status_code == 200

    body = compile_resp.json()
    assert [f["name"] for f in body["functions"]] == ["add"]
    assert [e["path"] for e in body["endpoints"]] == ["/add"]


def test_compile_rejects_both_only_and_exclude():
    content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_only_and_exclude_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={
            "notebook_path": "compile_only_and_exclude_test.ipynb",
            "only": ["add"],
            "exclude": ["add"],
        },
    )
    assert compile_resp.status_code == 400
    assert "only and exclude" in compile_resp.json()["detail"]


def test_compile_only_names_an_unknown_function():
    content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_only_unknown_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={
            "notebook_path": "compile_only_unknown_test.ipynb",
            "only": ["does_not_exist"],
        },
    )
    assert compile_resp.status_code == 400
    assert "does_not_exist" in compile_resp.json()["detail"]


def test_compile_rejects_a_non_list_only_field():
    content = _notebook_bytes("def add(a: int, b: int) -> int:\n    return a + b\n")

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "compile_bad_only_test.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "compile_bad_only_test.ipynb", "only": "add"},
    )
    assert compile_resp.status_code == 400
    assert "only" in compile_resp.json()["detail"]


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


def test_inspect_returns_404_not_500_when_notebook_path_is_a_directory():
    """Confirmed exploitable before this fix: the existence check here
    used to be full_path.exists(), which is also true for a directory --
    and UPLOAD_DIR itself is a valid, in-bounds resolution target for
    notebook_path ("." resolves right back to it via resolve_upload_path,
    the same way it would for any other relative path staying within
    UPLOAD_DIR). The load_notebook call just after it raises
    IsADirectoryError for a directory -- an OSError subclass, not one of
    MALFORMED_NOTEBOOK_ERRORS -- so it propagated completely unhandled,
    past both of this endpoint's own try blocks, into FastAPI's generic,
    detail-free 500.
    """

    resp = client.post("/api/inspect", json={"notebook_path": "."})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Notebook file not found"


def test_compile_returns_404_not_500_when_notebook_path_is_a_directory():
    """Same underlying gap as /api/inspect's identical fix just above,
    for /api/compile's own identical full_path.exists() check -- this
    endpoint doesn't crash unhandled (its own broad `except Exception`
    catches the IsADirectoryError load_notebook raises), but it still
    surfaced as an unhelpful `500 {"detail": "Compilation error: [Errno
    21] Is a directory: ..."}` instead of the same clean 404 a missing
    notebook_path already gets.
    """

    resp = client.post("/api/compile", json={"notebook_path": "."})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Notebook file not found"


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


def test_validate_reports_pass_for_a_clean_notebook():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "validate_clean.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post("/api/validate", json={"notebook_path": "validate_clean.ipynb"})

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "pass",
        "notebook": "validate_clean.ipynb",
        "reserved_name_conflicts": [],
        "skipped_functions": [],
    }


def test_validate_reports_warn_for_skipped_functions_without_strict():

    content = _notebook_bytes(
        "def unsupported(a, **kwargs):\n    return a\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "validate_warn.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post("/api/validate", json={"notebook_path": "validate_warn.ipynb"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "warn"
    assert body["reserved_name_conflicts"] == []
    assert [f["name"] for f in body["skipped_functions"]] == ["unsupported"]


def test_validate_reports_fail_for_skipped_functions_with_strict():

    content = _notebook_bytes(
        "def unsupported(a, **kwargs):\n    return a\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "validate_strict_fail.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/validate",
        json={"notebook_path": "validate_strict_fail.ipynb", "strict": True},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "fail"


def test_validate_reports_fail_for_a_reserved_name_conflict_even_without_strict():

    content = _notebook_bytes(
        "def health_check() -> dict:\n    return {}\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "validate_reserved.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post("/api/validate", json={"notebook_path": "validate_reserved.ipynb"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fail"
    assert body["reserved_name_conflicts"] == ["health_check"]


def test_validate_does_not_touch_generated_dir(monkeypatch, tmp_path):

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "validate_no_side_effects.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    generated_dir = tmp_path / "validate_generated"
    monkeypatch.setattr("backend.routes.upload.GENERATED_DIR", str(generated_dir))

    resp = client.post(
        "/api/validate", json={"notebook_path": "validate_no_side_effects.ipynb"}
    )

    assert resp.status_code == 200
    assert not generated_dir.exists()


def test_validate_returns_404_for_a_missing_notebook():

    resp = client.post(
        "/api/validate", json={"notebook_path": "does_not_exist.ipynb"}
    )

    assert resp.status_code == 404


def test_validate_returns_400_for_a_malformed_notebook_file():

    filename = "validate_malformed.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.post("/api/validate", json={"notebook_path": filename})

    assert resp.status_code == 400


def test_validate_all_reports_pass_warn_and_fail_across_the_catalog():

    client.delete("/api/notebooks?confirm=true")

    clean_content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    warn_content = _notebook_bytes(
        "def unsupported(a, **kwargs):\n    return a\n\n"
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )
    fail_content = _notebook_bytes(
        "def health_check() -> dict:\n    return {}\n"
    )

    for filename, content in (
        ("validate_all_pass.ipynb", clean_content),
        ("validate_all_warn.ipynb", warn_content),
        ("validate_all_fail.ipynb", fail_content),
    ):
        resp = client.post(
            "/api/upload",
            files={"file": (filename, io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 200

    resp = client.get("/api/validate-all")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["pass_count"] == 1
    assert body["warn_count"] == 1
    assert body["fail_count"] == 1

    results_by_filename = {r["filename"]: r for r in body["results"]}
    assert results_by_filename["validate_all_pass.ipynb"]["status"] == "pass"
    assert results_by_filename["validate_all_warn.ipynb"]["status"] == "warn"
    assert results_by_filename["validate_all_fail.ipynb"]["status"] == "fail"
    assert results_by_filename["validate_all_fail.ipynb"]["reserved_name_conflicts"] == [
        "health_check"
    ]


def test_validate_all_strict_turns_skipped_functions_into_a_failure():

    client.delete("/api/notebooks?confirm=true")

    warn_content = _notebook_bytes(
        "def unsupported(a, **kwargs):\n    return a\n"
    )

    resp = client.post(
        "/api/upload",
        files={"file": ("validate_all_strict.ipynb", io.BytesIO(warn_content), "application/json")},
    )
    assert resp.status_code == 200

    resp = client.get("/api/validate-all", params={"strict": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["fail_count"] == 1
    assert body["warn_count"] == 0
    assert body["results"][0]["status"] == "fail"


def test_validate_all_reports_a_malformed_notebook_as_fail_instead_of_skipping_it():
    """Deliberately different from GET /api/functions' own bulk search,
    which silently skips a notebook it can't parse -- here, a parse
    failure is exactly the kind of problem this endpoint exists to
    surface, not incidental to a different question.
    """

    client.delete("/api/notebooks?confirm=true")

    filename = "validate_all_malformed.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.get("/api/validate-all")

    assert resp.status_code == 200
    body = resp.json()
    assert body["fail_count"] == 1
    assert body["results"][0]["filename"] == filename
    assert body["results"][0]["status"] == "fail"
    assert "not a valid Jupyter notebook" in body["results"][0]["detail"]


def test_validate_all_reports_zero_when_nothing_uploaded():

    client.delete("/api/notebooks?confirm=true")

    resp = client.get("/api/validate-all")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "results": [],
        "pass_count": 0,
        "warn_count": 0,
        "fail_count": 0,
    }


def test_requirements_preview_matches_what_an_actual_compile_writes():

    content = _notebook_bytes(
        "import pandas\n\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "requirements_preview_match.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    preview_resp = client.post(
        "/api/requirements-preview",
        json={"notebook_path": "requirements_preview_match.ipynb"},
    )
    assert preview_resp.status_code == 200
    preview_body = preview_resp.json()
    assert preview_body["status"] == "success"
    assert preview_body["notebook"] == "requirements_preview_match.ipynb"

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "requirements_preview_match.ipynb"},
    )
    assert compile_resp.status_code == 200

    actual_requirements = client.get(
        "/api/generated/requirements.txt"
    ).json()["content"].split()

    assert sorted(preview_body["requirements"]) == sorted(actual_requirements)
    assert any(dep.startswith("fastapi") for dep in preview_body["requirements"])
    assert any(dep.startswith("pandas") for dep in preview_body["requirements"])


def test_requirements_preview_includes_an_explicit_requirement_directive():

    content = _notebook_bytes(
        "# notebook-to-api: requires definitely-not-a-real-pkg==1.2.3\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "requirements_preview_directive.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/requirements-preview",
        json={"notebook_path": "requirements_preview_directive.ipynb"},
    )

    assert resp.status_code == 200
    assert "definitely-not-a-real-pkg==1.2.3" in resp.json()["requirements"]


def test_requirements_preview_falls_back_to_a_bare_name_for_an_uninstalled_dependency():

    content = _notebook_bytes(
        "import definitely_not_installed_pkg_hopefully\n\n"
        "def noop() -> int:\n    return 1\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "requirements_preview_uninstalled.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/requirements-preview",
        json={"notebook_path": "requirements_preview_uninstalled.ipynb"},
    )

    assert resp.status_code == 200
    assert "definitely_not_installed_pkg_hopefully" in resp.json()["requirements"]


def test_requirements_preview_does_not_touch_generated_dir(monkeypatch, tmp_path):

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "requirements_preview_no_side_effects.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    generated_dir = tmp_path / "requirements_preview_generated"
    monkeypatch.setattr("backend.routes.upload.GENERATED_DIR", str(generated_dir))

    resp = client.post(
        "/api/requirements-preview",
        json={"notebook_path": "requirements_preview_no_side_effects.ipynb"},
    )

    assert resp.status_code == 200
    assert not generated_dir.exists()


def test_requirements_preview_returns_404_for_a_missing_notebook():

    resp = client.post(
        "/api/requirements-preview", json={"notebook_path": "does_not_exist.ipynb"}
    )

    assert resp.status_code == 404


def test_requirements_preview_returns_400_for_a_malformed_notebook_file():

    filename = "requirements_preview_malformed.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.post("/api/requirements-preview", json={"notebook_path": filename})

    assert resp.status_code == 400


def test_requirements_preview_requires_a_notebook_path():

    resp = client.post("/api/requirements-preview", json={})

    assert resp.status_code == 400


def test_app_preview_matches_what_an_actual_compile_writes():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "app_preview_match.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    preview_resp = client.post(
        "/api/app-preview",
        json={"notebook_path": "app_preview_match.ipynb"},
    )
    assert preview_resp.status_code == 200
    preview_body = preview_resp.json()
    assert preview_body["status"] == "success"
    assert preview_body["notebook"] == "app_preview_match.ipynb"
    assert preview_body["package_name"] == "generated"
    assert "def add(" in preview_body["app_code"]

    compile_resp = client.post(
        "/api/compile",
        json={"notebook_path": "app_preview_match.ipynb"},
    )
    assert compile_resp.status_code == 200

    actual_app_code = client.get("/api/generated/app.py").json()["content"]

    assert preview_body["app_code"] == actual_app_code


def test_app_preview_respects_only_and_exclude():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "app_preview_only.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/app-preview",
        json={"notebook_path": "app_preview_only.ipynb", "only": ["add"]},
    )

    assert resp.status_code == 200
    app_code = resp.json()["app_code"]
    assert "def add(" in app_code
    assert "def subtract(" not in app_code


def test_app_preview_does_not_touch_generated_dir(monkeypatch, tmp_path):

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "app_preview_no_side_effects.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    generated_dir = tmp_path / "app_preview_generated"
    monkeypatch.setattr("backend.routes.upload.GENERATED_DIR", str(generated_dir))

    resp = client.post(
        "/api/app-preview",
        json={"notebook_path": "app_preview_no_side_effects.ipynb"},
    )

    assert resp.status_code == 200
    assert not generated_dir.exists()


def test_app_preview_returns_404_for_a_missing_notebook():

    resp = client.post(
        "/api/app-preview", json={"notebook_path": "does_not_exist.ipynb"}
    )

    assert resp.status_code == 404


def test_app_preview_returns_400_for_a_malformed_notebook_file():

    filename = "app_preview_malformed.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.post("/api/app-preview", json={"notebook_path": filename})

    assert resp.status_code == 400


def test_app_preview_requires_a_notebook_path():

    resp = client.post("/api/app-preview", json={})

    assert resp.status_code == 400


def test_app_preview_rejects_both_only_and_exclude():

    resp = client.post(
        "/api/app-preview",
        json={
            "notebook_path": "anything.ipynb",
            "only": ["a"],
            "exclude": ["b"],
        },
    )

    assert resp.status_code == 400


def test_app_preview_returns_400_for_an_unknown_only_name():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "app_preview_unknown_only.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/app-preview",
        json={
            "notebook_path": "app_preview_unknown_only.ipynb",
            "only": ["does_not_exist_fn"],
        },
    )

    assert resp.status_code == 400
    assert "does_not_exist_fn" in resp.json()["detail"]


def test_app_preview_returns_400_for_a_reserved_function_name():

    content = _notebook_bytes(
        "def health_check() -> dict:\n    return {}\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "app_preview_reserved_name.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/app-preview",
        json={"notebook_path": "app_preview_reserved_name.ipynb"},
    )

    assert resp.status_code == 400
    assert "health_check" in resp.json()["detail"]


def test_curl_preview_returns_one_command_per_function():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "curl_preview_basic.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/curl-preview",
        json={"notebook_path": "curl_preview_basic.ipynb"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["notebook"] == "curl_preview_basic.ipynb"
    assert len(body["commands"]) == 2
    assert "curl -X POST http://localhost:8000/add" in body["commands"][0]
    assert "X-API-Key: notebook-to-api-dev-key" in body["commands"][0]
    assert "curl -X POST http://localhost:8000/subtract" in body["commands"][1]


def test_curl_preview_respects_custom_host_port_and_api_key():

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "curl_preview_custom.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/curl-preview",
        json={
            "notebook_path": "curl_preview_custom.ipynb",
            "host": "api.example.com",
            "port": 9000,
            "api_key": "mykey123",
        },
    )

    assert resp.status_code == 200
    command = resp.json()["commands"][0]
    assert "curl -X POST http://api.example.com:9000/add" in command
    assert "X-API-Key: mykey123" in command


def test_curl_preview_excludes_a_reserved_name_conflict():

    content = _notebook_bytes(
        "def health_check() -> dict:\n    return {}\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "curl_preview_reserved.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    resp = client.post(
        "/api/curl-preview",
        json={"notebook_path": "curl_preview_reserved.ipynb"},
    )

    assert resp.status_code == 200
    assert resp.json()["commands"] == []


def test_curl_preview_does_not_touch_generated_dir(monkeypatch, tmp_path):

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    upload_resp = client.post(
        "/api/upload",
        files={
            "file": (
                "curl_preview_no_side_effects.ipynb",
                io.BytesIO(content),
                "application/json",
            )
        },
    )
    assert upload_resp.status_code == 200

    generated_dir = tmp_path / "curl_preview_generated"
    monkeypatch.setattr("backend.routes.upload.GENERATED_DIR", str(generated_dir))

    resp = client.post(
        "/api/curl-preview",
        json={"notebook_path": "curl_preview_no_side_effects.ipynb"},
    )

    assert resp.status_code == 200
    assert not generated_dir.exists()


def test_curl_preview_returns_404_for_a_missing_notebook():

    resp = client.post(
        "/api/curl-preview", json={"notebook_path": "does_not_exist.ipynb"}
    )

    assert resp.status_code == 404


def test_curl_preview_returns_400_for_a_malformed_notebook_file():

    filename = "curl_preview_malformed.ipynb"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("not valid json at all")

    resp = client.post("/api/curl-preview", json={"notebook_path": filename})

    assert resp.status_code == 400


def test_curl_preview_requires_a_notebook_path():

    resp = client.post("/api/curl-preview", json={})

    assert resp.status_code == 400


def test_curl_preview_rejects_a_non_integer_port():

    _upload_sample_notebook("curl_preview_bad_port.ipynb")

    resp = client.post(
        "/api/curl-preview",
        json={"notebook_path": "curl_preview_bad_port.ipynb", "port": "not-a-number"},
    )

    assert resp.status_code == 400


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


def test_deploy_history_is_empty_before_any_deploy(monkeypatch, tmp_path):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "deploy_history_empty_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    resp = client.get("/api/deploy/history")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "entries": [],
        "entry_count": 0,
    }


def test_deploy_records_a_history_entry_on_successful_build(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_history_record_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={"tag": "myapp:v1"})
    assert resp.status_code == 200

    history_resp = client.get("/api/deploy/history")
    assert history_resp.status_code == 200
    body = history_resp.json()
    assert body["entry_count"] >= 1

    entry = body["entries"][0]
    assert entry["tag"] == "myapp:v1"
    assert entry["platform"] is None
    assert entry["pushed"] is False
    assert entry["source_notebook_filename"] == "deploy_history_record_test.ipynb"
    assert entry["source_notebook_sha256"]
    assert "deployed_at" in entry


def test_deploy_records_pushed_true_when_push_requested(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_history_pushed_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post(
        "/api/deploy",
        json={"tag": "myapp:pushed", "push": True, "platform": "linux/amd64"},
    )
    assert resp.status_code == 200

    entry = client.get("/api/deploy/history").json()["entries"][0]
    assert entry["tag"] == "myapp:pushed"
    assert entry["pushed"] is True
    assert entry["platform"] == "linux/amd64"


def test_deploy_history_lists_most_recent_first(tmp_path, monkeypatch):

    _compile_a_notebook("deploy_history_order_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    client.post("/api/deploy", json={"tag": "order:first"})
    client.post("/api/deploy", json={"tag": "order:second"})

    entries = client.get("/api/deploy/history").json()["entries"]
    tags_in_order = [e["tag"] for e in entries if e["tag"] in ("order:first", "order:second")]
    assert tags_in_order == ["order:second", "order:first"]


def test_deploy_history_is_capped_at_the_configured_maximum(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "deploy_history_cap_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))
    monkeypatch.setattr(upload_module, "MAX_DEPLOY_HISTORY_ENTRIES", 3)

    for i in range(5):
        upload_module._append_deploy_history_entry({
            "deployed_at": f"2024-01-0{i + 1}T00:00:00+00:00",
            "tag": f"cap:{i}",
            "platform": None,
            "pushed": False,
            "source_notebook_filename": None,
            "source_notebook_sha256": None,
        })

    body = client.get("/api/deploy/history").json()
    assert body["entry_count"] == 3
    assert [e["tag"] for e in body["entries"]] == ["cap:4", "cap:3", "cap:2"]


def _seed_deploy_history_for_filtering(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "deploy_history_filter_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    entries = [
        {
            "deployed_at": "2024-01-01T00:00:00+00:00", "tag": "filter:a",
            "platform": "linux/amd64", "pushed": True,
            "source_notebook_filename": "one.ipynb", "source_notebook_sha256": "aaa",
        },
        {
            "deployed_at": "2024-01-02T00:00:00+00:00", "tag": "filter:b",
            "platform": "linux/arm64", "pushed": False,
            "source_notebook_filename": "two.ipynb", "source_notebook_sha256": "bbb",
        },
        {
            "deployed_at": "2024-01-03T00:00:00+00:00", "tag": "filter:c",
            "platform": "linux/amd64", "pushed": False,
            "source_notebook_filename": "one.ipynb", "source_notebook_sha256": "ccc",
        },
    ]

    for entry in entries:
        upload_module._append_deploy_history_entry(entry)


def test_deploy_history_filters_by_source_notebook_filename(tmp_path, monkeypatch):

    _seed_deploy_history_for_filtering(tmp_path, monkeypatch)

    body = client.get(
        "/api/deploy/history", params={"source_notebook_filename": "one.ipynb"}
    ).json()

    assert [e["tag"] for e in body["entries"]] == ["filter:c", "filter:a"]
    assert body["entry_count"] == 2


def test_deploy_history_filters_by_platform(tmp_path, monkeypatch):

    _seed_deploy_history_for_filtering(tmp_path, monkeypatch)

    body = client.get("/api/deploy/history", params={"platform": "linux/arm64"}).json()

    assert [e["tag"] for e in body["entries"]] == ["filter:b"]


def test_deploy_history_filters_by_pushed(tmp_path, monkeypatch):

    _seed_deploy_history_for_filtering(tmp_path, monkeypatch)

    pushed_body = client.get("/api/deploy/history", params={"pushed": "true"}).json()
    assert [e["tag"] for e in pushed_body["entries"]] == ["filter:a"]

    not_pushed_body = client.get("/api/deploy/history", params={"pushed": "false"}).json()
    assert [e["tag"] for e in not_pushed_body["entries"]] == ["filter:c", "filter:b"]


def test_deploy_history_respects_limit(tmp_path, monkeypatch):

    _seed_deploy_history_for_filtering(tmp_path, monkeypatch)

    body = client.get("/api/deploy/history", params={"limit": 2}).json()

    assert [e["tag"] for e in body["entries"]] == ["filter:c", "filter:b"]
    assert body["entry_count"] == 2


def test_deploy_history_combines_filters_and_limit(tmp_path, monkeypatch):

    _seed_deploy_history_for_filtering(tmp_path, monkeypatch)

    body = client.get(
        "/api/deploy/history",
        params={"source_notebook_filename": "one.ipynb", "limit": 1},
    ).json()

    assert [e["tag"] for e in body["entries"]] == ["filter:c"]


def test_deploy_history_rejects_a_negative_limit(tmp_path, monkeypatch):

    _seed_deploy_history_for_filtering(tmp_path, monkeypatch)

    resp = client.get("/api/deploy/history", params={"limit": -1})

    assert resp.status_code == 400


def test_deploy_does_not_record_a_history_entry_on_build_failure(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "deploy_history_build_failure_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    _compile_a_notebook("deploy_history_build_failure_test.ipynb")

    bin_dir = tmp_path / "fakebin"
    docker_stub = bin_dir / "docker"
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    resp = client.post("/api/deploy", json={})
    assert resp.status_code == 500

    assert client.get("/api/deploy/history").json()["entries"] == []


def test_clear_deploy_history_removes_every_entry(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "clear_deploy_history_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    for i in range(3):
        upload_module._append_deploy_history_entry({
            "deployed_at": f"2024-01-0{i + 1}T00:00:00+00:00",
            "tag": f"clear:{i}",
            "platform": None,
            "pushed": False,
            "source_notebook_filename": None,
            "source_notebook_sha256": None,
        })

    assert client.get("/api/deploy/history").json()["entry_count"] == 3

    clear_resp = client.delete("/api/deploy/history")

    assert clear_resp.status_code == 200
    assert clear_resp.json() == {"status": "success", "deleted_count": 3}

    assert client.get("/api/deploy/history").json() == {
        "status": "success",
        "entries": [],
        "entry_count": 0,
    }


def test_clear_deploy_history_is_a_no_op_success_when_nothing_was_ever_deployed(
    tmp_path, monkeypatch
):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "clear_deploy_history_empty_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    resp = client.delete("/api/deploy/history")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "deleted_count": 0}


def test_clear_deploy_history_does_not_touch_generated_dir_or_notebooks(
    tmp_path, monkeypatch
):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "clear_deploy_history_isolation_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    upload_module._append_deploy_history_entry({
        "deployed_at": "2024-01-01T00:00:00+00:00",
        "tag": "isolation:0",
        "platform": None,
        "pushed": False,
        "source_notebook_filename": None,
        "source_notebook_sha256": None,
    })

    generated_dir_before = Path(upload_module.GENERATED_DIR)
    dockerfile_existed_before = (generated_dir_before / "Dockerfile").is_file()

    client.delete("/api/deploy/history")

    assert (generated_dir_before / "Dockerfile").is_file() == dockerfile_existed_before


def test_compile_history_is_empty_before_any_compile(monkeypatch, tmp_path):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "compile_history_empty_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    resp = client.get("/api/compile/history")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "entries": [], "entry_count": 0}


def test_compile_records_a_history_entry(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "compile_history_record_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    _compile_a_notebook("compile_history_record_test.ipynb")

    history_resp = client.get("/api/compile/history")
    assert history_resp.status_code == 200

    body = history_resp.json()
    assert body["entry_count"] == 1

    entry = body["entries"][0]
    assert entry["notebook_filename"] == "compile_history_record_test.ipynb"
    assert entry["endpoint_count"] == 1
    assert entry["only"] is None
    assert entry["exclude"] is None
    assert entry["source_notebook_sha256"]
    assert entry["compiled_at"]


def test_compile_history_records_only_and_exclude(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "compile_history_only_exclude_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    content = _notebook_bytes(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
        "def sub(a: int, b: int) -> int:\n    return a - b\n"
    )
    client.post(
        "/api/upload",
        files={
            "file": (
                "compile_history_only_test.ipynb", io.BytesIO(content),
                "application/json",
            )
        },
    )

    resp = client.post(
        "/api/compile",
        json={"notebook_path": "compile_history_only_test.ipynb", "only": ["add"]},
    )
    assert resp.status_code == 200

    entry = client.get("/api/compile/history").json()["entries"][0]
    assert entry["only"] == ["add"]
    assert entry["exclude"] is None
    assert entry["endpoint_count"] == 1


def test_compile_history_lists_most_recent_first(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "compile_history_order_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    _compile_a_notebook("compile_history_order_a.ipynb")
    _compile_a_notebook("compile_history_order_b.ipynb")

    entries = client.get("/api/compile/history").json()["entries"]
    filenames_in_order = [e["notebook_filename"] for e in entries]
    assert filenames_in_order == [
        "compile_history_order_b.ipynb", "compile_history_order_a.ipynb",
    ]


def test_compile_history_is_capped_at_the_configured_maximum(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "compile_history_cap_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))
    monkeypatch.setattr(upload_module, "MAX_COMPILE_HISTORY_ENTRIES", 3)

    for i in range(5):
        upload_module._append_compile_history_entry({
            "compiled_at": f"2024-01-0{i + 1}T00:00:00+00:00",
            "notebook_filename": f"cap_{i}.ipynb",
            "source_notebook_sha256": "abc",
            "only": None,
            "exclude": None,
            "endpoint_count": 1,
            "dependency_count": 0,
            "skipped_function_count": 0,
        })

    body = client.get("/api/compile/history").json()
    assert body["entry_count"] == 3
    assert [e["notebook_filename"] for e in body["entries"]] == [
        "cap_4.ipynb", "cap_3.ipynb", "cap_2.ipynb",
    ]


def _seed_compile_history_for_filtering(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "compile_history_filter_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    entries = [
        {
            "compiled_at": "2024-01-01T00:00:00+00:00", "notebook_filename": "one.ipynb",
            "source_notebook_sha256": "aaa", "only": None, "exclude": None,
            "endpoint_count": 1, "dependency_count": 0, "skipped_function_count": 0,
        },
        {
            "compiled_at": "2024-01-02T00:00:00+00:00", "notebook_filename": "two.ipynb",
            "source_notebook_sha256": "bbb", "only": None, "exclude": None,
            "endpoint_count": 2, "dependency_count": 1, "skipped_function_count": 0,
        },
        {
            "compiled_at": "2024-01-03T00:00:00+00:00", "notebook_filename": "one.ipynb",
            "source_notebook_sha256": "ccc", "only": None, "exclude": None,
            "endpoint_count": 3, "dependency_count": 1, "skipped_function_count": 1,
        },
    ]

    for entry in entries:
        upload_module._append_compile_history_entry(entry)


def test_compile_history_filters_by_notebook_filename(tmp_path, monkeypatch):

    _seed_compile_history_for_filtering(tmp_path, monkeypatch)

    body = client.get(
        "/api/compile/history", params={"notebook_filename": "one.ipynb"}
    ).json()

    assert [e["source_notebook_sha256"] for e in body["entries"]] == ["ccc", "aaa"]
    assert body["entry_count"] == 2


def test_compile_history_filters_by_unknown_notebook_filename_yields_no_entries(
    tmp_path, monkeypatch
):

    _seed_compile_history_for_filtering(tmp_path, monkeypatch)

    body = client.get(
        "/api/compile/history", params={"notebook_filename": "does_not_exist.ipynb"}
    ).json()

    assert body == {"status": "success", "entries": [], "entry_count": 0}


def test_compile_history_respects_limit(tmp_path, monkeypatch):

    _seed_compile_history_for_filtering(tmp_path, monkeypatch)

    body = client.get("/api/compile/history", params={"limit": 2}).json()

    assert [e["source_notebook_sha256"] for e in body["entries"]] == ["ccc", "bbb"]
    assert body["entry_count"] == 2


def test_compile_history_combines_notebook_filter_and_limit(tmp_path, monkeypatch):

    _seed_compile_history_for_filtering(tmp_path, monkeypatch)

    body = client.get(
        "/api/compile/history",
        params={"notebook_filename": "one.ipynb", "limit": 1},
    ).json()

    assert [e["source_notebook_sha256"] for e in body["entries"]] == ["ccc"]


def test_compile_history_rejects_a_negative_limit(tmp_path, monkeypatch):

    _seed_compile_history_for_filtering(tmp_path, monkeypatch)

    resp = client.get("/api/compile/history", params={"limit": -1})

    assert resp.status_code == 400


def test_clear_compile_history_removes_every_entry(tmp_path, monkeypatch):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "clear_compile_history_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    for i in range(3):
        upload_module._append_compile_history_entry({
            "compiled_at": f"2024-01-0{i + 1}T00:00:00+00:00",
            "notebook_filename": f"clear_{i}.ipynb",
            "source_notebook_sha256": "abc",
            "only": None,
            "exclude": None,
            "endpoint_count": 1,
            "dependency_count": 0,
            "skipped_function_count": 0,
        })

    assert client.get("/api/compile/history").json()["entry_count"] == 3

    clear_resp = client.delete("/api/compile/history")

    assert clear_resp.status_code == 200
    assert clear_resp.json() == {"status": "success", "deleted_count": 3}
    assert client.get("/api/compile/history").json() == {
        "status": "success", "entries": [], "entry_count": 0,
    }


def test_clear_compile_history_is_a_no_op_success_when_nothing_was_ever_compiled(
    tmp_path, monkeypatch
):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "clear_compile_history_empty_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    resp = client.delete("/api/compile/history")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "deleted_count": 0}


def test_clear_compile_history_does_not_touch_generated_dir_or_notebooks(
    tmp_path, monkeypatch
):

    from backend.routes import upload as upload_module

    isolated_upload_dir = tmp_path / "clear_compile_history_isolation_upload_dir"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(isolated_upload_dir))

    upload_module._append_compile_history_entry({
        "compiled_at": "2024-01-01T00:00:00+00:00",
        "notebook_filename": "isolation.ipynb",
        "source_notebook_sha256": "abc",
        "only": None,
        "exclude": None,
        "endpoint_count": 1,
        "dependency_count": 0,
        "skipped_function_count": 0,
    })

    generated_dir_before = Path(upload_module.GENERATED_DIR)
    dockerfile_existed_before = (generated_dir_before / "Dockerfile").is_file()

    client.delete("/api/compile/history")

    assert (generated_dir_before / "Dockerfile").is_file() == dockerfile_existed_before


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


def test_download_reports_not_stale_right_after_compile():

    filename = "download_not_stale_test.ipynb"
    _compile_a_notebook(filename)

    resp = client.get("/api/download")

    assert resp.status_code == 200
    assert resp.headers["x-notebook-changed-since-compile"] == "false"


def test_download_reports_stale_after_the_source_notebook_changes():
    """Unlike POST /api/deploy, GET /api/download never refuses a stale
    build outright -- it has no "force" escape hatch, and downloading a
    zip doesn't ship it anywhere the way a Docker build/push would -- but
    a caller who does care about staleness (e.g. this CLI's own
    `remote-build`, which warns on it) needs a way to find out without a
    separate GET /api/notebooks call.
    """

    filename = "download_stale_test.ipynb"
    _compile_a_notebook(filename)

    changed_content = _notebook_bytes(
        "def subtract(a: int, b: int) -> int:\n    return a - b\n"
    )
    overwrite_resp = client.post(
        "/api/upload?overwrite=true",
        files={"file": (filename, io.BytesIO(changed_content), "application/json")},
    )
    assert overwrite_resp.status_code == 200

    resp = client.get("/api/download")

    assert resp.status_code == 200
    assert resp.headers["x-notebook-changed-since-compile"] == "true"

    # Still returns the (now-stale) zip itself -- unlike /api/deploy, this
    # never turns into a 409.
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "app.py" in archive.namelist()


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
    assert body["file_details"] == []
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


def test_list_generated_files_file_details_reports_size_and_modified_at():
    """"file_details" closes a gap "generated_files" (a bare list of
    names) always had: a dashboard frontend wanting to show a real file
    browser for what's compiled (file sizes, most-recently-touched-first,
    ...) had to issue a separate GET /api/generated/{filename} call per
    file just to learn how big each one is -- exactly the level of detail
    GET /api/notebooks already reports per uploaded notebook.
    """

    from backend.routes import upload as upload_module

    filename = "list_generated_files_file_details_test.ipynb"
    _compile_a_notebook(filename)

    resp = client.get("/api/generated")

    assert resp.status_code == 200
    body = resp.json()

    file_details_by_name = {
        entry["filename"]: entry for entry in body["file_details"]
    }

    assert set(file_details_by_name) == set(body["generated_files"])

    app_py_details = file_details_by_name["app.py"]
    expected_size = (Path(upload_module.GENERATED_DIR) / "app.py").stat().st_size

    assert app_py_details["size_bytes"] == expected_size
    assert app_py_details["size_bytes"] > 0
    assert "modified_at" in app_py_details

    os.remove(Path(UPLOAD_DIR) / filename)


def test_list_generated_files_file_details_excludes_pycache_and_compile_metadata():

    from backend.routes import upload as upload_module

    filename = "list_generated_files_file_details_exclusions_test.ipynb"
    _compile_a_notebook(filename)

    pycache_dir = Path(upload_module.GENERATED_DIR) / "__pycache__"
    pycache_dir.mkdir(exist_ok=True)
    (pycache_dir / "app.cpython-000.pyc").write_bytes(b"")

    resp = client.get("/api/generated")

    assert resp.status_code == 200
    body = resp.json()

    detail_filenames = {entry["filename"] for entry in body["file_details"]}

    assert not any("__pycache__" in name for name in detail_filenames)
    assert COMPILE_METADATA_FILENAME not in detail_filenames

    shutil.rmtree(pycache_dir)
    os.remove(Path(UPLOAD_DIR) / filename)


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


def test_get_generated_file_rejects_a_filename_with_an_embedded_null_byte():
    """Confirmed exploitable before this fix: a null byte in the filename
    sailed past resolve_generated_path's absolute-path guard clause, but
    the later .resolve() call raised a bare ValueError from the
    underlying os.path.realpath/lstat syscalls, an unhandled 500 instead
    of the same clean 400/404 an absolute or traversal path already gets
    above.
    """

    _compile_a_notebook("get_file_null_byte_test.ipynb")

    resp = client.get("/api/generated/app%00.py")

    assert resp.status_code == 400


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


def test_get_config_reports_the_configured_limits():

    resp = client.get("/api/config")

    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "success"
    assert isinstance(body["max_upload_bytes"], int)
    assert isinstance(body["max_batch_upload_files"], int)
    assert isinstance(body["max_notebook_versions"], int)
    assert isinstance(body["max_tag_length"], int)
    assert isinstance(body["max_tags_per_notebook"], int)
    assert isinstance(body["deploy_subprocess_timeout_seconds"], int)


def test_get_config_reflects_a_configured_max_upload_bytes(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 12345)

    resp = client.get("/api/config")

    assert resp.json()["max_upload_bytes"] == 12345


def test_get_config_reflects_a_configured_max_batch_upload_files(monkeypatch):

    from backend.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "MAX_BATCH_UPLOAD_FILES", 7)

    resp = client.get("/api/config")

    assert resp.json()["max_batch_upload_files"] == 7


def test_get_config_reports_notebook_sort_keys_and_orders_matching_list_notebooks():
    """GET /api/notebooks' own "sort"/"order" query parameters accept
    exactly _NOTEBOOK_SORT_KEYS/_NOTEBOOK_SORT_ORDERS -- this must report
    the same values, not a second, independently-drifting copy of them.
    """

    from backend.routes.upload import _NOTEBOOK_SORT_KEYS, _NOTEBOOK_SORT_ORDERS

    resp = client.get("/api/config")
    body = resp.json()

    assert body["notebook_sort_keys"] == sorted(_NOTEBOOK_SORT_KEYS)
    assert body["notebook_sort_orders"] == sorted(_NOTEBOOK_SORT_ORDERS)

    for sort_key in body["notebook_sort_keys"]:
        assert client.get(f"/api/notebooks?sort={sort_key}").status_code == 200

    for order in body["notebook_sort_orders"]:
        assert client.get(f"/api/notebooks?order={order}").status_code == 200


def test_get_config_never_leaks_the_upload_or_generated_directory_path():
    """UPLOAD_DIR/GENERATED_DIR are filesystem paths on the compiling
    server -- the same category of information GET /api/health's own
    docstring already explains has no business leaking out of a
    dashboard API response.
    """

    resp = client.get("/api/config")

    assert resp.status_code == 200
    body_text = json.dumps(resp.json())

    assert "upload_dir" not in resp.json()
    assert "generated_dir" not in resp.json()
    assert UPLOAD_DIR not in body_text
