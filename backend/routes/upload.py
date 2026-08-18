from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from datetime import datetime, timezone
import io
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile

# ValidationError is the exception nbformat raises for a syntactically
# valid JSON file that is nonetheless missing required notebook keys
# (e.g. no "cells"). It's distinct from nbformat.reader.NotJSONError
# (already a ValueError subclass) and needs to be named explicitly to be
# treated as a 400 (the notebook itself is malformed, not a server bug),
# same as the CLI already does for the identical failure mode (see
# CLI_USER_FACING_ERRORS in cli.py).
from nbformat import ValidationError as NotebookValidationError

from backend.compiler import (
    COMPILE_LOCK,
    COMPILE_METADATA_FILENAME,
    compile_notebook,
    hash_notebook_file,
    package_name_for_output_dir,
    update_compile_metadata_source_notebook,
)
from backend.generator.api_generator import (
    ReservedFunctionNameError,
)
from backend.inspector import (
    EXCLUDED_GENERATED_DIR_NAMES,
    EXCLUDED_GENERATED_FILE_NAMES,
    inspect_notebook_data,
    list_generated_files,
)
from backend.parser.notebook_parser import (
    load_notebook,
)

# Endpoints below that operate on the compiled app (export-openapi,
# export-sdk) mirror /api/compile in always targeting this fixed directory
# rather than accepting a client-supplied path: export_openapi_schema
# dynamically imports "<package_name>.app" to read its live OpenAPI schema,
# so letting a network caller pick an arbitrary package/directory to import
# would be a far bigger trust boundary than the CLI's --app-dir (a local,
# already-trusted operator flag).
#
# Configurable via NOTEBOOK_API_GENERATED_DIR, matching the app's existing
# NOTEBOOK_API_* env-var convention (see MAX_UPLOAD_BYTES and
# DEPLOY_SUBPROCESS_TIMEOUT_SECONDS below), rather than being permanently
# fixed to "generated" with no way for an operator to point the dashboard
# API at a different output directory (e.g. to avoid colliding with a
# `compile --output` a developer already runs by hand alongside it).
GENERATED_DIR = os.getenv("NOTEBOOK_API_GENERATED_DIR", "generated")

# A malformed notebook file (invalid JSON, or valid JSON missing required
# notebook keys) is a problem with the client-supplied content, not this
# server. POST /api/inspect and POST /api/compile each validate with a
# dedicated load_notebook() call before doing anything else -- mirroring
# the same pattern upload_notebook already uses below to validate an
# upload -- so this can be caught precisely at that one call and reported
# as a 400 the caller can act on (re-upload a valid notebook), not a 500,
# which previously made a bad file look like a server-side bug. The same
# distinction ReservedFunctionNameError already gets in POST /api/compile
# for a different failure mode; this closes the identical gap for the
# earlier, more fundamental one. NotJSONError (invalid JSON) is already a
# ValueError subclass; NotebookValidationError (valid JSON, missing
# required notebook keys) is named explicitly since it isn't.
MALFORMED_NOTEBOOK_ERRORS = (NotebookValidationError, ValueError)

router = APIRouter(
    prefix="/api",
    tags=["dashboard"]
)

# Every route below except upload_notebook (which genuinely awaits
# UploadFile.read) is declared as a plain `def`, not `async def`. FastAPI
# only runs `async def` path operations directly on the single asyncio
# event loop; every one of these does purely synchronous, blocking work
# (file I/O, subprocess.run for `docker build`/`docker push` -- up to
# DEPLOY_SUBPROCESS_TIMEOUT_SECONDS, 600s by default -- and
# compile_notebook's own file writes and per-dependency
# importlib.metadata.version() lookups) with no `await` anywhere in it.
# Declared `async def` with no `await`, a handler never yields control
# back to the loop for its entire duration, so it doesn't just block the
# one client that made that request -- it blocks *every* concurrent
# request this server is handling, including an unrelated GET
# /api/health from a completely different caller, for as long as it
# runs. Confirmed against a real (non-TestClient) uvicorn server: an
# async def endpoint blocking for 1.5s with no await delayed a
# concurrent request to a trivial endpoint by the same 1.5s; the
# identical blocking call in a plain def endpoint (which FastAPI runs in
# its worker threadpool instead) added under 2ms. Plain `def` path
# operations behave identically to `async def` ones in every other way
# (dependency injection, HTTPException, returning a FileResponse/
# StreamingResponse, ...), so this changes nothing about how any of these
# already-synchronous handlers work -- only how FastAPI schedules them.

# Configurable via NOTEBOOK_API_UPLOAD_DIR, matching this exact same
# NOTEBOOK_API_* env-var convention GENERATED_DIR above already
# establishes for its own sibling directory -- rather than being
# permanently fixed to "uploads" with no way for an operator to point the
# dashboard at a different uploads directory (e.g. a mounted persistent
# volume in a container, or to avoid colliding with an "uploads"
# directory something else on the same host already uses). Read once at
# import time, same as GENERATED_DIR: unlike GENERATED_DIR's directory
# (created fresh on every compile_notebook_to_api call, wherever it
# currently points), UPLOAD_DIR's directory is only ever created here,
# eagerly, so this env var must be set before the process starts for it
# to take effect -- setting it afterward, or monkeypatching this module's
# UPLOAD_DIR attribute in-process, changes where uploads are written but
# not this one-time directory creation.
UPLOAD_DIR = os.getenv("NOTEBOOK_API_UPLOAD_DIR", "uploads")
os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
)

# Matches the app's existing NOTEBOOK_API_* env-var convention (see
# allowed_origins() in backend/dashboard.py and TASK_TTL_SECONDS in
# api_generator.py) rather than hardcoding a fixed limit. Without this,
# /api/upload accepted a file of any size onto disk before anything ever
# tried to parse it.
MAX_UPLOAD_BYTES = int(
    os.getenv("NOTEBOOK_API_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))
)
_UPLOAD_CHUNK_BYTES = 1024 * 1024

# Same NOTEBOOK_API_* convention as MAX_UPLOAD_BYTES above, rather than the
# fixed 600s every `docker build`/`docker push` call in /api/deploy
# previously hardcoded -- some deploy environments legitimately need
# longer (a slow/cold image layer cache) or want it clamped shorter (fail
# fast in CI) than a one-size-fits-all default allows.
DEPLOY_SUBPROCESS_TIMEOUT_SECONDS = int(
    os.getenv("NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS", "600")
)

# Same NOTEBOOK_API_* convention as MAX_UPLOAD_BYTES/DEPLOY_SUBPROCESS_
# TIMEOUT_SECONDS above. upload_notebook's own hidden ".<name>.<uuid>.part"
# temp files (see temp_path below) are already cleaned up on every error
# path it can reach on its own -- but a hard process crash or restart
# (OOM kill, a container recreated mid-deploy, ...) between that file
# being created and the request finishing skips every one of those,
# leaving it behind permanently. Nothing else in this codebase ever
# looked at UPLOAD_DIR for a leftover ".part" file again: it doesn't end
# in ".ipynb", so GET /api/notebooks never lists it, and there was no
# admin endpoint or startup sweep to find or remove it either -- on a
# long-running dashboard seeing occasional upload failures (a flaky
# client connection, a repeatedly-retried oversized file, ...), these
# accumulate invisibly and consume disk space forever with no way to
# reclaim it short of an operator manually finding and deleting hidden
# dot-files on the server's filesystem by hand.
STALE_UPLOAD_TEMP_FILE_SECONDS = int(
    os.getenv("NOTEBOOK_API_STALE_UPLOAD_TEMP_FILE_SECONDS", str(60 * 60))
)


def _cleanup_stale_upload_temp_files():
    """Remove any "*.part" temp file directly inside UPLOAD_DIR whose
    last modification is older than STALE_UPLOAD_TEMP_FILE_SECONDS.

    Called opportunistically at the start of every upload (see
    upload_notebook below) rather than via a separate background thread
    or scheduler -- this codebase has no such machinery for the core
    notebook-to-API path, and a periodic sweep would need one just for
    this. Piggybacking on the one code path that already creates these
    files self-heals the leak without adding a new moving part: as long
    as uploads keep happening at all, old orphaned temp files never
    survive longer than STALE_UPLOAD_TEMP_FILE_SECONDS past the next one.

    Age-gated (not "every .part file, always") specifically so this never
    races an upload that is itself still genuinely streaming -- a large,
    slow, or merely in-flight upload's own temp file is younger than the
    threshold and is left alone.

    Best-effort: a temp file that's already gone by the time this tries
    to remove it (e.g. its own upload finished and swapped it into place
    in the meantime) is not an error.
    """
    upload_root = Path(UPLOAD_DIR)

    if not upload_root.is_dir():
        return

    now = datetime.now(timezone.utc).timestamp()

    for entry in upload_root.iterdir():

        if not entry.is_file() or not entry.name.endswith(".part"):
            continue

        try:
            age_seconds = now - entry.stat().st_mtime
        except OSError:
            continue

        if age_seconds > STALE_UPLOAD_TEMP_FILE_SECONDS:
            entry.unlink(missing_ok=True)


def _resolve_path_within(root_dir: str, name: str, dir_label: str) -> Path:
    """Resolve `name` against `root_dir`, rejecting anything that would
    escape it.

    `name` comes straight from client input (an uploaded/generated file's
    name, from a URL path segment or a JSON body field). Both
    os.path.join and pathlib's `/` operator discard the left-hand side
    entirely when the right-hand side is absolute (`Path("uploads") /
    "/etc/passwd" == Path("/etc/passwd")`), and plain `../` segments
    escape just as easily. Without this check, a client-controlled name
    can read or write arbitrary files outside `root_dir`. Shared by
    resolve_upload_path (UPLOAD_DIR) and resolve_generated_path
    (GENERATED_DIR) below, so both stay protected identically instead of
    the check drifting between the two call sites.

    The `isinstance` check below matters on its own, separately from the
    traversal check: `name` reaches resolve_upload_path as a raw JSON
    body field (POST /api/inspect and /api/compile's "notebook_path"),
    not a Pydantic-validated string, so a caller can send *any* JSON
    type there -- a number, a list, a bool. Confirmed exploitable before
    this check existed: `Path(123)` raises a bare TypeError, not
    something any of this project's callers ever caught, so
    `{"notebook_path": 123}` crashed the request with an unhandled 500
    instead of the same clean, actionable 400 a malformed *string* path
    already got.

    The embedded-null-byte check is a separate, later fix, for the same
    reason: `Path(name).is_absolute()` above doesn't raise for a name
    like "nb\x00.ipynb" -- a null byte isn't special to pathlib's own
    parsing -- but the `.resolve()` call further down eventually hands it
    to the underlying os.path.realpath/lstat syscalls, which do reject
    it, as a bare ValueError ("embedded null character in path").
    Confirmed exploitable before this check existed, across every route
    that calls through here (POST /api/inspect, POST /api/compile, GET/
    DELETE/PATCH /api/notebooks/{filename}, GET
    /api/generated/{filename}): a name or new_filename containing "\x00"
    crashed the request with an unhandled 500, the exact same failure
    mode the isinstance check above already closed for a non-string
    "notebook_path" -- just reached through a valid *string* this time,
    so that check alone didn't catch it.
    """

    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or Path(name).is_absolute()
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: must be a relative filename within the {dir_label} directory"
        )

    resolved_root = Path(root_dir).resolve()
    candidate = (resolved_root / name).resolve()

    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: must stay within the {dir_label} directory"
        )

    return candidate


def resolve_upload_path(name: str) -> Path:
    """Resolve `name` against UPLOAD_DIR, rejecting anything that would
    escape it -- and, unlike resolve_generated_path below, anything that
    isn't a single flat filename directly inside it.

    Without the escape check, /upload allows writing arbitrary files
    outside UPLOAD_DIR, and /inspect and /compile allow reading them
    (confirmed: both were exploitable before that check existed).

    The flat-filename check is a separate, later fix: `name` staying
    within UPLOAD_DIR doesn't mean it has no directory component of its
    own -- confirmed exploitable: POST /api/upload with
    file.filename="subdir/nb.ipynb" passed the escape check fine (it
    never leaves UPLOAD_DIR) but crashed with an unhandled
    FileNotFoundError from upload_notebook's own os.replace(temp_path,
    file_path) call, an uncaught 500 instead of the same clean 400 every
    other malformed-input case in this file already gets, since nothing
    ever creates the intermediate "subdir/" directory. Even granting that
    directory existed, every other route that operates on an uploaded
    notebook by name already assumes a flat, single-segment filename --
    list_notebooks only ever walks UPLOAD_DIR's top level
    (Path.iterdir(), not rglob), and get_notebook/delete_notebook's
    "{filename}" route parameter can't be reached with a literal '/' in
    it through normal routing -- so a notebook tucked into a subdirectory
    would have been permanently invisible to every one of them, reachable
    only by whoever remembered the exact nested notebook_path they'd
    typed into /api/compile or /api/inspect's JSON body (which, unlike a
    URL path segment, has no such routing restriction).
    """
    if isinstance(name, str) and name and os.path.basename(name) != name:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid path: must be a single filename within the "
                "uploads directory, not a nested path"
            )
        )

    return _resolve_path_within(UPLOAD_DIR, name, "uploads")


def resolve_generated_path(name: str) -> Path:
    """Resolve `name` against GENERATED_DIR, rejecting anything that
    would escape it -- same protection as resolve_upload_path, applied to
    GET /api/generated/{filename}'s filename, which is exactly as much
    client input as an uploaded file's own name.
    """
    return _resolve_path_within(GENERATED_DIR, name, "generated output")


@router.post("/upload")
async def upload_notebook(
    file: UploadFile = File(...),
    overwrite: bool = False,
):
    """Upload a Jupyter notebook file.

    Streams to a temporary file inside UPLOAD_DIR first and only moves it
    into place -- atomically, via os.replace -- after it passes the same
    size and notebook-validity checks this endpoint already enforced.
    Previously the upload was written straight to its final
    "<filename>.ipynb" path as it streamed in: re-uploading a name that
    already existed overwrote the previous file's bytes immediately, before
    any validation ran, so an oversized or invalid re-upload permanently
    destroyed a previously good notebook with no way to recover it
    (confirmed: uploading garbage over an existing valid notebook silently
    replaced it, then deleted the garbage too on the validation-failure
    cleanup path, losing the original for good). There was also no way to
    even detect a same-name collision was about to happen.

    A same-named notebook is now rejected outright with 409 before the
    upload body is even read, unless the caller passes ?overwrite=true to
    confirm the replacement -- mirroring the explicit opt-in `deploy
    --push` already uses elsewhere in this codebase for another
    action with real, hard-to-undo consequences.
    """

    if not file.filename.endswith(".ipynb"):

        raise HTTPException(
            status_code=400,
            detail="File must be a .ipynb notebook"
        )

    # Opportunistic sweep for temp files a *previous* upload left behind
    # after a hard crash/restart (see STALE_UPLOAD_TEMP_FILE_SECONDS
    # above) -- run before this upload creates its own, so it never
    # touches anything from this request.
    _cleanup_stale_upload_temp_files()

    file_path = resolve_upload_path(file.filename)

    if file_path.exists() and not overwrite:

        raise HTTPException(
            status_code=409,
            detail=(
                f"A notebook named '{file.filename}' already exists. "
                "Pass ?overwrite=true to replace it."
            )
        )

    upload_root = Path(UPLOAD_DIR).resolve()
    # A hidden, uniquely-suffixed name in the same directory as the final
    # destination: hidden so it never shows up in GET /api/notebooks (which
    # only lists ".ipynb" files, and this doesn't end in that suffix), and
    # in the same directory so the final os.replace() below is an atomic
    # rename rather than a cross-filesystem copy.
    temp_path = upload_root / f".{file_path.name}.{uuid.uuid4().hex}.part"

    size = 0

    try:

        with open(temp_path, "wb") as buffer:

            while True:

                chunk = await file.read(_UPLOAD_CHUNK_BYTES)

                if not chunk:
                    break

                size += len(chunk)

                if size > MAX_UPLOAD_BYTES:
                    break

                buffer.write(chunk)

    except Exception as e:

        if temp_path.exists():
            os.remove(temp_path)

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    if size > MAX_UPLOAD_BYTES:

        os.remove(temp_path)

        raise HTTPException(
            status_code=413,
            detail=(
                f"Notebook exceeds the maximum upload size of "
                f"{MAX_UPLOAD_BYTES} bytes"
            )
        )

    try:

        load_notebook(str(temp_path))

    except Exception as e:

        os.remove(temp_path)

        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    # Re-checked immediately before the swap (rather than trusting the
    # early check above) so the "overwritten" flag in the response stays
    # accurate even if a concurrent request created the file while this
    # one's body was still streaming/validating.
    overwritten = file_path.exists()

    if overwritten and not overwrite:

        os.remove(temp_path)

        raise HTTPException(
            status_code=409,
            detail=(
                f"A notebook named '{file.filename}' already exists. "
                "Pass ?overwrite=true to replace it."
            )
        )

    os.replace(temp_path, file_path)

    return {
        "status": "success",
        "filename": file.filename,
        "path": str(file_path),
        "overwritten": overwritten,
    }


def _currently_compiled_notebook_metadata():
    """(resolved path, content sha256, compiled_at) of the notebook that
    produced the app currently in GENERATED_DIR, if any -- read from the
    .compile_metadata.json every successful compile writes (see
    write_compile_metadata in backend/compiler.py). Returns (None, None,
    None) if nothing has been compiled yet, or if the metadata file is
    missing/unreadable/corrupt -- list_notebooks should degrade to
    reporting no notebook as currently compiled rather than 500 over this
    being informational, best-effort metadata.
    """

    metadata_path = Path(GENERATED_DIR) / COMPILE_METADATA_FILENAME

    if not metadata_path.is_file():
        return None, None, None

    try:

        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    except (OSError, ValueError):
        return None, None, None

    source_notebook = metadata.get("source_notebook")

    if not source_notebook:
        return None, None, None

    return (
        Path(source_notebook).resolve(),
        metadata.get("source_notebook_sha256"),
        metadata.get("compiled_at"),
    )


def _currently_compiled_notebook_is_stale():
    """Whether the notebook that produced the app currently in
    GENERATED_DIR has been edited since that compile -- the same
    comparison list_notebooks' notebook_changed_since_compile field
    already makes for the currently-compiled entry, reused here so
    /api/deploy can warn before building (and possibly pushing) a Docker
    image from code that no longer matches its source.

    Returns False (nothing to warn about) if nothing has been compiled
    yet, the metadata is missing/corrupt, or the source notebook file no
    longer exists (e.g. deleted -- see was_currently_compiled on DELETE
    /api/notebooks/{filename}) -- in every one of these cases there's no
    current notebook content left to compare against, so there's nothing
    this check can meaningfully flag either way.
    """

    compiled_path, compiled_sha256, _ = _currently_compiled_notebook_metadata()

    if compiled_path is None or compiled_sha256 is None:
        return False

    if not compiled_path.is_file():
        return False

    return hash_notebook_file(compiled_path) != compiled_sha256


_NOTEBOOK_SORT_KEYS = frozenset({"name", "size", "modified"})
_NOTEBOOK_SORT_ORDERS = frozenset({"asc", "desc"})


@router.get("/notebooks")
def list_notebooks(search: str = None, sort: str = "name", order: str = "asc"):
    """List previously uploaded notebooks.

    /api/upload was previously a one-way door: notebooks could be
    uploaded but never listed or removed again through the API, so a
    dashboard frontend had no way to let a user pick a previously
    uploaded notebook without re-uploading it, and the uploads directory
    could only grow.

    Each entry's "currently_compiled" flag -- added alongside this same
    docstring's original feature, not a separate change -- says whether
    this is the notebook that produced whatever's currently in
    GENERATED_DIR, which nothing here previously exposed at all: a
    dashboard frontend had to track that itself client-side, which is
    fragile (lost on refresh) and wrong the moment a second compile
    happens without it finding out.

    The currently-compiled entry additionally gets
    "notebook_changed_since_compile": even once "this is the
    currently-compiled notebook" was known, there was still no way to
    tell whether it had since been edited and re-uploaded (e.g. via
    /api/upload?overwrite=true) *after* the compile that produced the
    current generated/ output -- silently leaving the served app stale
    relative to a notebook a caller might think it still matches exactly.

    It also gets "compiled_at", the timestamp write_compile_metadata
    recorded when that compile happened -- already written to
    .compile_metadata.json for every compile and already read by this
    endpoint to resolve the fields above, but previously discarded rather
    than returned. Without it, a caller could tell *that* the currently
    running app might be stale (via notebook_changed_since_compile) but
    had no way to tell *how* stale -- e.g. to show "last compiled 3
    minutes ago" -- without a separate, redundant read of the same file.

    "search", "sort", and "order" close a gap that only gets worse as
    UPLOAD_DIR accumulates notebooks over a dashboard's lifetime: before
    this, the response was always every ".ipynb" file in UPLOAD_DIR, in a
    fixed alphabetical-by-filename order, with no way to find one by name
    or see the newest/largest first short of a caller sorting/filtering
    the full list itself on every page load. "search" matches a
    case-insensitive substring of the filename; "sort" is one of "name"
    (the previous, and still default, order), "size", or "modified"; and
    "order" is "asc" (default) or "desc". An invalid "sort"/"order" value
    is rejected with 400 rather than silently falling back to the
    default, the same way an invalid "format" already is elsewhere in
    this file (see POST /api/export-openapi).
    """

    if sort not in _NOTEBOOK_SORT_KEYS:

        raise HTTPException(
            status_code=400,
            detail=f"sort must be one of {sorted(_NOTEBOOK_SORT_KEYS)}"
        )

    if order not in _NOTEBOOK_SORT_ORDERS:

        raise HTTPException(
            status_code=400,
            detail=f"order must be one of {sorted(_NOTEBOOK_SORT_ORDERS)}"
        )

    upload_root = Path(UPLOAD_DIR)

    compiled_path, compiled_sha256, compiled_at = _currently_compiled_notebook_metadata()

    # (name, size_bytes, mtime, entry dict) tuples -- name/size_bytes/mtime
    # kept alongside the dict itself so sorting by "size"/"modified" can use
    # the raw numeric stat values rather than re-deriving them from the
    # dict's own already-formatted "size_bytes"/"modified_at" fields (the
    # latter is an ISO 8601 string, not safely sortable as one: isoformat()
    # only appends a microseconds component when it's non-zero, so two
    # timestamps that differ only in whether they happen to land on a whole
    # second don't compare consistently as plain strings).
    entries = []

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if search and search.lower() not in entry.name.lower():
            continue

        entry_stat = entry.stat()

        is_currently_compiled = (
            compiled_path is not None and entry.resolve() == compiled_path
        )

        notebook_entry = {
            "filename": entry.name,
            "size_bytes": entry_stat.st_size,
            "modified_at": datetime.fromtimestamp(
                entry_stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            "currently_compiled": is_currently_compiled,
        }

        if is_currently_compiled:
            notebook_entry["notebook_changed_since_compile"] = (
                compiled_sha256 is not None
                and hash_notebook_file(entry) != compiled_sha256
            )
            notebook_entry["compiled_at"] = compiled_at

        entries.append((entry.name, entry_stat.st_size, entry_stat.st_mtime, notebook_entry))

    sort_key_index = {"name": 0, "size": 1, "modified": 2}[sort]

    entries.sort(
        key=lambda entry_tuple: entry_tuple[sort_key_index],
        reverse=(order == "desc"),
    )

    notebooks = [entry_tuple[3] for entry_tuple in entries]

    return {
        "status": "success",
        "notebooks": notebooks,
    }


@router.delete("/notebooks")
def delete_all_notebooks(confirm: bool = False):
    """Remove every uploaded notebook in UPLOAD_DIR at once.

    GET /api/notebooks and DELETE /api/generated already form a
    "list what's here" / "clear all of it" pair for GENERATED_DIR, but
    UPLOAD_DIR only ever had the single-file DELETE /api/notebooks/{filename}
    -- no bulk equivalent. An operator wanting to reset the uploads
    directory entirely (e.g. before a demo, to reclaim disk space after
    accumulating scratch notebooks, or to clear out everything before a
    fresh batch of uploads) had to call DELETE /api/notebooks/{filename}
    once per file, discovering each name from a separate GET
    /api/notebooks first -- there was no single call that emptied it.

    Unlike DELETE /api/generated (whose target is reproducible build
    output -- recompiling regenerates it), the notebooks here are the only
    copy of a user's original uploaded source on this server, so this
    requires an explicit "?confirm=true" opt-in before it does anything,
    the same explicit-confirmation pattern /api/upload's own "overwrite"
    and /api/deploy's "force"/"push" already use elsewhere in this file
    for actions with real, hard-to-undo consequences -- a plain DELETE
    /api/notebooks with no query string is rejected with 400 rather than
    silently wiping every uploaded notebook.

    Deliberately leaves GENERATED_DIR completely untouched, the same way
    the single-file DELETE /api/notebooks/{filename} already does: the
    compiled app currently running keeps running exactly as before. The
    response's "currently_compiled_notebook_deleted" flag mirrors that
    endpoint's own "was_currently_compiled" flag for the same reason --
    without it, a caller had no way to know this bulk delete had just
    orphaned whatever's currently compiled, short of a separate GET
    /api/notebooks call beforehand to check every entry's
    "currently_compiled" flag itself.

    Only ever removes ".ipynb" files directly inside UPLOAD_DIR -- the
    same set GET /api/notebooks already lists -- so an in-flight upload's
    own hidden ".part" temp file (see _cleanup_stale_upload_temp_files
    above) is never touched by this.
    """

    if not confirm:

        raise HTTPException(
            status_code=400,
            detail=(
                "This deletes every uploaded notebook. Pass "
                '"?confirm=true" to proceed.'
            )
        )

    upload_root = Path(UPLOAD_DIR)

    compiled_path, _, _ = _currently_compiled_notebook_metadata()

    deleted_filenames = []
    currently_compiled_notebook_deleted = False

    for entry in sorted(upload_root.iterdir()):

        if not entry.is_file() or entry.suffix != ".ipynb":
            continue

        if compiled_path is not None and entry.resolve() == compiled_path:
            currently_compiled_notebook_deleted = True

        os.remove(entry)
        deleted_filenames.append(entry.name)

    return {
        "status": "success",
        "deleted_count": len(deleted_filenames),
        "deleted_filenames": deleted_filenames,
        "currently_compiled_notebook_deleted": currently_compiled_notebook_deleted,
    }


@router.delete("/notebooks/{filename}")
def delete_notebook(filename: str):
    """Delete a previously uploaded notebook.

    Reuses resolve_upload_path for the same traversal protection already
    applied to /inspect and /compile's notebook_path -- a filename here
    comes from the URL path, but is exactly as much client input as a
    JSON body field, and must be rejected the same way if it tries to
    escape UPLOAD_DIR.

    The response's "was_currently_compiled" flag says whether the file
    just deleted was the notebook that produced whatever's still running
    in GENERATED_DIR (see the identical currently_compiled check in
    list_notebooks above). Deleting it doesn't touch the already-compiled
    app -- it keeps running exactly as before -- but silently orphans it:
    there's no longer an uploaded notebook a caller could re-inspect,
    diff, or recompile from to confirm what's currently being served.
    Without this, a caller had no way to know that had just happened
    short of a separate GET /api/notebooks call beforehand to check.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    compiled_path, _, _ = _currently_compiled_notebook_metadata()

    was_currently_compiled = (
        compiled_path is not None and file_path.resolve() == compiled_path
    )

    try:

        os.remove(file_path)

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    return {
        "status": "success",
        "filename": filename,
        "was_currently_compiled": was_currently_compiled,
    }


@router.get("/notebooks/{filename}")
def get_notebook(filename: str):
    """Download a previously uploaded notebook's raw content.

    GET /api/notebooks lists what's been uploaded and DELETE removes it,
    but there was previously no way to retrieve a specific notebook's
    actual content again through the API -- a dashboard frontend could
    show a list of uploaded notebooks but never let a user view or
    re-download one, only re-upload a fresh copy from scratch.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    return FileResponse(
        path=file_path,
        media_type="application/x-ipynb+json",
        filename=filename,
    )


@router.patch("/notebooks/{filename}")
def rename_notebook(filename: str, data: dict):
    """Rename a previously uploaded notebook in place, keeping its bytes
    untouched.

    Before this, the only way to change an uploaded notebook's name was to
    download it, delete it, and re-upload it under the new name -- and
    doing that to the notebook currently backing GENERATED_DIR silently
    broke every "currently_compiled"/staleness check this dashboard makes
    (see _currently_compiled_notebook_metadata above): the delete step
    left .compile_metadata.json's "source_notebook" pointing at a path
    that no longer existed, so GET /api/notebooks would report
    "currently_compiled": false for every uploaded notebook afterward,
    with none of them any longer identifiable as the one that actually
    produced what's still running in GENERATED_DIR.

    Renaming in place (os.replace, same directory) avoids that entirely:
    it's the same file, so its content hash never changes, and -- if it
    *was* the currently-compiled notebook --
    update_compile_metadata_source_notebook (backend/compiler.py) keeps
    .compile_metadata.json's "source_notebook" pointing at wherever it now
    lives, so "currently_compiled" keeps tracking it correctly under its
    new name instead of going dark.

    Same explicit-overwrite opt-in as /api/upload's own reupload
    collision: renaming onto an existing filename is rejected with 409
    unless the caller passes "overwrite": true.
    """

    new_filename = data.get("new_filename")

    if not isinstance(new_filename, str) or not new_filename:

        raise HTTPException(
            status_code=400,
            detail="new_filename is required"
        )

    if not new_filename.endswith(".ipynb"):

        raise HTTPException(
            status_code=400,
            detail="new_filename must be a .ipynb notebook"
        )

    overwrite = bool(data.get("overwrite", False))

    old_path = resolve_upload_path(filename)

    if not old_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    new_path = resolve_upload_path(new_filename)

    if new_path == old_path:

        # Renaming a notebook to its own current name is a no-op --
        # nothing moved, so there's nothing for the metadata update below
        # to do either, and it must not be rejected by the collision check
        # below (the destination "already exists" precisely because it's
        # the same file).
        return {
            "status": "success",
            "filename": filename,
            "new_filename": new_filename,
            "was_currently_compiled": False,
        }

    if new_path.exists() and not overwrite:

        raise HTTPException(
            status_code=409,
            detail=(
                f"A notebook named '{new_filename}' already exists. "
                'Pass "overwrite": true to replace it.'
            )
        )

    compiled_path, _, _ = _currently_compiled_notebook_metadata()

    was_currently_compiled = (
        compiled_path is not None and old_path.resolve() == compiled_path
    )

    try:

        os.replace(old_path, new_path)

    except OSError as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    if was_currently_compiled:

        # Held for the same reason every other read/write of
        # .compile_metadata.json already does (see COMPILE_LOCK in
        # backend/compiler.py): without it, a concurrent POST /api/compile
        # racing this rename could write a fresh .compile_metadata.json
        # for an unrelated notebook and have this call immediately
        # clobber it with the just-renamed path instead.
        with COMPILE_LOCK:

            update_compile_metadata_source_notebook(
                GENERATED_DIR, str(new_path.resolve())
            )

    return {
        "status": "success",
        "filename": filename,
        "new_filename": new_filename,
        "was_currently_compiled": was_currently_compiled,
    }


@router.post("/inspect")
def inspect_notebook_endpoint(
    data: dict
):
    """Inspect notebook and return extracted functions, dependencies, and
    any files already produced by a prior compile.

    Delegates to inspect_notebook_data (backend/inspector.py), which was
    written specifically to back a JSON endpoint like this one -- its
    docstring literally says "Perfect for FastAPI endpoints and frontend
    dashboards" -- but was never actually wired to any route. This
    endpoint previously duplicated a subset of that same parsing logic
    inline and only ever returned "functions", so callers had no way to
    get a notebook's third-party dependencies or the compiled output file
    list from the API at all.
    """

    notebook_path = data.get(
        "notebook_path"
    )

    if not notebook_path:

        raise HTTPException(
            status_code=400,
            detail="notebook_path is required"
        )

    full_path = resolve_upload_path(notebook_path)

    # .is_file(), not the previous .exists() -- confirmed exploitable:
    # .exists() is also true for a directory, and UPLOAD_DIR itself is a
    # valid, in-bounds resolution target for notebook_path (e.g. "."
    # resolves right back to it via resolve_upload_path). The
    # load_notebook call just below raises IsADirectoryError (an OSError
    # subclass) for one, which isn't in MALFORMED_NOTEBOOK_ERRORS -- so it
    # propagated completely unhandled, past both try blocks in this
    # function, into FastAPI's generic, detail-free 500. Every other
    # route in this file that resolves a client-supplied path to a file
    # it's about to read already checks .is_file() before doing so
    # (get_notebook, delete_notebook, rename_notebook,
    # get_generated_file) -- this endpoint (and POST /api/compile, just
    # below) were the two exceptions.
    if not full_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    try:

        load_notebook(str(full_path))

    except MALFORMED_NOTEBOOK_ERRORS as e:

        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    try:

        # inspect_notebook_data's "generated_files" field walks
        # GENERATED_DIR (see _list_generated_files in backend/inspector.py)
        # -- every other route that reads GENERATED_DIR's compiled output
        # already holds COMPILE_LOCK while doing so (POST
        # /api/export-openapi, POST /api/export-sdk, POST /api/deploy,
        # GET /api/download, GET /api/generated/{filename} -- see
        # COMPILE_LOCK in backend/compiler.py), but this endpoint held it
        # nowhere. Without it, a concurrent POST /api/compile racing this
        # on another thread runs clear_stale_export_artifacts as part of
        # every recompile, which rmtree's the sdk/ subdirectory --
        # os.walk (inside _list_generated_files) can raise
        # FileNotFoundError if that subdirectory is removed out from
        # under it mid-walk, an avoidable 500 for what both call this
        # endpoint's own "preview what compiling will do" step.
        with COMPILE_LOCK:

            inspection = inspect_notebook_data(
                str(full_path),
                GENERATED_DIR
            )

        return {
            "status": "success",
            **inspection
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Inspection error: {str(e)}"
        )


@router.post("/compile")
def compile_notebook_endpoint(
    data: dict
):
    """Compile notebook to API."""

    notebook_path = data.get(
        "notebook_path"
    )

    if not notebook_path:

        raise HTTPException(
            status_code=400,
            detail="notebook_path is required"
        )

    full_path = resolve_upload_path(notebook_path)

    # .is_file(), not .exists() -- see the identical fix and its docstring
    # on POST /api/inspect's own resolve_upload_path check above. This
    # endpoint doesn't crash unhandled the way that one did (the
    # IsADirectoryError load_notebook raises for a directory lands inside
    # this function's own broad `except Exception` below), but it still
    # surfaced as an unhelpful `500 {"detail": "Compilation error: [Errno
    # 21] Is a directory: ..."}` instead of the same clean 404 a missing
    # or otherwise-invalid notebook_path already gets.
    if not full_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    try:

        # A cheap up-front validity check (mirroring the identical
        # load_notebook call every other route in this file makes before
        # doing anything else) so a malformed notebook is reported as the
        # MALFORMED_NOTEBOOK_ERRORS 400 below, rather than surfacing from
        # deep inside compile_notebook as an unrelated-looking failure.
        load_notebook(
            str(full_path)
        )

        compile_notebook(
            str(full_path),
            GENERATED_DIR
        )

        # inspect_notebook_data (backend/inspector.py) already computes
        # everything this response needs -- functions, endpoints,
        # skipped_functions, dependencies, generated_files -- and is
        # exactly what the CLI's `compile --json` already returns for the
        # identical operation (see _dispatch_core_command in cli.py).
        # Before this, this endpoint hand-rolled its own, separate
        # extraction of functions/endpoints/skipped_functions instead of
        # reusing it, which meant its response was a strict *subset* of
        # what `compile --json` gives for the same compile: no
        # "dependencies" (what actually got pinned into requirements.txt,
        # and from there the Docker image `deploy`/`docker build` would
        # ship) and no "generated_files" (what a caller could go fetch via
        # GET /api/download or /api/generated/{filename}) -- a dashboard
        # frontend showing "here's what your notebook compiled into" had
        # no way to answer either question without a separate, redundant
        # POST /api/inspect call right after.
        #
        # compile_notebook (just above) only holds COMPILE_LOCK for its
        # own write phase, releasing it before returning -- so this read
        # needs its own lock, the same way POST /api/inspect's identical
        # call into inspect_notebook_data now does. Without it, a
        # concurrent POST /api/compile for a *different* notebook racing
        # in this exact window runs clear_stale_export_artifacts as part
        # of its own recompile, which rmtree's the sdk/ subdirectory --
        # the os.walk inside inspect_notebook_data's "generated_files"
        # field (_list_generated_files) can raise FileNotFoundError if
        # that subdirectory disappears out from under it mid-walk.
        with COMPILE_LOCK:

            data = inspect_notebook_data(
                str(full_path),
                GENERATED_DIR
            )

        return {
            "status": "success",
            "notebook": notebook_path,
            "functions": data["functions"],
            "endpoints": data["endpoints"],
            "skipped_functions": data["skipped_functions"],
            "dependencies": data["dependencies"],
            "generated_files": data["generated_files"],
            "message": "Notebook compiled successfully"
        }

    except ReservedFunctionNameError as e:

        # The notebook itself is the problem (a function name collides
        # with an identifier the generated app defines -- see
        # RESERVED_INFRASTRUCTURE_NAMES in generator/api_generator.py),
        # not this server, so this is a 400 the caller can act on by
        # renaming the function and recompiling -- not a 500, which
        # previously made this look like a server-side bug and would
        # misfire any 5xx-based alerting watching this endpoint.
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except MALFORMED_NOTEBOOK_ERRORS as e:

        # load_notebook (the first thing this try block does) is the
        # only thing above that can raise these -- the notebook itself is
        # malformed (invalid JSON, or valid JSON missing required
        # notebook keys), not this server, so this is a 400 the caller
        # can act on the same way ReservedFunctionNameError's 400 already
        # is, not a 500.
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Compilation error: {str(e)}"
        )


def _evict_compiled_app_from_module_cache(package_name):
    """Remove `package_name` and every submodule already imported from it
    (e.g. "<package_name>.app", "<package_name>.runtime.notebook_module")
    from sys.modules, so the next import re-reads whatever is currently on
    disk instead of returning what got cached from an earlier import in
    this same long-running dashboard process.

    export_openapi_schema (backend/exporters/openapi_exporter.py) imports
    "<package_name>.app" with plain importlib.import_module, which Python
    resolves from sys.modules -- not from disk -- the moment that name has
    already been imported once. Without this, the second /api/compile ->
    /api/export-openapi round trip in the same process silently exported
    the *previous* compile's schema: confirmed by compiling a notebook
    exposing `add`, exporting it, then recompiling to expose `multiply`
    instead and exporting again -- the second export still returned `add`
    in its paths, with the freshly-written app.py on disk never actually
    read. Not applicable to the CLI's own export-openapi/export-sdk
    commands -- each CLI invocation is a fresh Python process with an
    empty sys.modules, so this staleness can only happen here.
    """
    prefix = f"{package_name}."

    for name in list(sys.modules):

        if name == package_name or name.startswith(prefix):
            del sys.modules[name]


@router.post("/export-openapi")
def export_openapi_endpoint(
    data: dict = None
):
    """Export the OpenAPI schema for the most recently compiled app and
    return it inline, so the dashboard frontend can show/download it
    without shelling out to the `export-openapi` CLI command."""

    from backend.exporters.openapi_exporter import export_openapi_schema

    data = data or {}

    export_format = data.get("format", "json")

    if export_format not in ("json", "yaml"):

        raise HTTPException(
            status_code=400,
            detail="format must be 'json' or 'yaml'"
        )

    output_path = os.path.join(
        GENERATED_DIR,
        f"openapi.{export_format}"
    )

    try:

        # Held for the same reason POST /api/compile's writes hold it
        # (see COMPILE_LOCK in backend/compiler.py): without it, this
        # could import "<package_name>.app" mid-write from a concurrent
        # compile racing it on another thread, reading a torn mix of the
        # old and new compiled output instead of a consistent one.
        #
        # Extended to cover the read-back below too, not just the write --
        # confirmed exploitable: releasing the lock right after
        # export_openapi_schema writes output_path and only then reading
        # it back left a window where a concurrent POST /api/compile
        # racing in could run clear_stale_export_artifacts as part of its
        # own recompile, which unlinks openapi.json/.yaml unconditionally.
        # Reproduced directly against clear_stale_export_artifacts: a file
        # written successfully one moment raised a bare FileNotFoundError
        # on the very next read, immediately after -- this endpoint's own
        # write succeeding gave no guarantee its own read-back, a few
        # lines later, still would.
        with COMPILE_LOCK:

            package_name = package_name_for_output_dir(GENERATED_DIR)

            _evict_compiled_app_from_module_cache(package_name)

            export_openapi_schema(
                output_path,
                package_name,
                format=export_format
            )

            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()

    except ModuleNotFoundError:

        raise HTTPException(
            status_code=404,
            detail="No compiled app found. Run /api/compile first."
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"OpenAPI export error: {str(e)}"
        )

    response = {
        "status": "success",
        "format": export_format,
        "path": output_path,
    }

    if export_format == "json":
        response["schema"] = json.loads(content)
    else:
        response["content"] = content

    return response


@router.post("/export-sdk")
def export_sdk_endpoint(
    data: dict = None
):
    """Generate an SDK client from the exported OpenAPI schema and return
    its source inline, so the dashboard frontend can show/download it
    without shelling out to the `export-sdk` CLI command."""

    from backend.exporters.sdk_generator import (
        generate_python_sdk,
        generate_typescript_sdk,
    )

    data = data or {}

    language = data.get("language", "python")

    if language not in ("python", "typescript"):

        raise HTTPException(
            status_code=400,
            detail="language must be 'python' or 'typescript'"
        )

    openapi_json_path = os.path.join(GENERATED_DIR, "openapi.json")
    openapi_yaml_path = os.path.join(GENERATED_DIR, "openapi.yaml")

    # Confirmed exploitable before this fix: this only ever checked for
    # "openapi.json", hardcoded, even though POST /api/export-openapi
    # (just above) can just as validly write "openapi.yaml" instead, via
    # {"format": "yaml"}. A caller who exported yaml and then called this
    # endpoint got a 404 saying "Run /api/export-openapi first" -- wrong,
    # they already had, in the only other format this same API offers --
    # instead of ever reaching _load_openapi_schema
    # (exporters/sdk_generator.py), whose ValueError message was written
    # specifically to explain this exact situation ("This looks like a
    # YAML export ... export-sdk only reads JSON schemas; re-export with
    # --format json first") but could only ever fire for the CLI's
    # `export-sdk --openapi <path>`, never for this endpoint, since this
    # 404 short-circuited before that check ever ran.
    #
    # Falls back to the yaml file specifically so that hint actually
    # reaches an API caller too -- generate_python_sdk/
    # generate_typescript_sdk below will still refuse to read it (SDK
    # generation needs the JSON schema), but now via the same clear,
    # actionable 400 the CLI already gets, not a misleading 404 that says
    # the opposite of what actually happened.
    if language == "typescript":
        output_path = os.path.join(GENERATED_DIR, "sdk", "typescript_client.ts")
    else:
        output_path = os.path.join(GENERATED_DIR, "sdk", "python_client.py")

    # Held across the existence check, the read, and the generated SDK's
    # own write -- every other route that touches GENERATED_DIR's
    # compiled output already holds this (POST /api/export-openapi's own
    # write, POST /api/deploy's build, GET /api/download's zip, GET
    # /api/generated/{filename}'s read -- see COMPILE_LOCK in
    # backend/compiler.py), but this endpoint held it nowhere at all.
    # Without it, a concurrent POST /api/compile racing this on another
    # thread can run clear_stale_export_artifacts (backend/compiler.py)
    # mid-read -- it unlinks openapi.json/.yaml and rmtree's the sdk/
    # directory as part of every recompile -- so this could read a
    # half-deleted openapi export (a bare FileNotFoundError where the
    # existence check above just said the file was there) or write its
    # generated client into a sdk/ directory a concurrent recompile is
    # simultaneously removing out from under it.
    with COMPILE_LOCK:

        if os.path.isfile(openapi_json_path):
            openapi_path = openapi_json_path
        elif os.path.isfile(openapi_yaml_path):
            openapi_path = openapi_yaml_path
        else:

            raise HTTPException(
                status_code=404,
                detail="No exported OpenAPI schema found. Run /api/export-openapi first."
            )

        try:

            if language == "typescript":
                generate_typescript_sdk(openapi_path, output_path)
            else:
                generate_python_sdk(openapi_path, output_path)

        except ValueError as e:

            # _load_openapi_schema (exporters/sdk_generator.py) raises this
            # specifically when openapi_path's content isn't valid JSON --
            # the schema itself is the problem (most commonly: a yaml-only
            # export, per the fallback above, or a JSON file corrupted/
            # truncated by a concurrent write), not this server, so this is a
            # 400 the caller can act on (re-run /api/export-openapi with
            # format=json), the same distinction ReservedFunctionNameError
            # and MALFORMED_NOTEBOOK_ERRORS already get elsewhere in this file
            # instead of a 500 that looks like a server-side bug.
            raise HTTPException(
                status_code=400,
                detail=f"SDK generation error: {str(e)}"
            )

        except Exception as e:

            raise HTTPException(
                status_code=500,
                detail=f"SDK generation error: {str(e)}"
            )

        with open(output_path, "r", encoding="utf-8") as f:
            code = f.read()

    return {
        "status": "success",
        "language": language,
        "path": output_path,
        "code": code,
    }


def _run_docker_command(args, cwd):
    """Run a `docker ...` subprocess for /api/deploy, translating the two
    ways it can fail to even complete into a clean HTTPException instead
    of an uncaught exception surfacing as FastAPI's generic 500.

    Before this, only the `docker build` call was wrapped at all, and only
    for FileNotFoundError -- `docker push` had no handling whatsoever (a
    missing Docker CLI between a successful build and the push step
    crashed the request), and neither call handled
    subprocess.TimeoutExpired, so a build or push that ran past
    DEPLOY_SUBPROCESS_TIMEOUT_SECONDS also crashed the request instead of
    returning an actionable error.
    """
    try:

        return subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=DEPLOY_SUBPROCESS_TIMEOUT_SECONDS,
        )

    except FileNotFoundError:

        raise HTTPException(
            status_code=500,
            detail="Docker CLI not found on the server. Install Docker to use /api/deploy."
        )

    except subprocess.TimeoutExpired:

        raise HTTPException(
            status_code=504,
            detail=(
                f"`{' '.join(args)}` did not finish within "
                f"{DEPLOY_SUBPROCESS_TIMEOUT_SECONDS} seconds. Set "
                "NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS to allow more time."
            )
        )


@router.post("/deploy")
def deploy_generated_app(data: dict = None):
    """Build (and optionally push) a Docker image from the compiled app.

    The CLI's `deploy` command already does this (compile + `docker
    build`, optionally `docker push` -- see the deploy --push feature),
    but the dashboard REST API had no equivalent: a user could compile a
    notebook through the dashboard but had to drop back to a CLI/shell on
    the server to actually build or push a deployable image.

    Operates on the fixed `generated` directory like /api/export-openapi,
    /api/export-sdk, and /api/download already do, rather than accepting
    a client-supplied directory or Dockerfile to build.

    Unlike the CLI's `deploy` command -- which always recompiles from the
    given notebook path as its own first step, so it can never build a
    stale image -- this endpoint deliberately builds whatever is already
    sitting in GENERATED_DIR from an earlier, separate /api/compile call.
    If the source notebook has since been edited (e.g. re-uploaded via
    /api/upload?overwrite=true) without a matching recompile, that gap
    previously went completely unchecked: this could silently build (and,
    with "push": true, publish) a Docker image reflecting outdated code,
    the exact staleness list_notebooks' notebook_changed_since_compile
    field already exists to warn about elsewhere -- just never enforced
    at the one place actually shipping that stale build somewhere. Pass
    "force": true to deploy the stale build anyway (e.g. deliberately
    re-deploying a known-good previous compile).
    """

    generated_path = Path(GENERATED_DIR)

    data = data or {}

    force = bool(data.get("force", False))

    tag = data.get("tag")

    # tag flows straight into a `docker build`/`docker push` subprocess
    # argument list below -- subprocess.run requires every element to be
    # str/bytes/PathLike, so a non-string "tag" (a number, a list, ...)
    # crashed with an unhandled TypeError from deep inside subprocess
    # internals before this check existed, instead of the same clean 400
    # a bad "force"/"push" already can't produce (bool() never raises).
    if tag is not None and not isinstance(tag, str):
        raise HTTPException(
            status_code=400,
            detail="tag must be a string"
        )

    tag = tag or f"{generated_path.name.lower()}:latest"
    push = bool(data.get("push", False))

    platform = data.get("platform")

    # Same reasoning as the "tag" check above: platform flows straight
    # into the `docker build` subprocess argument list below, so a
    # non-string value would otherwise crash with an unhandled TypeError
    # instead of a clean 400.
    if platform is not None and not isinstance(platform, str):
        raise HTTPException(
            status_code=400,
            detail="platform must be a string"
        )

    # `docker build`'s own default target platform is whatever the local
    # Docker daemon's host architecture is -- correct for a plain `docker
    # run` on that same machine, but not for the common case of building
    # on one architecture (e.g. Apple Silicon) for a deploy target that
    # runs another, which almost every cloud PaaS does (linux/amd64).
    # Without this, the dashboard's /api/deploy had no way to override it
    # at all, unlike the CLI's own `deploy --platform` (added alongside
    # this same feature).
    build_args = ["docker", "build", "-t", tag]
    if platform:
        build_args += ["--platform", platform]
    build_args.append(".")

    # Held from the staleness check through the build itself (see
    # COMPILE_LOCK in backend/compiler.py): `docker build`'s context is
    # every file `generated_path` contains at the moment it reads them,
    # so a concurrent POST /api/compile rewriting that same directory
    # mid-build could ship an image built from a torn mix of the old and
    # new compile. Released before `docker push`, which only pushes the
    # already-built local image by tag and no longer reads the
    # directory, so it doesn't need to block a compile that arrives
    # while the push itself is still in flight.
    with COMPILE_LOCK:

        if not (generated_path / "Dockerfile").is_file():

            raise HTTPException(
                status_code=404,
                detail="No compiled app found. Run /api/compile first."
            )

        if not force and _currently_compiled_notebook_is_stale():

            raise HTTPException(
                status_code=409,
                detail=(
                    "The currently-compiled app no longer matches its source "
                    "notebook's current content -- it was edited since the "
                    "last compile. Run /api/compile again first, or pass "
                    '"force": true to deploy the stale build anyway.'
                )
            )

        build_result = _run_docker_command(
            build_args, generated_path
        )

        if build_result.returncode != 0:

            raise HTTPException(
                status_code=500,
                detail=f"Docker build failed: {build_result.stderr}"
            )

    response = {
        "status": "success",
        "tag": tag,
        "pushed": False,
    }

    if push:

        push_result = _run_docker_command(
            ["docker", "push", tag], generated_path
        )

        if push_result.returncode != 0:

            raise HTTPException(
                status_code=500,
                detail=f"Docker push failed: {push_result.stderr}"
            )

        response["pushed"] = True

    return response


@router.get("/download")
def download_generated_app():
    """Download the compiled app as a zip archive.

    /api/compile only ever returned metadata (the function list and
    endpoint names) -- it never gave the dashboard frontend any way to
    retrieve the actual compiled artifacts (app.py, requirements.txt,
    Dockerfile, .dockerignore, the runtime module) short of CLI or
    filesystem access to the server. This packages the whole `generated`
    output directory as a single zip so the frontend can offer a direct
    download after compiling.

    Excludes EXCLUDED_GENERATED_DIR_NAMES subtrees (currently just
    __pycache__) the same way inspect_notebook_data's "generated_files"
    field does -- __pycache__ is created by Python itself the first time
    the compiled app or its runtime module gets imported (e.g. by a prior
    /api/export-openapi call, which does exactly that), not by the
    compiler, and its .pyc filenames are tied to whichever Python version
    happened to import it. Left unfiltered, a downloaded "compiled app"
    bundle could ship a stale, non-portable bytecode cache alongside the
    actual deliverable (app.py, requirements.txt, Dockerfile, ...).

    Also excludes EXCLUDED_GENERATED_FILE_NAMES (currently just
    .compile_metadata.json) for the same reason: it's dashboard-internal
    bookkeeping, not a compiled deliverable, and its "source_notebook"
    field is the source notebook's absolute filesystem path on the
    compiling server -- not something a "download the compiled app" caller
    has any business receiving.
    """

    generated_path = Path(GENERATED_DIR)

    # Held while reading generated_path (see COMPILE_LOCK in
    # backend/compiler.py) so a concurrent POST /api/compile can't
    # rewrite it mid-walk, which could zip up a torn mix of files from
    # the old and new compile instead of one consistent output.
    with COMPILE_LOCK:

        if not (generated_path / "app.py").is_file():

            raise HTTPException(
                status_code=404,
                detail="No compiled app found. Run /api/compile first."
            )

        buffer = io.BytesIO()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

            for file_path in sorted(generated_path.rglob("*")):

                if (
                    file_path.is_file()
                    and file_path.name not in EXCLUDED_GENERATED_FILE_NAMES
                    and not (
                        EXCLUDED_GENERATED_DIR_NAMES & set(file_path.relative_to(generated_path).parts)
                    )
                ):

                    archive.write(
                        file_path,
                        file_path.relative_to(generated_path)
                    )

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{generated_path.name}.zip"'
            )
        }
    )


@router.get("/generated")
def list_generated_files_endpoint():
    """List the files currently sitting in GENERATED_DIR, without requiring
    a notebook_path -- unlike POST /api/inspect, which can also list this
    same generated_files set, but only alongside a full inspection of a
    notebook that must still exist in UPLOAD_DIR.

    GET /api/notebooks already reports "currently_compiled" and
    "compiled_at" for whichever *uploaded* notebook produced GENERATED_DIR's
    current contents -- but DELETE /api/notebooks/{filename} (its own
    "was_currently_compiled" flag documents this) doesn't touch
    GENERATED_DIR at all when that notebook is removed: the compiled app
    keeps running exactly as before, just with no uploaded notebook left to
    ask /api/inspect about. Previously, that left no way to even list
    what's still in GENERATED_DIR short of GET /api/download's zip (opaque
    bytes, not a listing) or already knowing an exact filename to pass GET
    /api/generated/{filename}. A dashboard frontend showing "here's what's
    currently deployed" after a page refresh -- with the notebook that
    produced it possibly long since deleted -- had no endpoint to ask for
    that.

    "source_notebook_filename" is the currently-compiled notebook's name
    relative to UPLOAD_DIR, for a direct GET /api/notebooks/{filename}
    follow-up -- or null if nothing has been compiled yet, or if whatever
    was compiled came from outside UPLOAD_DIR entirely (e.g. compiled by
    the CLI directly against an arbitrary path, not through /api/upload).
    "source_notebook_exists" says whether that notebook still exists on
    disk right now -- distinct from and independent of
    "source_notebook_filename", since a notebook can be deleted (this goes
    false) without GENERATED_DIR's own contents changing at all.

    "file_details" carries the same filenames as "generated_files" (kept
    for backward compatibility -- and because inspect_notebook_data's own
    "generated_files" field, shared by POST /api/inspect and POST
    /api/compile, is a plain list of names too, and this stays consistent
    with it), each paired with its "size_bytes" and "modified_at" --
    exactly the level of detail GET /api/notebooks already reports per
    uploaded notebook (see list_notebooks above), which this endpoint's
    own "generated_files" never had. Before this, a dashboard frontend
    wanting to show "here's what's compiled" as a real file browser (file
    sizes, most-recently-touched-first, ...) had to issue a separate GET
    /api/generated/{filename} call per file just to learn how big each one
    is -- N+1 requests for what "list what's in GENERATED_DIR" should
    answer in one.
    """

    with COMPILE_LOCK:

        generated_files = list_generated_files(GENERATED_DIR)

        # Stat'd under the same COMPILE_LOCK hold as the listing above --
        # not a separate, later acquisition -- so a concurrent POST
        # /api/compile racing in on another thread can't remove or replace
        # one of these files (see clear_stale_export_artifacts, backend/
        # compiler.py) in the gap between listing it and stat'ing it,
        # which would otherwise raise an avoidable FileNotFoundError for
        # what's supposed to be this endpoint's own safe, read-only
        # listing step.
        generated_file_details = []

        for relative_name in generated_files:

            file_stat = (Path(GENERATED_DIR) / relative_name).stat()

            generated_file_details.append({
                "filename": relative_name,
                "size_bytes": file_stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    file_stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })

        compiled_path, _, compiled_at = _currently_compiled_notebook_metadata()

    source_notebook_filename = None
    source_notebook_exists = False

    if compiled_path is not None:

        source_notebook_exists = compiled_path.is_file()

        try:
            source_notebook_filename = str(
                compiled_path.relative_to(Path(UPLOAD_DIR).resolve())
            )
        except ValueError:
            source_notebook_filename = None

    return {
        "status": "success",
        "generated_files": generated_files,
        "file_details": generated_file_details,
        "compiled_at": compiled_at,
        "source_notebook_filename": source_notebook_filename,
        "source_notebook_exists": source_notebook_exists,
    }


@router.delete("/generated")
def delete_generated_app():
    """Remove GENERATED_DIR and everything compiled into it, resetting the
    dashboard's compiled-app state back to "nothing compiled yet".

    Every write path in this codebase already handles a *stale* compiled
    output (clear_stale_export_artifacts in backend/compiler.py replaces
    the previous notebook's app.py/exports on every recompile), but there
    was previously no way to remove one outright: the only ways to make
    GENERATED_DIR empty again were to delete it by hand on the server's
    filesystem, or to recompile some other notebook over it, which still
    leaves *a* compiled app sitting there -- just a different one. An
    operator who has deleted the notebook that produced the current
    compile (DELETE /api/notebooks/{filename}'s "was_currently_compiled"
    flag, or GET /api/generated's "source_notebook_exists": false, already
    surface this orphaned state) and wants to actually reclaim the disk
    space or reset the dashboard to a clean slate had no endpoint to call
    for it.

    Mirrors GET /api/download's own "is there anything to act on"
    check (app.py must exist) rather than a bare directory existence
    check, and the same 404 message /api/deploy and /api/download already
    use for "nothing compiled yet" -- so a caller can't tell this apart
    from those by response shape alone.

    Also evicts the compiled app from sys.modules the same way POST
    /api/export-openapi already does before every import of it (see
    _evict_compiled_app_from_module_cache above): once GENERATED_DIR is
    gone, a cached import of it in a long-running dashboard process no
    longer corresponds to anything on disk at all, not just a stale
    version of it.
    """

    generated_path = Path(GENERATED_DIR)

    # Held for the same reason every other route that touches
    # GENERATED_DIR's compiled output already does (see COMPILE_LOCK in
    # backend/compiler.py): without it, a concurrent POST /api/compile
    # racing this on another thread could be left writing into a
    # directory this request is simultaneously deleting out from under
    # it, or this could delete a directory a concurrent compile just
    # finished writing to.
    with COMPILE_LOCK:

        if not (generated_path / "app.py").is_file():

            raise HTTPException(
                status_code=404,
                detail="No compiled app found. Run /api/compile first."
            )

        try:
            package_name = package_name_for_output_dir(GENERATED_DIR)
        except ValueError:
            # GENERATED_DIR's basename isn't a valid Python package name
            # (e.g. NOTEBOOK_API_GENERATED_DIR was reconfigured to
            # something unusual after the compile that produced this
            # directory) -- nothing could have been imported under an
            # invalid name in the first place, so there's nothing to
            # evict from sys.modules.
            pass
        else:
            _evict_compiled_app_from_module_cache(package_name)

        shutil.rmtree(generated_path)

    return {
        "status": "success",
        "generated_dir": str(generated_path),
    }


@router.get("/generated/{filename:path}")
def get_generated_file(filename: str):
    """Preview a single compiled output file's raw text content by name
    (e.g. "app.py", "requirements.txt", "Dockerfile", or
    "runtime/notebook_module.py").

    GET /api/download already lets a caller retrieve the whole compiled
    output as a zip, and inspect_notebook_data's "generated_files" field
    already lists what's in it by name -- but there was previously no way
    to actually read any *one* of those files' content through the API: a
    dashboard frontend wanting to show "here's the app.py you're about to
    deploy" (or requirements.txt, or the Dockerfile) had no choice but to
    download and unzip the entire bundle client-side just to display a
    single file, or shell out to the server's filesystem directly.

    Reuses resolve_generated_path for the same traversal protection
    resolve_upload_path already applies to UPLOAD_DIR -- `filename` here
    comes straight from the URL path, exactly as much client input as an
    uploaded file's own name. `{filename:path}` (not the plain
    `{filename}` GET /api/notebooks/{filename} uses) so a nested path like
    "runtime/notebook_module.py" is accepted as a single path parameter
    instead of only ever matching a single path segment.
    """

    file_path = resolve_generated_path(filename)

    generated_root = Path(GENERATED_DIR).resolve()

    # Same __pycache__ exclusion inspect_notebook_data's "generated_files"
    # field and GET /api/download already apply (see
    # EXCLUDED_GENERATED_DIR_NAMES) -- it's a Python-created, non-portable
    # implementation artifact never actually written by the compiler, not
    # a real deliverable this endpoint should ever serve back.
    #
    # .compile_metadata.json (EXCLUDED_GENERATED_FILE_NAMES) is excluded
    # for a sharper reason: it's dashboard-internal bookkeeping whose
    # "source_notebook" field is the source notebook's absolute filesystem
    # path on the compiling server -- this endpoint has no business handing
    # server-side filesystem layout back to a caller who just asked to
    # preview a compiled output file.
    if (
        file_path.name in EXCLUDED_GENERATED_FILE_NAMES
        or EXCLUDED_GENERATED_DIR_NAMES
        & set(file_path.relative_to(generated_root).parts)
    ):
        raise HTTPException(
            status_code=404,
            detail="Generated file not found"
        )

    # Held while reading (see COMPILE_LOCK in backend/compiler.py) so a
    # concurrent POST /api/compile can't rewrite this exact file out from
    # under a read in progress, which could otherwise return a torn mix
    # of the old and new compile's bytes instead of one consistent file.
    with COMPILE_LOCK:

        if not file_path.is_file():

            raise HTTPException(
                status_code=404,
                detail="Generated file not found. Run /api/compile first."
            )

        try:

            content = file_path.read_text(encoding="utf-8")

        except UnicodeDecodeError:

            raise HTTPException(
                status_code=415,
                detail=f"'{filename}' is not a text file"
            )

    return {
        "status": "success",
        "filename": filename,
        "content": content,
    }


@router.get("/health")
def health_check():
    """Liveness/readiness probe for the dashboard API.

    Before this, GET /api/health returned the exact same static
    {"status": "healthy", ...} body whether or not a notebook had ever
    been compiled -- a load balancer or Kubernetes readinessProbe pointed
    at it could only ever confirm the dashboard process itself was up,
    never that it actually had a compiled app ready to serve traffic for
    (e.g. right after a fresh deploy, before the first POST /api/compile
    has run). "compiled_app_present" and "compiled_at" close that gap,
    reusing the exact same .compile_metadata.json read list_notebooks
    already does for its own "compiled_at" field, so this never drifts
    from what that endpoint already reports.

    Deliberately omits .compile_metadata.json's "source_notebook" field
    (an absolute filesystem path on the compiling server) -- the same
    field EXCLUDED_GENERATED_FILE_NAMES already keeps out of GET
    /api/download, GET /api/generated/{filename}, and the generated
    Docker image, for the same reason: a health probe has no business
    leaking server-side filesystem layout to whatever's polling it.
    """

    _, _, compiled_at = _currently_compiled_notebook_metadata()

    compiled_app_present = (Path(GENERATED_DIR) / "app.py").is_file()

    return {
        "status": "healthy",
        "service": "notebook-to-api",
        "compiled_app_present": compiled_app_present,
        "compiled_at": compiled_at if compiled_app_present else None,
    }