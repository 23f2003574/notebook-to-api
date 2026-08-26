from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List
import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import threading
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
    _extract_explicit_requirements,
    _filter_functions_by_name,
    compile_notebook,
    extract_third_party_imports,
    hash_notebook_file,
    package_name_for_output_dir,
    resolve_requirements,
    update_compile_metadata_source_notebook,
)
from backend.generator.api_generator import (
    ReservedFunctionNameError,
    generate_fastapi_code,
)
from backend.inspector import (
    EXCLUDED_GENERATED_DIR_NAMES,
    EXCLUDED_GENERATED_FILE_NAMES,
    _extract_notebook_functions,
    diff_notebook_functions,
    generate_curl_commands,
    inspect_notebook_data,
    list_generated_files,
)
from backend.parser.ast_parser import (
    deduplicate_functions_by_name,
    extract_functions_from_code,
    is_parseable_python,
)
from backend.parser.notebook_parser import (
    extract_code_cells,
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

# Every route below except upload_notebook and upload_notebooks_batch
# (which genuinely await UploadFile.read, via _save_uploaded_notebook) is
# declared as a plain `def`, not `async def`. FastAPI
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

# Confirmed catastrophic if left unchecked: NOTEBOOK_API_UPLOAD_DIR and
# NOTEBOOK_API_GENERATED_DIR are each read independently above, with
# nothing stopping an operator from -- accidentally or otherwise --
# configuring them to the same directory, or one nested inside the
# other. Reproduced live: pointing both at the same path, uploading a
# notebook, compiling it, then calling DELETE /api/generated -- whose own
# docstring says it "resets the dashboard's compiled-app state back to
# nothing compiled yet" via shutil.rmtree(GENERATED_DIR) -- permanently
# destroyed the uploaded notebook right along with it: the whole shared
# directory vanished outright, not just the compiled output, with no way
# to recover it. The identical destructive potential applies to
# GENERATED_DIR nested inside UPLOAD_DIR (that same rmtree call would
# still remove real uploaded notebooks sitting under it) and UPLOAD_DIR
# nested inside GENERATED_DIR (the reverse -- a recompile's own
# clear_stale_export_artifacts, backend/compiler.py, or a future
# GENERATED_DIR-wide write could just as easily reach into it). Rejected
# outright at import time instead of silently running with it, the same
# "fail fast on a configuration this dangerous" precedent
# allowed_origins()'s own wildcard-CORS-origin rejection already sets
# (backend/dashboard.py) -- by the time a request has arrived to trigger
# the actual data loss, it's too late to warn about it.
_upload_dir_resolved = Path(UPLOAD_DIR).resolve()
_generated_dir_resolved = Path(GENERATED_DIR).resolve()

if (
    _upload_dir_resolved == _generated_dir_resolved
    or _upload_dir_resolved in _generated_dir_resolved.parents
    or _generated_dir_resolved in _upload_dir_resolved.parents
):
    raise ValueError(
        f"NOTEBOOK_API_UPLOAD_DIR ({UPLOAD_DIR!r}) and "
        f"NOTEBOOK_API_GENERATED_DIR ({GENERATED_DIR!r}) must not be the "
        "same directory, or nested inside each other -- DELETE "
        "/api/generated removes GENERATED_DIR (and everything under it) "
        "outright, which would destroy uploaded notebooks too if the two "
        "overlap. Configure them to point at two separate, non-nested "
        "directories."
    )

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

# Same NOTEBOOK_API_* convention as MAX_UPLOAD_BYTES above. Without this,
# POST /api/upload/batch (see upload_notebooks_batch below) accepted a
# multipart request with any number of files at all -- a single request
# could try to stream, validate, and write an unbounded number of
# notebooks, each up to MAX_UPLOAD_BYTES on its own, tying up a worker
# thread for as long as that takes with nothing to cap the total.
MAX_BATCH_UPLOAD_FILES = int(
    os.getenv("NOTEBOOK_API_MAX_BATCH_UPLOAD_FILES", "50")
)

# Same NOTEBOOK_API_* convention as MAX_UPLOAD_BYTES above, rather than the
# fixed 600s every `docker build`/`docker push` call in /api/deploy
# previously hardcoded -- some deploy environments legitimately need
# longer (a slow/cold image layer cache) or want it clamped shorter (fail
# fast in CI) than a one-size-fits-all default allows.
DEPLOY_SUBPROCESS_TIMEOUT_SECONDS = int(
    os.getenv("NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS", "600")
)

# Same NOTEBOOK_API_* convention as MAX_NOTEBOOK_VERSIONS below. Without a
# cap, a dashboard's own deploy history (see _append_deploy_history_entry
# below) would grow by one entry every single POST /api/deploy, forever,
# for the lifetime of the dashboard process.
MAX_DEPLOY_HISTORY_ENTRIES = int(
    os.getenv("NOTEBOOK_API_MAX_DEPLOY_HISTORY_ENTRIES", "50")
)

# Same NOTEBOOK_API_* convention as MAX_DEPLOY_HISTORY_ENTRIES above, for
# this dashboard's own compile history (see _append_compile_history_entry
# below) instead of its deploy history.
MAX_COMPILE_HISTORY_ENTRIES = int(
    os.getenv("NOTEBOOK_API_MAX_COMPILE_HISTORY_ENTRIES", "50")
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


# Serializes upload_notebook's own check-then-write critical section per
# destination filename within UPLOAD_DIR -- see _upload_lock_for's own
# docstring below for why this exists. Module-level (not per-request) so
# every concurrent request targeting the same filename shares the same
# Lock instance.
_upload_locks_by_filename = {}


def _upload_lock_for(filename: str) -> asyncio.Lock:
    """asyncio.Lock scoped to a single destination filename within
    UPLOAD_DIR, created lazily and kept for the lifetime of this process.

    Without this, two truly concurrent POST /api/upload requests for the
    same, brand-new filename raced this endpoint's own check-then-write
    sequence -- confirmed exploitable, reproduced directly: firing two
    concurrent uploads of different content under the same never-before-
    seen filename from two threads against a live server in a tight loop
    reliably produced trials where both requests' "does this already
    exist" check (either the early one at the top of this function, or
    the one re-checked immediately before the final os.replace, added
    specifically so "overwritten" stays accurate against a concurrent
    writer -- see that check's own comment) observed "not yet", since
    neither request's os.replace() had run yet when either checked. Both
    then proceeded straight through to os.replace() with no 409 raised by
    either, one silently clobbered the other's just-uploaded content, and
    *both* responses reported "overwritten": false -- directly
    contradicting this endpoint's own explicit contract (see
    upload_notebook's own docstring: a same-named notebook is supposed to
    be rejected with 409 unless the caller opts in via ?overwrite=true).
    That's the exact "silently destroyed with no way to recover it"
    failure mode that contract exists to prevent in the first place, just
    reintroduced through a race window between the check and the write,
    rather than the original "write immediately, validate after" ordering
    that contract was written to fix.

    Scoped per filename, not a single global lock covering every upload:
    two concurrent uploads of two *different* notebooks -- the
    overwhelmingly common case -- must stay fully concurrent; only
    genuinely colliding same-name uploads need to serialize at all.

    Never removed once created: this dict grows by at most one small Lock
    object per distinct filename ever uploaded over this process's
    lifetime -- a deliberately simple tradeoff over the added complexity
    (and its own race: safely deciding "nothing is waiting on this lock
    anymore" isn't a single atomic check either) of expiring entries,
    negligible next to what UPLOAD_DIR itself would already need to hold
    that many distinct files on disk.
    """
    return _upload_locks_by_filename.setdefault(filename, asyncio.Lock())


# Directory name (hidden, so it's invisible to list_notebooks' own
# ".ipynb"-only iterdir() filter) under UPLOAD_DIR holding one subdirectory
# per notebook filename, each containing that notebook's previous-version
# snapshots (see _snapshot_current_notebook_version below).
UPLOAD_VERSIONS_DIRNAME = ".versions"

# Same NOTEBOOK_API_* env-var convention as MAX_UPLOAD_BYTES/
# STALE_UPLOAD_TEMP_FILE_SECONDS above. Without a cap, a notebook
# overwritten (or restored -- see restore_notebook_version below) many
# times over a dashboard's lifetime would accumulate an unbounded number
# of snapshots on disk forever, with no way to reclaim that space short of
# an operator finding and deleting them by hand.
MAX_NOTEBOOK_VERSIONS = int(
    os.getenv("NOTEBOOK_API_MAX_VERSIONS_PER_NOTEBOOK", "20")
)


def _notebook_versions_dir(notebook_filename: str) -> Path:
    """Directory holding `notebook_filename`'s previous-version snapshots.

    `notebook_filename` is always a notebook's own already-validated
    Path.name (from resolve_upload_path), never raw client input directly
    -- same precondition _tags_sidecar_path already documents for the
    identical reason.
    """
    return Path(UPLOAD_DIR) / UPLOAD_VERSIONS_DIRNAME / notebook_filename


def _prune_notebook_versions(versions_dir: Path) -> None:
    """Remove the oldest snapshots in `versions_dir` beyond
    MAX_NOTEBOOK_VERSIONS, oldest first.

    Snapshot filenames are timestamp-prefixed (see
    _snapshot_current_notebook_version below), so sorting by name alone
    already sorts oldest-to-newest -- no need to stat every file just to
    order them.
    """
    if not versions_dir.is_dir():
        return

    version_files = sorted(
        (entry for entry in versions_dir.iterdir() if entry.is_file()),
        key=lambda entry: entry.name,
    )

    excess = len(version_files) - MAX_NOTEBOOK_VERSIONS

    for stale in version_files[:max(excess, 0)]:
        stale.unlink(missing_ok=True)


def _snapshot_current_notebook_version(file_path: Path) -> None:
    """Copy `file_path`'s current on-disk bytes into its version history
    before they're about to be overwritten.

    Before this, overwriting a notebook (POST /api/upload?overwrite=true
    -- the only way to update a previously uploaded notebook's content)
    destroyed its previous bytes permanently and unconditionally: an
    accidental overwrite (the wrong file, a bad edit, ...) had no way to
    be undone short of the uploader still having their own separate copy.
    Snapshotting here, right before every overwrite, makes that
    recoverable via GET/POST .../versions below.

    A no-op if `file_path` doesn't exist yet -- nothing to snapshot for a
    brand-new upload, which is the common, non-overwriting case.
    """
    if not file_path.is_file():
        return

    versions_dir = _notebook_versions_dir(file_path.name)
    versions_dir.mkdir(parents=True, exist_ok=True)

    # Zero-padded and fixed-width (this format never drops a leading
    # digit) so plain lexicographic sort-by-filename -- see
    # _prune_notebook_versions above and list_notebook_versions below --
    # already sorts snapshots chronologically, with no need to stat each
    # one just to order them. The trailing uuid4 suffix disambiguates two
    # snapshots of the same notebook saved within the same microsecond.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    version_id = f"{timestamp}_{uuid.uuid4().hex[:8]}.ipynb"

    shutil.copy2(file_path, versions_dir / version_id)

    _prune_notebook_versions(versions_dir)


_version_locks_by_filename = {}


def _version_lock_for(filename: str) -> threading.Lock:
    """threading.Lock scoped to a single notebook filename's version
    history, guarding restore_notebook_version's own snapshot-then-copy
    sequence below.

    A plain threading.Lock, not an asyncio.Lock like upload_notebook's own
    _upload_lock_for -- restore_notebook_version is a plain `def`, not
    `async def` (see test_blocking_endpoints_are_declared_as_plain_def_not_async_def),
    the same reason rename_notebook's own _rename_lock_for above is a
    threading.Lock rather than reusing _upload_lock_for's asyncio.Lock.

    A separate lock table from _rename_lock_for/_upload_lock_for, scoped
    only to restore: this codebase doesn't attempt to serialize every
    write endpoint against every other one for the same filename (rename
    and upload don't cross-lock each other either) -- only concurrent
    calls to the *same* operation.
    """
    return _version_locks_by_filename.setdefault(filename, threading.Lock())


async def _save_uploaded_notebook(file: UploadFile, overwrite: bool) -> dict:
    """Validate and save one uploaded notebook, exactly as upload_notebook
    (below) always has -- extracted into its own function so
    upload_notebook and upload_notebooks_batch (below) share this one
    implementation instead of a second, inevitably-drifting copy of it.

    Raises the identical HTTPException upload_notebook always raised for
    each failure mode -- unchanged behavior for upload_notebook's own
    single-file callers. upload_notebooks_batch instead catches that
    HTTPException per file, so one bad file in a batch doesn't abort every
    other file's own upload.
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

    # See _upload_lock_for's own docstring for exactly what this closes:
    # without it, two truly concurrent uploads of the same, brand-new
    # filename could both pass every "does this already exist" check
    # below (neither has written file_path yet when either checks) and
    # both proceed straight through to os.replace() -- one silently
    # clobbering the other's just-uploaded content, with *both* responses
    # wrongly reporting "overwritten": false and no 409 raised by either.
    async with _upload_lock_for(file_path.name):

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
        # one's body was still streaming/validating. Still meaningful even
        # under the lock above: it's what catches the *sequential* case (an
        # earlier request for this same filename that already completed
        # and released the lock before this one acquired it), as opposed to
        # the truly-concurrent case the lock itself prevents.
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

        if overwritten:
            _snapshot_current_notebook_version(file_path)

        os.replace(temp_path, file_path)

        return {
            "status": "success",
            "filename": file.filename,
            "path": str(file_path),
            "overwritten": overwritten,
        }


@router.post("/upload")
async def upload_notebook(
    file: UploadFile = File(...),
    overwrite: bool = False,
    tags: str = None,
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

    An overwrite is no longer permanently destructive, either: the
    previous content is snapshotted (see
    _snapshot_current_notebook_version above) right before it's replaced,
    recoverable afterward via GET/POST
    /api/notebooks/{filename}/versions[/{version_id}[/restore]].

    "tags" (optional, a comma-separated list, validated via
    _parse_and_validate_tags_query_param above -- the identical query
    param POST /api/notebooks/import already accepts for the same
    reason) replaces this notebook's own tag set once the upload itself
    succeeds. Before this, tagging a notebook at upload time meant a
    separate PUT /api/notebooks/{filename}/tags round trip immediately
    after -- fine for a human clicking through a dashboard UI, but an
    unnecessary extra request for a script uploading and tagging in one
    logical step (e.g. seeding a "production" notebook that should never
    exist untagged, even for the instant between the two calls). An
    invalid "tags" value is rejected with 400 before the file itself is
    even read, so a malformed tag list can't cause a notebook to be
    uploaded successfully but silently left untagged.
    """

    normalized_tags = _parse_and_validate_tags_query_param(tags)

    result = await _save_uploaded_notebook(file, overwrite)

    if normalized_tags is not None:
        _write_notebook_tags(result["filename"], normalized_tags)

    return result


@router.post("/upload/batch")
async def upload_notebooks_batch(
    files: List[UploadFile] = File(...),
    overwrite: bool = False,
    tags: str = None,
):
    """Upload several Jupyter notebook files in a single request.

    POST /api/upload only ever accepted one file per request -- uploading
    a whole batch of notebooks (an initial import, restoring several from
    a backup, seeding a demo environment, ...) meant one HTTP round trip
    per file, with a caller left to script that themselves. Reuses the
    exact same per-file validation and atomic-write logic as
    upload_notebook (see _save_uploaded_notebook above), so a notebook
    uploaded through either endpoint is validated, versioned on overwrite,
    and written identically.

    Unlike a plain loop of individual POST /api/upload calls, one bad file
    in the batch (invalid notebook content, an oversized file, a same-name
    collision without ?overwrite=true, ...) does not abort the rest: each
    file is processed independently, and "results" reports a
    {"filename", "status", ...} entry per file -- "success" with the same
    shape upload_notebook's own response has, or "error" with the
    HTTPException detail that file's own upload would have raised on its
    own. The response is always 200 (even if every file failed) since the
    batch request itself was handled successfully; "succeeded_count"/
    "failed_count" tell the caller how many of "results" landed which way
    without needing to scan the whole list themselves.

    "overwrite" applies uniformly to every file in the batch, the same
    single flag POST /api/upload itself takes -- there's no per-file
    override; a caller needing different overwrite behavior per file
    still needs separate requests for those.

    Bounded by MAX_BATCH_UPLOAD_FILES (default 50, configurable via
    NOTEBOOK_API_MAX_BATCH_UPLOAD_FILES) so a single request can't try to
    stream, validate, and write an unbounded number of notebooks at once.

    "tags" (optional, a comma-separated list, validated via
    _parse_and_validate_tags_query_param above) applies uniformly to
    every successfully-uploaded file in the batch, the same single flag
    "overwrite" itself already applies uniformly rather than per-file --
    the identical query param POST /api/upload and POST
    /api/notebooks/import already accept for a single upload/a zip
    import respectively, just shared across every file this endpoint
    uploads at once instead. Validated once, up front, as a
    whole-request 400 -- a malformed "tags" value is this request's own
    fault, not any one file's -- and never applied to a file that itself
    failed to upload.
    """

    if len(files) > MAX_BATCH_UPLOAD_FILES:

        raise HTTPException(
            status_code=400,
            detail=(
                f"A batch upload accepts at most {MAX_BATCH_UPLOAD_FILES} "
                f"files at once (got {len(files)})."
            )
        )

    normalized_tags = _parse_and_validate_tags_query_param(tags)

    results = []
    succeeded_count = 0
    failed_count = 0

    for file in files:

        try:

            result = await _save_uploaded_notebook(file, overwrite)

            if normalized_tags is not None:
                _write_notebook_tags(result["filename"], normalized_tags)

            results.append(result)
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": file.filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.post("/notebooks/import")
async def import_notebooks(
    file: UploadFile = File(...),
    overwrite: bool = False,
    tags: str = None,
):
    """Upload every .ipynb file bundled inside a single .zip archive --
    the counterpart to GET /api/notebooks/export, which produces exactly
    this kind of archive from a set of already-uploaded notebooks.

    POST /api/upload/batch already accepts several notebooks in one
    request, but only as separate multipart file parts a caller has to
    construct itself, one per notebook -- restoring an export produced by
    GET /api/notebooks/export (or any other .zip a caller already has,
    e.g. downloaded from a teammate or pulled from a backup) meant
    unzipping it locally first and re-uploading each extracted .ipynb
    individually or via /upload/batch, rather than handing the archive
    itself to this API directly.

    Each entry's filename is taken from its own basename within the
    archive -- "subdir/a.ipynb" is imported as "a.ipynb", exactly like
    the flat, non-nested namespace GET /api/notebooks and every other
    endpoint in this file already assume for UPLOAD_DIR -- so a zip
    that happens to bundle its notebooks inside a folder (as most zip
    tools do by default when archiving a directory) still imports
    cleanly, and no entry can use ".." or an absolute path segment to
    escape UPLOAD_DIR: only the basename is ever used to build the
    resulting filename, exactly as if that name had been typed directly
    into upload_notebook's own "file.filename" field. Non-".ipynb"
    entries (a README, a directory entry, ...) are silently skipped
    rather than rejecting the whole archive over content this endpoint
    never claimed to import anyway.

    Reuses _save_uploaded_notebook -- the exact same validation, atomic
    write, and pre-overwrite versioning upload_notebook and
    upload_notebooks_batch already apply per file -- by wrapping each
    entry's bytes in its own in-memory UploadFile, so a notebook imported
    this way is indistinguishable on disk from one uploaded any other way.
    Follows upload_notebooks_batch's own "one bad entry doesn't abort the
    batch" contract identically: a malformed notebook, an oversized entry,
    or a same-name collision without "?overwrite=true" is reported as its
    own {"filename", "status": "error", "detail"} result rather than
    failing every other entry in the archive.

    Bounded by the same MAX_BATCH_UPLOAD_FILES POST /api/upload/batch
    already enforces, for the identical reason: an archive can bundle far
    more entries than a multipart request practically would, and without
    a cap this could try to validate and write an unbounded number of
    notebooks from a single small .zip.

    "tags" (optional, a comma-separated list -- the same query-string
    format GET /api/notebooks/export's own "filenames" already uses)
    replaces every successfully-imported notebook's own tag set with it,
    via the identical _validate_and_normalize_tags/_write_notebook_tags
    pair PUT /api/notebooks/{filename}/tags itself calls -- so, e.g., a
    whole GET /api/notebooks/export?tag=production archive can be
    re-imported elsewhere with "production" already applied, in the same
    request, rather than a separate POST /api/notebooks/tags-batch
    round trip afterward naming every filename the import just produced.
    An invalid "tags" value (see _validate_and_normalize_tags) is
    rejected with 400 up front, before any entry in the archive is even
    read -- the request's own fault, not any one entry's, the same
    reasoning a malformed "entries" shape is already a whole-request 400
    on POST /api/notebooks/tags-batch. Never applied to an entry that
    itself failed to import: its own {"filename", "status": "error"}
    result is unaffected, and no tags sidecar file is written for a
    notebook that was never actually saved.
    """

    if not file.filename.endswith(".zip"):

        raise HTTPException(
            status_code=400,
            detail="File must be a .zip archive"
        )

    normalized_tags = _parse_and_validate_tags_query_param(tags)

    try:

        zip_bytes = await file.read()

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    try:

        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))

    except zipfile.BadZipFile:

        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid zip archive"
        )

    notebook_entries = [
        name for name in archive.namelist()
        if name.endswith(".ipynb") and not name.endswith("/")
    ]

    if not notebook_entries:

        raise HTTPException(
            status_code=400,
            detail="Zip archive contains no .ipynb files"
        )

    if len(notebook_entries) > MAX_BATCH_UPLOAD_FILES:

        raise HTTPException(
            status_code=400,
            detail=(
                f"A zip import accepts at most {MAX_BATCH_UPLOAD_FILES} "
                f".ipynb files at once (got {len(notebook_entries)})."
            )
        )

    results = []
    succeeded_count = 0
    failed_count = 0

    for entry_name in notebook_entries:

        filename = os.path.basename(entry_name)

        try:

            entry_bytes = archive.read(entry_name)

            upload_file = UploadFile(
                file=io.BytesIO(entry_bytes), filename=filename
            )

            result = await _save_uploaded_notebook(upload_file, overwrite)

            if normalized_tags is not None:
                _write_notebook_tags(result["filename"], normalized_tags)

            results.append(result)
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
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

# Bounds enforced by _validate_and_normalize_tags below. Without them, a
# single PUT /api/notebooks/{filename}/tags call could attach an unbounded
# number of arbitrarily long strings to a notebook -- there was previously
# no tagging concept at all, so nothing constrained what a caller could
# stuff into the sidecar file _write_notebook_tags writes, or into every
# GET /api/notebooks response listing it back out afterward.
_MAX_TAG_LENGTH = 50
_MAX_TAGS_PER_NOTEBOOK = 20


def _tags_sidecar_path(notebook_filename: str) -> Path:
    """Path to the hidden JSON sidecar file that stores a notebook's tags.

    Stored as ".<filename>.tags.json" directly inside UPLOAD_DIR -- hidden
    (leading dot) so it's invisible to list_notebooks' own ".ipynb"-only
    iterdir() filter below, the same way an in-flight upload's own hidden
    "*.part" temp file already is (see _cleanup_stale_upload_temp_files
    above).

    `notebook_filename` is always a notebook's own already-validated
    Path.name (from resolve_upload_path, e.g. file_path.name), never raw,
    unresolved client input directly -- so unlike resolve_upload_path's own
    callers, this doesn't need its own traversal check.
    """
    return Path(UPLOAD_DIR) / f".{notebook_filename}.tags.json"


def _read_notebook_tags(notebook_filename: str) -> list:
    """Tags currently recorded for the notebook named `notebook_filename`,
    sorted, or [] if it has none -- no sidecar file yet (the common case:
    most notebooks are never tagged), or one that's
    missing/unreadable/corrupt. Tags are optional, best-effort metadata,
    the same way .compile_metadata.json already is for
    _currently_compiled_notebook_metadata above -- a bad sidecar file
    should never break GET /api/notebooks over it.
    """
    sidecar_path = _tags_sidecar_path(notebook_filename)

    if not sidecar_path.is_file():
        return []

    try:

        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    except (OSError, ValueError):
        return []

    tags = data.get("tags")

    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return []

    return sorted(tags)


def _write_notebook_tags(notebook_filename: str, tags: list) -> None:
    """Persist `tags` (already validated/deduplicated/sorted by the
    caller -- see _validate_and_normalize_tags) as the tag set for the
    notebook named `notebook_filename`.

    Removes the sidecar file entirely, rather than writing an empty
    "tags": [] one, when `tags` is empty -- so an untagged notebook (every
    notebook, before this feature existed at all) never accumulates a
    sidecar file on disk just from an empty PUT. Mirrors
    write_compile_metadata's own file only ever existing once something
    has actually been recorded (backend/compiler.py).

    Writes via a temp-file-then-os.replace swap in the same directory --
    the identical atomic-write pattern upload_notebook already uses for
    the notebook file itself (see temp_path in upload_notebook above) --
    so a concurrent reader (list_notebooks, GET .../tags) never observes a
    partially-written sidecar file. The temp file's own name ends in
    ".part", so if a hard crash ever left one behind mid-write, it's swept
    up by the exact same _cleanup_stale_upload_temp_files opportunistic
    sweep that already handles upload_notebook's identical leftover-temp-
    file case, with no separate cleanup mechanism needed.
    """
    sidecar_path = _tags_sidecar_path(notebook_filename)

    if not tags:
        sidecar_path.unlink(missing_ok=True)
        return

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_path = upload_root / f".{notebook_filename}.tags.{uuid.uuid4().hex}.part"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"tags": tags}, f)

    os.replace(temp_path, sidecar_path)


def _validate_and_normalize_tags(raw_tags) -> list:
    """Validate `raw_tags` (the "tags" field of a PUT
    /api/notebooks/{filename}/tags JSON body) and return it deduplicated
    and sorted.

    `raw_tags` comes straight from a raw JSON body field, not a
    Pydantic-validated type -- the same reason resolve_upload_path's own
    isinstance check exists for "notebook_path" elsewhere in this file --
    so it can be any JSON type at all (a string, a number, null, ...), not
    necessarily the list of strings this endpoint actually needs.

    Each tag is stripped of surrounding whitespace before validation, so
    "  bug  " and "bug" are treated as the same tag rather than two
    distinct ones that merely look identical -- and an empty or
    whitespace-only string is rejected rather than silently becoming a
    blank, meaningless tag. Deduplicated case-sensitively (a set) since
    two callers tagging the same notebook with "bug" don't need two
    identical entries, but "bug" and "Bug" are left as distinct tags --
    this endpoint has no basis for deciding those are the same label.
    """
    if not isinstance(raw_tags, list):

        raise HTTPException(
            status_code=400,
            detail="tags must be a list of strings"
        )

    normalized = set()

    for tag in raw_tags:

        if not isinstance(tag, str):

            raise HTTPException(
                status_code=400,
                detail="each tag must be a string"
            )

        cleaned = tag.strip()

        if not cleaned:

            raise HTTPException(
                status_code=400,
                detail="tags must not be empty or whitespace-only strings"
            )

        if len(cleaned) > _MAX_TAG_LENGTH:

            raise HTTPException(
                status_code=400,
                detail=f"tags must be at most {_MAX_TAG_LENGTH} characters long"
            )

        normalized.add(cleaned)

    if len(normalized) > _MAX_TAGS_PER_NOTEBOOK:

        raise HTTPException(
            status_code=400,
            detail=f"a notebook may have at most {_MAX_TAGS_PER_NOTEBOOK} distinct tags"
        )

    return sorted(normalized)


def _parse_and_validate_tags_query_param(tags: str):
    """Parse a comma-separated "tags" query-string value (the same format
    GET /api/notebooks/export's own "filenames" already uses) into a
    validated, normalized tag list via _validate_and_normalize_tags above
    -- or None if `tags` is empty/omitted, distinct from [] (an explicit,
    already-normalized empty list would mean "clear every tag", which no
    caller of this helper currently means by simply not passing "tags" at
    all).

    Shared by every upload-time endpoint that accepts an optional "tags"
    query param to apply on success (POST /api/upload, POST
    /api/upload/batch, POST /api/notebooks/import) so parsing/validating a
    query-string tag list is identical -- and raises the identical 400
    HTTPException -- everywhere it's accepted, rather than three
    independent, inevitably-drifting copies of the same split/strip/
    validate logic.
    """
    if not tags:
        return None

    return _validate_and_normalize_tags(
        [tag.strip() for tag in tags.split(",") if tag.strip()]
    )


# A notebook's tags (above) are categorical labels for filtering/grouping --
# good for "which notebooks are 'production'", bad for "why does this
# notebook exist, and is it still needed". Bounds a single freeform
# description the same way _MAX_TAG_LENGTH bounds a single tag: without a
# cap, a single PUT /api/notebooks/{filename}/description call could stuff
# an arbitrarily large string into the sidecar file _write_notebook_description
# below writes, and into every GET /api/notebooks response listing it back
# out afterward.
_MAX_DESCRIPTION_LENGTH = 2000


def _description_sidecar_path(notebook_filename: str) -> Path:
    """Path to the hidden JSON sidecar file that stores a notebook's own
    freeform description -- the same ".<filename>.<thing>.json" hidden-
    sidecar convention _tags_sidecar_path already establishes for tags,
    just its own separate file rather than a second field jammed into
    that one: _write_notebook_tags already unconditionally overwrites its
    entire sidecar file's content with `{"tags": [...]}` on every write
    (see its own docstring), so sharing one file between the two would
    mean a tag-only write silently discarding whatever description had
    already been set, and vice versa -- confirmed by that function's own
    `json.dump({"tags": tags}, f)` overwrite, not a merge.

    `notebook_filename` is always a notebook's own already-validated
    Path.name, never raw, unresolved client input directly -- same
    precondition _tags_sidecar_path already documents for the identical
    reason.
    """
    return Path(UPLOAD_DIR) / f".{notebook_filename}.description.json"


def _read_notebook_description(notebook_filename: str) -> str:
    """The description currently recorded for the notebook named
    `notebook_filename`, or "" if it has none -- no sidecar file yet (the
    common case: most notebooks are never given one), or one that's
    missing/unreadable/corrupt. A description is optional, best-effort
    metadata, the same way a notebook's tags already are (see
    _read_notebook_tags) -- a bad sidecar file should never break GET
    /api/notebooks over it.
    """
    sidecar_path = _description_sidecar_path(notebook_filename)

    if not sidecar_path.is_file():
        return ""

    try:

        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    except (OSError, ValueError):
        return ""

    description = data.get("description")

    if not isinstance(description, str):
        return ""

    return description


def _write_notebook_description(notebook_filename: str, description: str) -> None:
    """Persist `description` (already validated/stripped by the caller)
    as the description for the notebook named `notebook_filename`.

    Removes the sidecar file entirely, rather than writing an empty
    "description": "" one, when `description` is empty -- so a notebook
    that's never had one (every notebook, before this feature existed at
    all) never accumulates a sidecar file on disk just from an empty PUT.
    Mirrors _write_notebook_tags' own identical "empty in, no file on
    disk" contract.

    Writes via a temp-file-then-os.replace swap in the same directory --
    the identical atomic-write pattern _write_notebook_tags already uses
    -- so a concurrent reader (GET .../description, list_notebooks) never
    observes a partially-written sidecar file.
    """
    sidecar_path = _description_sidecar_path(notebook_filename)

    if not description:
        sidecar_path.unlink(missing_ok=True)
        return

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_path = upload_root / f".{notebook_filename}.description.{uuid.uuid4().hex}.part"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"description": description}, f)

    os.replace(temp_path, sidecar_path)


@router.get("/tags")
def list_tags():
    """The distinct tags currently in use across every uploaded notebook,
    each with how many notebooks currently carry it, sorted alphabetically.

    GET /api/notebooks?tag=<tag> already filters by an exact tag, and each
    of its own entries already lists that notebook's own tags -- but
    there was previously no way to discover what tags actually *exist*
    across UPLOAD_DIR without first fetching every notebook and
    collecting their "tags" fields client-side. A dashboard frontend
    building a tag-filter dropdown (or a caller scripting `list --tag`)
    had no single call to populate it from, and had to either hardcode a
    guessed set of tags or pay the cost of an unfiltered, unpaginated GET
    /api/notebooks just to enumerate them.

    Reuses _read_notebook_tags -- the exact same per-notebook tag read
    list_notebooks already performs for its own "tags" field -- so a
    notebook's tags are counted identically here and there; nothing about
    what counts as "tagged" is redefined for this endpoint.

    Untagged notebooks (no sidecar file, see _read_notebook_tags) simply
    contribute nothing here, the same way they already contribute an
    empty "tags": [] to their own GET /api/notebooks entry rather than
    raising or being skipped.
    """

    upload_root = Path(UPLOAD_DIR)

    counts = {}

    for entry in upload_root.iterdir():

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        for tag in _read_notebook_tags(entry.name):
            counts[tag] = counts.get(tag, 0) + 1

    tags = [
        {"tag": tag, "notebook_count": count}
        for tag, count in sorted(counts.items())
    ]

    return {
        "status": "success",
        "tags": tags,
    }


@router.delete("/tags/{tag}")
def delete_tag(tag: str):
    """Remove `tag` from every notebook that currently carries it, in one
    call.

    PUT /api/notebooks/{filename}/tags is deliberately a full-replace,
    not a per-tag add/remove, for a single notebook (see its own
    docstring) -- but that decision was about the *shape* of one
    notebook's own tag set, not about retiring a tag across the whole
    catalog GET /api/tags now exposes. Before this, discarding a
    mistyped or retired tag from every notebook that had it meant
    fetching each one's own tags (GET /api/notebooks?tag=<tag> to find
    them, then GET .../tags per notebook to get its full current set),
    removing the one tag client-side, and PUTting the reduced set back --
    one round trip per affected notebook, with no single call to do it in.

    Every other notebook's tags -- including ones that never carried
    `tag` at all -- are left completely untouched: this only ever removes
    `tag` itself from whichever notebooks' own tag sets already contain
    it, never anything else in those sets.

    A 404 for a `tag` no notebook currently carries would only tell a
    caller what GET /api/tags already lets them check first -- and
    "nothing to remove" is a completely valid outcome of a bulk operation
    like this, not an error, the same reasoning DELETE /api/notebooks'
    own "deleted_count": 0 (routes/upload.py) already applies when
    nothing matched. "affected_notebooks" being empty already says so
    just as clearly as a 404 would, without forcing a caller to special-
    case a status code for what is, structurally, still a successful
    request.
    """

    upload_root = Path(UPLOAD_DIR)

    affected_notebooks = []

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        notebook_tags = _read_notebook_tags(entry.name)

        if tag not in notebook_tags:
            continue

        _write_notebook_tags(
            entry.name, [t for t in notebook_tags if t != tag]
        )
        affected_notebooks.append(entry.name)

    return {
        "status": "success",
        "tag": tag,
        "affected_notebooks": affected_notebooks,
        "notebook_count": len(affected_notebooks),
    }


@router.post("/tags/{tag}/apply")
def apply_tag(tag: str, data: dict):
    """Add `tag` to a caller-chosen set of already-uploaded notebooks in
    one call, merging it into each one's own existing tags rather than
    replacing them.

    PUT /api/notebooks/{filename}/tags is deliberately a full-replace,
    not a per-tag add/remove (see its own docstring) -- exactly right
    for one notebook at a time, but it makes tagging *several* notebooks
    with a shared label (e.g. "production" after a review pass) an
    error-prone, multi-round-trip chore: a caller has to GET each
    notebook's current tags first, merge the new one in client-side, then
    PUT the merged set back -- one round trip per notebook, and silently
    destructive (PUT's own replace semantics) if a caller forgets to
    include a notebook's existing tags in that merge. This does the same
    fetch-merge-write per notebook server-side, in one request.

    Deliberately takes an explicit "filenames" list rather than a
    search/tag filter the way GET /api/notebooks and DELETE
    /api/tags/{tag} already accept -- the caller already knows exactly
    which notebooks to tag, typically from a preceding GET
    /api/notebooks?search=... of its own, and an explicit list keeps this
    endpoint's effect fully predictable from its own request body alone,
    without re-deriving GET /api/notebooks' filtering rules a second time
    here for a "tag everything currently matching X" semantics that could
    silently pick up notebooks uploaded *after* the caller last checked.

    Reuses the exact per-file "one bad entry doesn't abort the batch"
    contract POST /api/upload/batch already established: each filename is
    processed independently, and "results" reports one {"filename",
    "status", ...} entry per filename -- "success" (with that notebook's
    resulting full tag set) or "error" (with the HTTPException detail
    that filename's own application would have raised on its own, e.g. a
    404 for a filename that doesn't exist, or the same 400
    _validate_and_normalize_tags already raises if merging `tag` in would
    push that one notebook over _MAX_TAGS_PER_NOTEBOOK). The response is
    always 200 -- the batch request itself was handled, even if every
    filename in it failed -- with "succeeded_count"/"failed_count"
    summarizing "results" the same way POST /api/upload/batch's own
    identical fields already do.
    """

    filenames = data.get("filenames")

    if (
        not isinstance(filenames, list)
        or not filenames
        or not all(isinstance(f, str) for f in filenames)
    ):
        raise HTTPException(
            status_code=400,
            detail="filenames must be a non-empty list of strings"
        )

    # Validate/normalize the tag itself once, up front, reusing the exact
    # same per-tag rules PUT .../tags already enforces (strip whitespace,
    # reject empty, reject over-length) -- a single-element list in, one
    # cleaned tag out. Deliberately outside the per-filename try/except
    # below: an invalid `tag` is this whole request's fault, not any one
    # filename's, so it's rejected once for the entire batch rather than
    # repeated as the same "error" entry for every filename in it.
    tag = _validate_and_normalize_tags([tag])[0]

    results = []
    succeeded_count = 0
    failed_count = 0

    for filename in filenames:

        try:

            file_path = resolve_upload_path(filename)

            if not file_path.is_file():

                raise HTTPException(
                    status_code=404,
                    detail="Notebook file not found"
                )

            existing_tags = _read_notebook_tags(file_path.name)
            merged_tags = _validate_and_normalize_tags(existing_tags + [tag])

            _write_notebook_tags(file_path.name, merged_tags)

            results.append({
                "filename": filename,
                "status": "success",
                "tags": merged_tags,
            })
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "tag": tag,
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.post("/tags/{tag}/remove")
def remove_tag_batch(tag: str, data: dict):
    """Remove `tag` from a caller-chosen set of already-uploaded notebooks
    in one call, leaving each one's other tags untouched.

    DELETE /api/tags/{tag} already retires a tag from *every* notebook
    that carries it, and POST /api/tags/{tag}/apply already adds a tag to
    a caller-chosen *set* of notebooks -- but there was no way to remove a
    tag from just a caller-chosen set, the mirror image of apply. Before
    this, discarding a tag from a handful of notebooks without touching
    every other notebook that also carries it meant GETting each target
    notebook's current tags, dropping the one tag client-side, and PUTting
    the reduced set back -- one round trip per notebook, same as
    POST /api/tags/{tag}/apply's own docstring already describes for the
    add case.

    Deliberately takes an explicit "filenames" list, the same reasoning
    POST /api/tags/{tag}/apply's own docstring already gives for that
    endpoint: the caller already knows exactly which notebooks to affect,
    typically from a preceding GET /api/notebooks?tag=<tag> or
    ?search=... of its own.

    Reuses the exact per-file "one bad entry doesn't abort the batch"
    contract POST /api/tags/{tag}/apply already established: each filename
    is processed independently, and "results" reports one {"filename",
    "status", ...} entry per filename -- "success" (with that notebook's
    resulting tag set) or "error" (with the HTTPException detail that
    filename's own removal would have raised on its own, e.g. a 404 for a
    filename that doesn't exist). A notebook that exists but never carried
    `tag` in the first place still counts as "success" with its tags
    unchanged -- removing a tag that isn't there is a no-op, not an error,
    the same "nothing to act on is still a valid outcome" reasoning
    DELETE /api/tags/{tag}'s own empty "affected_notebooks" already
    follows for the whole-catalog case.
    """

    filenames = data.get("filenames")

    if (
        not isinstance(filenames, list)
        or not filenames
        or not all(isinstance(f, str) for f in filenames)
    ):
        raise HTTPException(
            status_code=400,
            detail="filenames must be a non-empty list of strings"
        )

    # Validated the same way POST /api/tags/{tag}/apply validates the tag
    # it's adding -- once, up front, for the whole batch, since an invalid
    # `tag` is this request's fault rather than any one filename's.
    tag = _validate_and_normalize_tags([tag])[0]

    results = []
    succeeded_count = 0
    failed_count = 0

    for filename in filenames:

        try:

            file_path = resolve_upload_path(filename)

            if not file_path.is_file():

                raise HTTPException(
                    status_code=404,
                    detail="Notebook file not found"
                )

            remaining_tags = [
                t for t in _read_notebook_tags(file_path.name) if t != tag
            ]

            _write_notebook_tags(file_path.name, remaining_tags)

            results.append({
                "filename": filename,
                "status": "success",
                "tags": remaining_tags,
            })
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "tag": tag,
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.patch("/tags/{tag}")
def rename_tag(tag: str, data: dict):
    """Rename `tag` to `data["new_tag"]` on every notebook that currently
    carries it, in one call.

    DELETE /api/tags/{tag} and POST /api/tags/{tag}/apply already cover
    retiring a tag everywhere and adding one to a caller-chosen set of
    notebooks, but neither covers the much more common "I typo'd a tag"
    or "we're renaming 'prod' to 'production'" case -- before this, fixing
    a tag's spelling across the whole catalog meant DELETE-ing the old tag
    and then POST .../apply-ing the new one, two separate round trips with
    no atomicity between them (a crash in between leaves every affected
    notebook missing the tag entirely, since the delete already
    committed), and no way to tell, from the two calls alone, that they
    were meant as one rename rather than an unrelated delete followed by
    an unrelated apply.

    Every notebook that already has `new_tag` before the rename simply
    keeps it -- `tag` is dropped and `new_tag` was already present, so
    nothing changes for that notebook's resulting set beyond `tag` being
    gone, the same "one tag in, deduplicated set out" contract
    _validate_and_normalize_tags already guarantees for
    PUT /api/notebooks/{filename}/tags.

    Every other notebook's tags -- including ones that never carried `tag`
    at all -- are left completely untouched, the same guarantee DELETE
    /api/tags/{tag} already makes.
    """

    new_tag = data.get("new_tag")

    if not isinstance(new_tag, str):

        raise HTTPException(
            status_code=400,
            detail="new_tag must be a string"
        )

    new_tag = _validate_and_normalize_tags([new_tag])[0]

    if new_tag == tag:

        raise HTTPException(
            status_code=400,
            detail="new_tag must be different from the current tag"
        )

    upload_root = Path(UPLOAD_DIR)

    affected_notebooks = []

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        notebook_tags = _read_notebook_tags(entry.name)

        if tag not in notebook_tags:
            continue

        renamed_tags = _validate_and_normalize_tags(
            [t for t in notebook_tags if t != tag] + [new_tag]
        )

        _write_notebook_tags(entry.name, renamed_tags)
        affected_notebooks.append(entry.name)

    return {
        "status": "success",
        "tag": tag,
        "new_tag": new_tag,
        "affected_notebooks": affected_notebooks,
        "notebook_count": len(affected_notebooks),
    }


@router.get("/functions")
def search_functions(
    search: str = None, tag: str = None, limit: int = None, offset: int = 0
):
    """Find which uploaded notebooks define a function whose name
    contains `search` (case-insensitive), across every notebook in
    UPLOAD_DIR at once.

    GET /api/notebooks?search=<text> already matches a substring of a
    notebook's own *filename* -- but a notebook's filename tells a caller
    nothing about what functions it actually defines. Before this,
    answering "which of my uploaded notebooks already has a function
    called `train_model`" (before writing a duplicate under a different
    notebook, or auditing which notebooks would expose a `/train_model`
    endpoint once compiled) meant downloading and inspecting every
    uploaded notebook one at a time -- POST /api/inspect works on exactly
    one notebook_path per call, with nothing to search across the whole
    UPLOAD_DIR at once.

    Reuses inspector._extract_notebook_functions -- the same
    extract-every-cell/deduplicate pipeline inspect_notebook_data's own
    "functions" field already runs on a single notebook -- so a match
    here reports the exact same function shape (name, args, return_type,
    is_async, ...) that endpoint already would for the same notebook,
    with nothing about what counts as a "function" redefined for this
    endpoint. Only ever reads notebooks, never GENERATED_DIR or
    UPLOAD_DIR's tag sidecars -- no COMPILE_LOCK needed, unlike routes
    that walk GENERATED_DIR while a concurrent compile could be rewriting
    it.

    A notebook that fails to parse (MALFORMED_NOTEBOOK_ERRORS -- e.g. one
    tampered with directly on disk, outside of POST /api/upload's own
    validation) is silently skipped rather than failing this entire
    bulk scan over one unrelated notebook's own bad content -- the same
    "one bad entry doesn't sink a bulk listing" precedent
    _read_notebook_tags already sets for a corrupt tags sidecar file.

    "tag" (optional) scopes the scan to only notebooks currently carrying
    that exact tag, the same exact-match GET /api/notebooks?tag= already
    filters by -- closing the gap between this endpoint's own
    catalog-wide scan and a caller who only cares about one category of
    notebooks (e.g. "which of my *production* notebooks still define
    `train_model`"), which previously meant scanning every notebook's own
    functions and filtering the response down client-side afterward. A
    notebook that fails to parse is still silently skipped exactly as
    above, whether or not it happens to carry "tag" -- reading its tags
    sidecar first only to then discard it as unparseable would be wasted
    work either way.

    "limit"/"offset" page the returned "matches" (one entry per matching
    notebook, each with every one of its own matching functions) the
    identical way GET /api/notebooks' own "limit"/"offset" already page
    the notebook catalog -- before this, a catalog with many notebooks
    defining a commonly-searched name always returned every matching
    notebook's own entry in one response. Applied last, after every
    "tag"-matching notebook has already been scanned (still needed in
    full to compute "notebook_count" correctly); a negative "offset" is
    rejected with 400 and a non-positive "limit" the same way GET
    /api/notebooks' own do. "notebook_count" always reflects every
    matching notebook, never just the current page, the same "totals
    describe the whole matching set" reasoning GET /api/notebooks/
    storage's own identically-paginated running totals already follow.
    """

    if not search:

        raise HTTPException(
            status_code=400,
            detail="search is required"
        )

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    if limit is not None and limit <= 0:

        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer"
        )

    upload_root = Path(UPLOAD_DIR)

    search_lower = search.lower()

    matches = []

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if tag and tag not in _read_notebook_tags(entry.name):
            continue

        try:

            functions = _extract_notebook_functions(str(entry))

        except MALFORMED_NOTEBOOK_ERRORS:
            continue

        matching_functions = [
            func for func in functions
            if search_lower in func["name"].lower()
        ]

        if matching_functions:
            matches.append({
                "filename": entry.name,
                "functions": matching_functions,
            })

    notebook_count = len(matches)

    paginated_matches = (
        matches[offset:offset + limit] if limit is not None else matches[offset:]
    )

    return {
        "status": "success",
        "search": search,
        "matches": paginated_matches,
        "notebook_count": notebook_count,
        "limit": limit,
        "offset": offset,
    }


def _notebook_metadata_entry(entry, compiled_path, compiled_sha256, compiled_at):
    """Build one notebook's own metadata dict -- the exact shape GET
    /api/notebooks' own "notebooks" list already returns per entry (see
    its own docstring for what each field means and why it exists).
    Shared with GET /api/notebooks/{filename}/info below, so a
    single-notebook metadata fetch can never drift from the identical
    entry already embedded in the full list for the same notebook.
    """

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
        "tags": _read_notebook_tags(entry.name),
        "description": _read_notebook_description(entry.name),
    }

    if is_currently_compiled:
        notebook_entry["notebook_changed_since_compile"] = (
            compiled_sha256 is not None
            and hash_notebook_file(entry) != compiled_sha256
        )
        notebook_entry["compiled_at"] = compiled_at

    return notebook_entry


@router.get("/notebooks")
def list_notebooks(
    search: str = None,
    sort: str = "name",
    order: str = "asc",
    limit: int = None,
    offset: int = 0,
    tag: str = None,
    description_search: str = None,
    sha256: str = None,
):
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

    "limit" and "offset" close a related gap "search"/"sort"/"order" alone
    didn't: without them, this endpoint always returned every matching
    notebook in one response, no matter how many UPLOAD_DIR has
    accumulated -- a dashboard frontend paginating a large notebook list
    (or simply wanting "the 20 most recently modified") had to fetch the
    entire list on every page load and slice it client-side itself, and a
    caller scripting against this endpoint had no way to bound response
    size at all. "offset" (default 0) is how many matching notebooks,
    after "search"/"sort"/"order" are applied, to skip before the page
    starts; "limit", if given, caps how many are returned from there. Both
    apply after sorting/filtering, not before, so paging through results
    stays stable across pages for a given search/sort/order combination.
    "total_count" is returned alongside the (possibly paginated)
    "notebooks" list -- the count of notebooks matching "search" before
    "limit"/"offset" are applied -- so a caller can compute how many pages
    remain without a separate, unpaginated request just to learn the
    total. A negative "offset", or a "limit" that isn't a positive
    integer, is rejected with 400, the same way an invalid "sort"/"order"
    already is above.

    Each entry's "tags" field lists the labels PUT
    /api/notebooks/{filename}/tags has recorded for it (see
    _read_notebook_tags above), or [] for a notebook that's never been
    tagged. Before tagging existed at all, a dashboard with many uploaded
    notebooks had no way to categorize or group them beyond filename
    "search" -- e.g. separating a handful of "production" notebooks from a
    much larger pile of one-off scratch ones. The "tag" query parameter
    filters the list down to notebooks carrying that exact tag, applied
    (like "search") before "sort"/"limit"/"offset", so it composes with
    every other filter/pagination parameter this endpoint already has.

    Each entry's "description" field is the freeform text PUT
    /api/notebooks/{filename}/description has recorded for it (see
    _read_notebook_description above), or "" for a notebook that's never
    had one set -- the same "" default GET .../description itself already
    returns for a single notebook, so a caller sees it here without a
    separate follow-up request per notebook.

    "description_search" (composes with "search"/"tag" the identical way
    they already compose with each other, applied before "sort"/"limit"/
    "offset") filters to notebooks whose own description contains this
    text, case-insensitively -- closing the one search this endpoint's
    own "search" (a filename substring) and GET /api/notebooks/search-
    content (a code cell's own raw source) never covered: description
    text a caller wrote specifically to explain *why* a notebook exists,
    invisible to both of those. A notebook with no description at all
    (description "") simply never matches a non-empty
    "description_search", the same way an untagged notebook already never
    matches "tag".

    "sha256" filters to the notebook(s) whose exact content currently
    hashes to this value -- the same digest GET /api/notebooks/duplicates
    already groups uploads by, and that GET /api/deploy/history's and GET
    /api/compile/history's own "source_notebook_sha256" query params
    already filter by. A notebook can be renamed (PATCH
    /api/notebooks/{filename}) between a compile/deploy and now, so a
    hash read back from either history endpoint had no way to answer
    "is that content still in the catalog, and if so under what
    filename(s) today" short of downloading and re-hashing every
    uploaded notebook by hand. Applied last, after "search"/"tag"/
    "description_search" have already narrowed the candidates -- hashing
    a notebook's full content is real work GET /api/notebooks otherwise
    never does per entry, so it's only ever paid for candidates that
    already passed every cheaper filter first. An unknown hash simply
    yields no matching notebooks, not an error, the same as every other
    filter here.
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

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    if limit is not None and limit <= 0:

        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer"
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

        notebook_tags = _read_notebook_tags(entry.name)

        if tag and tag not in notebook_tags:
            continue

        if description_search and (
            description_search.lower()
            not in _read_notebook_description(entry.name).lower()
        ):
            continue

        if sha256 and hash_notebook_file(entry) != sha256:
            continue

        entry_stat = entry.stat()

        notebook_entry = _notebook_metadata_entry(
            entry, compiled_path, compiled_sha256, compiled_at
        )

        entries.append((entry.name, entry_stat.st_size, entry_stat.st_mtime, notebook_entry))

    sort_key_index = {"name": 0, "size": 1, "modified": 2}[sort]

    entries.sort(
        key=lambda entry_tuple: entry_tuple[sort_key_index],
        reverse=(order == "desc"),
    )

    total_count = len(entries)

    paginated_entries = (
        entries[offset:offset + limit] if limit is not None else entries[offset:]
    )

    notebooks = [entry_tuple[3] for entry_tuple in paginated_entries]

    return {
        "status": "success",
        "notebooks": notebooks,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/notebooks/export")
def export_notebooks(filenames: str = None, tag: str = None):
    """Download a caller-chosen set of already-uploaded notebooks -- or,
    with "filenames" omitted, every uploaded notebook -- bundled into one
    .zip.

    GET /api/notebooks/{filename} already downloads a single notebook's
    raw content, but retrieving several (e.g. to back up a set of
    notebooks before a POST /api/notebooks/delete-batch, or everything
    before DELETE /api/notebooks) meant one request per file with no way
    to get them back as a single, coherent bundle -- unlike GET
    /api/download, which already zips up the *compiled* output in
    GENERATED_DIR in one call, UPLOAD_DIR's own originals had no
    equivalent.

    "filenames" is a comma-separated list, the same format --only/--exclude
    already use on the CLI side (see _parse_comma_separated_names in
    backend/cli.py) -- deliberately not a JSON body, so this stays a plain
    GET a browser tab or curl -O can hit directly, the same reason GET
    /api/download and GET /api/notebooks/{filename} are both GETs rather
    than POSTs despite returning a caller-shaped result.

    "tag" (mutually exclusive with "filenames" -- combining both is a 400,
    since each already picks the export set a completely different way)
    exports every notebook currently carrying that exact tag instead, the
    same exact-match GET /api/notebooks?tag= already filters by -- a
    caller wanting to back up (or hand off) "every notebook tagged
    production" previously had to fetch GET /api/notebooks?tag=production
    first just to build the "filenames" list this endpoint already
    required. Unlike an unknown "filenames" entry, a "tag" that matches no
    notebook at all is simply an empty selection -- the same "no notebooks
    to export" 404 an empty catalog already gets, not a per-tag 404, since
    a tag (unlike a specific filename) was never guaranteed to exist in
    the first place.

    Unlike POST /api/tags/{tag}/apply and POST /api/notebooks/delete-batch,
    a "filenames"-selected export is all-or-nothing rather than "one bad
    entry doesn't abort the batch": a caller asking for a specific set of
    filenames is trying to get exactly those notebooks back, and a zip
    silently missing one of them (because it was already deleted, or
    typo'd) is a worse failure mode here than in a delete/tag batch --
    there's no per-entry "result" list a caller could inspect after the
    fact, since the response body *is* the zip. A 404 up front, naming
    every filename that doesn't exist, lets a caller fix its request
    before getting nothing back.
    """

    if filenames and tag:

        raise HTTPException(
            status_code=400,
            detail="filenames and tag can't both be given -- choose one."
        )

    upload_root = Path(UPLOAD_DIR)

    if filenames:

        requested = [name.strip() for name in filenames.split(",") if name.strip()]

        if not requested:

            raise HTTPException(
                status_code=400,
                detail="filenames must not be empty"
            )

        missing = []
        notebooks_to_export = []

        for name in requested:

            file_path = resolve_upload_path(name)

            if not file_path.is_file():
                missing.append(name)
            else:
                notebooks_to_export.append((name, file_path))

        if missing:

            raise HTTPException(
                status_code=404,
                detail=f"Notebook file(s) not found: {', '.join(missing)}"
            )

    elif tag:

        notebooks_to_export = [
            (entry.name, entry)
            for entry in sorted(upload_root.iterdir())
            if entry.is_file() and entry.suffix == ".ipynb"
            and tag in _read_notebook_tags(entry.name)
        ]

    else:

        notebooks_to_export = [
            (entry.name, entry)
            for entry in sorted(upload_root.iterdir())
            if entry.is_file() and entry.suffix == ".ipynb"
        ]

    if not notebooks_to_export:

        raise HTTPException(
            status_code=404,
            detail="No notebooks to export"
        )

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        for name, file_path in notebooks_to_export:
            archive.write(file_path, name)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="notebooks_export.zip"',
        },
    )


@router.get("/notebooks/duplicates")
def find_duplicate_notebooks(tag: str = None):
    """Group every uploaded notebook by its raw content, reporting only
    the groups with more than one filename -- byte-identical uploads
    sitting in UPLOAD_DIR under different names.

    Nothing in this file previously had any notion of "these two uploads
    are actually the same notebook" -- `copy` (POST
    /api/notebooks/{filename}/copy) deliberately *creates* one of these
    (see its own docstring), and a notebook re-uploaded under a new name
    instead of overwritten (POST /api/upload's own "overwrite" flag) is
    another common way one shows up organically. Before this, spotting
    them meant downloading every uploaded notebook and diffing their
    bytes by hand, or trusting filenames alone, which two independently
    uploaded, unrelated notebooks can share nothing in common with beyond
    a coincidental name.

    Reuses hash_notebook_file (backend/compiler.py) -- the exact same
    SHA-256-of-raw-bytes check GET /api/notebooks' own
    "notebook_changed_since_compile" field already uses to detect whether
    a notebook's content actually changed, not just its mtime -- so two
    notebooks are only ever grouped here because their bytes are actually
    identical, never because they merely look similar.

    Each group's "filenames" is sorted for a deterministic response, and
    groups themselves are sorted by their own "sha256" for the same
    reason -- this endpoint has no natural ordering of its own (unlike GET
    /api/notebooks' filename/size/modified sort) since a duplicate group
    isn't tied to any one notebook's own timestamp or name.

    "tag" (optional) scopes the scan to only notebooks currently carrying
    that exact tag, the same exact-match GET /api/notebooks?tag= already
    filters by, and the identical gap "tag" already closes for GET
    /api/functions and GET /api/notebooks/search-content -- before this,
    answering "which of my *production* notebooks are byte-identical
    duplicates of each other" meant fetching every duplicate group across
    the entire catalog and filtering the response down to "filenames"
    that also happen to carry that tag client-side, which -- unlike a
    simple substring/exact-match filter -- can't be done correctly after
    the fact: a group with one tagged and one untagged member would need
    to be reported as if the untagged one were never there, not just
    hidden or kept whole. Applied before a notebook is even hashed, so an
    out-of-tag notebook never contributes to a group (or gets hashed at
    all) in the first place -- a group where the scan finds only one
    matching notebook simply isn't reported, the same "fewer than two
    members isn't a duplicate group" rule this endpoint already applies
    with no "tag" given.
    """

    upload_root = Path(UPLOAD_DIR)

    entries_by_hash = {}

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if tag and tag not in _read_notebook_tags(entry.name):
            continue

        digest = hash_notebook_file(entry)

        entries_by_hash.setdefault(digest, []).append(entry)

    duplicate_groups = []

    for digest, entries in sorted(entries_by_hash.items()):

        if len(entries) < 2:
            continue

        duplicate_groups.append({
            "sha256": digest,
            "filenames": sorted(entry.name for entry in entries),
            "size_bytes": entries[0].stat().st_size,
        })

    duplicate_notebook_count = sum(
        len(group["filenames"]) for group in duplicate_groups
    )

    return {
        "status": "success",
        "duplicate_groups": duplicate_groups,
        "group_count": len(duplicate_groups),
        "duplicate_notebook_count": duplicate_notebook_count,
    }


@router.post("/notebooks/duplicates/resolve")
def resolve_duplicate_notebooks(data: dict = None):
    """Delete every byte-identical duplicate of each already-uploaded
    notebook in one call, keeping exactly one filename per duplicate
    group -- acting on exactly what GET /api/notebooks/duplicates already
    reports, without a caller having to turn that report back into a
    per-filename POST /api/notebooks/delete-batch request by hand.

    GET /api/notebooks/duplicates already answers "which uploads are
    actually the same notebook under different names", but there was no
    way to act on that beyond copying its own "filenames" out of each
    group and building a delete-batch request naming every filename
    except whichever one a caller wanted kept -- tedious and error-prone
    for a catalog with several duplicate groups at once, and impossible
    to script without first parsing that response.

    By default, keeps each group's alphabetically-first filename (the
    same deterministic order GET /api/notebooks/duplicates' own
    "filenames" already sorts by) and deletes the rest. "keep" (optional)
    overrides that per group: a {"<sha256>": "<filename to keep>"} object
    keyed by each group's own "sha256" from GET /api/notebooks/duplicates
    -- a sha256 naming a filename that isn't actually a member of that
    duplicate group is reported as its own {"sha256", "status": "error"}
    result rather than silently falling back to the default, the same
    "one bad entry doesn't abort the batch" contract POST
    /api/notebooks/delete-batch already established, just keyed by
    "sha256" (a duplicate group's own identity) instead of "filename".

    Every deletion reuses the exact same cleanup DELETE
    /api/notebooks/{filename} and POST /api/notebooks/delete-batch
    already apply per file -- removing that filename's own tags sidecar,
    description sidecar, and version history directory, and reporting
    "was_currently_compiled" -- so a notebook resolved away this way is
    indistinguishable from one deleted individually. A notebook with no
    duplicates at all is left completely untouched: this endpoint only
    ever acts on groups GET /api/notebooks/duplicates would itself
    report, recomputed fresh (not cached from an earlier call), so a
    notebook uploaded or deleted between that GET and this POST is
    reflected correctly.

    "tag" (optional, in the request body) scopes which groups get
    resolved the identical way GET /api/notebooks/duplicates' own "tag"
    query param scopes which groups get reported -- an out-of-tag
    notebook is never hashed, grouped, or deleted, and never counted
    towards whether a group even has more than one member. Without this,
    an operator wanting to clean up duplicates among only their
    "production" notebooks (e.g. before a tag-scoped GET
    /api/notebooks/export?tag=production backup) had no way to avoid this
    endpoint also acting on a byte-identical scratch/experiment notebook
    that happens to carry no tag, or a different one, at all -- exactly
    the same "one bad entry doesn't abort the batch, but an out-of-scope
    entry shouldn't be touched at all" reasoning "tag" already gives GET
    /api/notebooks/duplicates. Passing a non-string "tag" is a 400, the
    same way a malformed "keep" already is below.

    "dry_run" (optional, default false, in the request body) reports the
    exact same "results" a real run would -- each group's own
    "kept_filename", the "deleted_filenames" (with "was_currently_compiled")
    that would be removed, and any "keep" error -- without deleting
    anything at all. Before this, the only way to see what a resolve
    would actually do was GET /api/notebooks/duplicates plus working out
    "keep"'s effect on each group by hand: this endpoint is otherwise
    irreversible (every deletion also discards that filename's own tags,
    description, and version history, the identical permanent cleanup
    DELETE /api/notebooks/{filename} applies), so an operator wanting to
    check a "keep" override actually lands on the filename they intended
    -- before it, and every other duplicate, is gone for good -- had no
    safe way to preview that first. A dry run still recomputes duplicate
    groups fresh (not cached from an earlier call), so its report reflects
    the catalog's exact current state; the top-level response's own
    "dry_run" field echoes back whether this call actually deleted
    anything, so a caller can't mistake one response for the other.
    """

    data = data or {}

    tag = data.get("tag")

    if tag is not None and not isinstance(tag, str):
        raise HTTPException(
            status_code=400,
            detail="tag must be a string"
        )

    keep_overrides = data.get("keep") or {}

    if not isinstance(keep_overrides, dict) or not all(
        isinstance(sha256, str) and isinstance(keep_filename, str)
        for sha256, keep_filename in keep_overrides.items()
    ):
        raise HTTPException(
            status_code=400,
            detail="keep must be an object mapping sha256 to a filename"
        )

    dry_run = bool(data.get("dry_run", False))

    upload_root = Path(UPLOAD_DIR)

    entries_by_hash = {}

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if tag and tag not in _read_notebook_tags(entry.name):
            continue

        entries_by_hash.setdefault(hash_notebook_file(entry), []).append(entry.name)

    duplicate_groups = [
        (sha256, sorted(filenames))
        for sha256, filenames in sorted(entries_by_hash.items())
        if len(filenames) > 1
    ]

    compiled_path, _, _ = _currently_compiled_notebook_metadata()

    results = []
    succeeded_count = 0
    failed_count = 0

    for sha256, filenames in duplicate_groups:

        keep_filename = keep_overrides.get(sha256, filenames[0])

        if keep_filename not in filenames:

            results.append({
                "sha256": sha256,
                "status": "error",
                "detail": (
                    f"'{keep_filename}' is not a member of duplicate "
                    f"group {sha256}"
                ),
            })
            failed_count += 1
            continue

        deleted_filenames = []

        for filename in filenames:

            if filename == keep_filename:
                continue

            file_path = resolve_upload_path(filename)

            was_currently_compiled = (
                compiled_path is not None and file_path.resolve() == compiled_path
            )

            if not dry_run:
                os.remove(file_path)
                _tags_sidecar_path(file_path.name).unlink(missing_ok=True)
                _description_sidecar_path(file_path.name).unlink(missing_ok=True)
                shutil.rmtree(_notebook_versions_dir(file_path.name), ignore_errors=True)

            deleted_filenames.append({
                "filename": filename,
                "was_currently_compiled": was_currently_compiled,
            })

        results.append({
            "sha256": sha256,
            "status": "success",
            "kept_filename": keep_filename,
            "deleted_filenames": deleted_filenames,
        })
        succeeded_count += 1

    return {
        "status": "success",
        "dry_run": dry_run,
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.get("/notebooks/search-content")
def search_notebook_content(
    search: str = None, tag: str = None, limit: int = None, offset: int = 0
):
    """Find every uploaded notebook with a code cell whose raw source
    contains `search` (case-insensitive), across the whole catalog at
    once.

    GET /api/functions already searches every uploaded notebook's own
    function *names* in one call, and GET /api/notebooks?search= matches
    a substring of a notebook's own *filename* -- but neither looks at a
    cell's actual code. Answering "which of my uploaded notebooks still
    call this deprecated function", "where did I use pd.read_csv instead
    of pd.read_parquet", or just "which notebook had that TODO comment"
    meant downloading and grepping every uploaded notebook by hand, since
    POST /api/inspect only ever works on one notebook_path per call and
    reports function/dependency metadata, not raw source text.

    Each match reports the matching cell's own 0-indexed position within
    the notebook (as GET /api/notebooks/{filename}/versions' own ordering
    conventions elsewhere in this file already number things, 0-based)
    and a single-line "snippet" -- the first line within that cell that
    actually contains `search` -- rather than the cell's full source, so
    a caller can tell *where* a match is without this response ballooning
    to the size of every matching notebook's own full content combined.

    A notebook that fails to parse at all is silently skipped rather than
    failing the whole search, the same "one bad entry doesn't sink a bulk
    listing" precedent GET /api/functions' own identical bulk search
    already sets -- unlike GET /api/validate-all, which deliberately
    reports a parse failure as its own result instead: a malformed
    notebook is incidental to what *this* endpoint is answering (does any
    cell contain a substring), the identical reasoning GET /api/functions'
    own docstring already gives for skipping one here too.

    "tag" (optional) scopes the scan to only notebooks currently carrying
    that exact tag, the identical GET /api/notebooks?tag= exact match and
    the same gap it closes for GET /api/functions above -- a caller
    wanting "which of my *production* notebooks still call this
    deprecated function" previously had to scan every uploaded notebook's
    own code and filter the response down client-side afterward.

    "limit"/"offset" page the returned "matches" (one entry per matching
    notebook, each with every one of its own matching cells) the
    identical way GET /api/functions' own "limit"/"offset" already page
    its own analogous per-notebook "matches" -- before this, a catalog
    with many notebooks containing a commonly-searched substring always
    returned every matching notebook's own entry in one response. Applied
    last, after every "tag"-matching notebook has already been scanned
    (still needed in full to compute "notebook_count" correctly); a
    negative "offset" is rejected with 400 and a non-positive "limit" the
    same way GET /api/notebooks' own do. "notebook_count" always reflects
    every matching notebook, never just the current page.
    """

    if not search:

        raise HTTPException(
            status_code=400,
            detail="search is required"
        )

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    if limit is not None and limit <= 0:

        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer"
        )

    upload_root = Path(UPLOAD_DIR)

    search_lower = search.lower()

    matches = []

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if tag and tag not in _read_notebook_tags(entry.name):
            continue

        try:

            notebook = load_notebook(str(entry))

        except MALFORMED_NOTEBOOK_ERRORS:
            continue

        code_cells = extract_code_cells(notebook)

        cell_matches = []

        for cell_index, cell in enumerate(code_cells):

            if search_lower not in cell.lower():
                continue

            snippet = next(
                (
                    line.strip() for line in cell.splitlines()
                    if search_lower in line.lower()
                ),
                "",
            )

            cell_matches.append({
                "cell_index": cell_index,
                "snippet": snippet,
            })

        if cell_matches:
            matches.append({
                "filename": entry.name,
                "matches": cell_matches,
            })

    notebook_count = len(matches)

    paginated_matches = (
        matches[offset:offset + limit] if limit is not None else matches[offset:]
    )

    return {
        "status": "success",
        "search": search,
        "matches": paginated_matches,
        "notebook_count": notebook_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/notebooks/diff")
def diff_notebooks(old: str = None, new: str = None):
    """Compare the top-level functions two already-uploaded notebooks
    would each compile into endpoints -- entirely server-side, without
    downloading either one, and without compiling either one.

    The CLI's own `diff` already does this for two local files, and
    `remote-diff` for one local file against one already-uploaded
    notebook -- but comparing two notebooks that are *both* already on
    this dashboard (e.g. a "staging"-tagged notebook against a
    "production"-tagged one, or a notebook against a copy POST
    /api/notebooks/{filename}/copy made of it earlier) had no way to
    happen without a caller downloading both first and diffing the local
    copies itself, the same round trip GET /api/notebooks/{filename}
    /versions/{version_id}'s own client-side `versions diff` CLI command
    already accepts for comparing a notebook against its own past
    version, just not for two independently-uploaded notebooks.

    Reuses diff_notebook_functions (backend/inspector.py) unchanged --
    the exact same {"added", "removed", "changed", "unchanged"} report
    `diff`/`remote-diff` already produce -- so this can't drift from
    what either of those already report for the same two notebooks.

    "old" and "new" are both required filenames of notebooks already in
    UPLOAD_DIR; each is validated (existence, then parseability) before
    diffing, so a 404 or 400 names exactly which of the two is the
    problem rather than a single ambiguous error.
    """

    if not old or not new:

        raise HTTPException(
            status_code=400,
            detail="old and new are both required"
        )

    old_path = resolve_upload_path(old)

    if not old_path.is_file():

        raise HTTPException(
            status_code=404,
            detail=f"Notebook file not found: {old}"
        )

    new_path = resolve_upload_path(new)

    if not new_path.is_file():

        raise HTTPException(
            status_code=404,
            detail=f"Notebook file not found: {new}"
        )

    for label, path in (("old", old_path), ("new", new_path)):

        try:

            load_notebook(str(path))

        except MALFORMED_NOTEBOOK_ERRORS as e:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{label}' notebook is not a valid Jupyter "
                    f"notebook: {e}"
                )
            )

    diff = diff_notebook_functions(str(old_path), str(new_path))

    return {
        "status": "success",
        "old": old,
        "new": new,
        **diff,
    }


@router.get("/notebooks/storage")
def notebook_storage_usage(tag: str = None, limit: int = None, offset: int = 0):
    """How much disk space UPLOAD_DIR is actually using, broken down per
    notebook -- its own current bytes plus everything its snapshotted
    version history (see _notebook_versions_dir/_snapshot_current_notebook_version
    above) is holding onto -- and as running totals across the whole
    catalog.

    Nothing in this file previously had any notion of "how much space is
    this dashboard's UPLOAD_DIR actually using": GET /api/notebooks
    already reports each notebook's own "size_bytes", but that's silent
    about its version history entirely, which POST /api/upload
    ?overwrite=true and POST .../versions/{version_id}/restore both grow
    on every overwrite, up to MAX_NOTEBOOK_VERSIONS-many snapshots per
    notebook -- and there's no cap at all on how many notebooks a
    dashboard can accumulate. An operator noticing UPLOAD_DIR's disk
    usage climbing (or wanting to catch it before it becomes a problem)
    had no way to see where that space was actually going short of
    shelling into the server and running `du` by hand -- this dashboard's
    own API had nothing to answer that with.

    Each entry's "total_bytes" is its own current content plus its full
    version history combined -- the same combination an operator would
    actually want to reclaim via DELETE .../versions (clear a notebook's
    history) or DELETE /api/notebooks/{filename} (remove the notebook
    outright) -- and "notebooks" is sorted by "total_bytes" descending,
    biggest first, the same "show me what's actually worth acting on"
    ordering `du -sh | sort -rh` already gives an operator on the
    command line, rather than GET /api/notebooks' own default
    alphabetical-by-filename order, which has nothing to do with what
    this endpoint exists to surface.

    Read-only and never touches GENERATED_DIR, so this needs no
    COMPILE_LOCK -- unlike GET /api/notebooks?sort=size, which orders by
    a single notebook's own bytes alone, this is the first place a
    notebook's version history is actually sized and summed at all.

    "tag" (optional) scopes both the per-notebook "notebooks" list and
    every running total to only notebooks currently carrying that exact
    tag, the same exact-match GET /api/notebooks?tag= already filters by
    -- an operator asking "how much space are my *production* notebooks
    (plus their version history) actually using" previously had to fetch
    every notebook's own entry here and filter/re-sum the response down
    client-side, since totals computed over the whole catalog can't be
    narrowed after the fact. Applied before a notebook's own bytes (or
    its version directory) are ever read, so an out-of-tag notebook
    contributes nothing to "notebooks" or any running total at all.

    "limit"/"offset" page the returned "notebooks" list the identical way
    GET /api/notebooks' own "limit"/"offset" already page the notebook
    catalog -- before this, a large catalog always returned every
    notebook's own entry in one response, with no way to ask for just
    "the 10 biggest notebooks" short of fetching everything and slicing it
    client-side, even though "notebooks" is already sorted biggest-first
    for exactly that use case. Applied after sorting, so "limit"
    (optional) and "offset" (default 0) page the same biggest-first order
    "notebooks" already has; a negative "offset" is rejected with 400 and
    a non-positive "limit" the same way GET /api/notebooks' own do.
    Deliberately does NOT scope any running total to the current page --
    "total_notebook_bytes"/"total_version_bytes"/"total_version_count"/
    "total_bytes" (and "notebook_count") always reflect every
    "tag"-matching notebook, the same "totals describe the whole matching
    set, never just one page of it" reasoning GET /api/notebooks' own
    "total_count" already follows for its identically-paginated list.
    """

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    if limit is not None and limit <= 0:

        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer"
        )

    upload_root = Path(UPLOAD_DIR)

    notebooks = []
    total_notebook_bytes = 0
    total_version_bytes = 0
    total_version_count = 0

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if tag and tag not in _read_notebook_tags(entry.name):
            continue

        notebook_bytes = entry.stat().st_size

        versions_dir = _notebook_versions_dir(entry.name)

        version_files = (
            [f for f in versions_dir.iterdir() if f.is_file()]
            if versions_dir.is_dir() else []
        )

        version_bytes = sum(f.stat().st_size for f in version_files)
        version_count = len(version_files)

        notebooks.append({
            "filename": entry.name,
            "notebook_bytes": notebook_bytes,
            "version_bytes": version_bytes,
            "version_count": version_count,
            "total_bytes": notebook_bytes + version_bytes,
        })

        total_notebook_bytes += notebook_bytes
        total_version_bytes += version_bytes
        total_version_count += version_count

    notebooks.sort(key=lambda entry: entry["total_bytes"], reverse=True)

    notebook_count = len(notebooks)

    paginated_notebooks = (
        notebooks[offset:offset + limit] if limit is not None else notebooks[offset:]
    )

    return {
        "status": "success",
        "notebooks": paginated_notebooks,
        "notebook_count": notebook_count,
        "limit": limit,
        "offset": offset,
        "total_notebook_bytes": total_notebook_bytes,
        "total_version_bytes": total_version_bytes,
        "total_version_count": total_version_count,
        "total_bytes": total_notebook_bytes + total_version_bytes,
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

    Also removes each deleted notebook's tags sidecar file, description
    sidecar file, and version history directory, if it has any of them
    (see _tags_sidecar_path, _description_sidecar_path, and
    _notebook_versions_dir above) -- without this, a notebook re-uploaded
    later under the same filename would silently inherit tags or a
    description, or gain "previous versions" to restore, left behind by a
    completely different, previously-deleted notebook that just happened
    to share its name.
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
        _tags_sidecar_path(entry.name).unlink(missing_ok=True)
        _description_sidecar_path(entry.name).unlink(missing_ok=True)
        shutil.rmtree(_notebook_versions_dir(entry.name), ignore_errors=True)
        deleted_filenames.append(entry.name)

    return {
        "status": "success",
        "deleted_count": len(deleted_filenames),
        "deleted_filenames": deleted_filenames,
        "currently_compiled_notebook_deleted": currently_compiled_notebook_deleted,
    }


@router.delete("/notebooks/versions")
def prune_all_notebook_versions(
    older_than_days: int = None, tag: str = None, dry_run: bool = False
):
    """Permanently discard every notebook's snapshotted versions older
    than "older_than_days" days, across the whole catalog at once,
    without touching any notebook's own current content, tags, or
    currently-compiled status.

    DELETE /api/notebooks/{filename}/versions already clears one
    notebook's *entire* version history in one call, and
    _prune_notebook_versions (above) already trims the oldest snapshots
    automatically once a single notebook accumulates more than
    MAX_NOTEBOOK_VERSIONS -- but neither helps an operator who wants to
    reclaim space from *old* snapshots specifically, across *every*
    notebook, while still keeping each notebook's own recent history
    intact: that meant GET .../versions per notebook to find every
    version_id old enough to discard, then one DELETE
    .../versions/{version_id} call per stale snapshot -- mirroring
    exactly the gap DELETE /api/notebooks/delete-batch already closed for
    deleting several *notebooks* at once, just applied one level down, to
    every notebook's own version history, and age-based rather than
    all-or-nothing the way DELETE /api/notebooks/{filename}/versions is.

    A version's own age is its "saved_at" -- the exact same on-disk
    mtime GET /api/notebooks/{filename}/versions already reports for each
    entry (see list_notebook_versions above) -- so a version reported
    there as, say, "saved 40 days ago" is exactly what "older_than_days":
    30 would catch here; nothing about what counts as a version's age is
    redefined for this endpoint.

    Held per-notebook under the same _version_lock_for
    restore_notebook_version's own snapshot-then-copy sequence already
    uses, for the identical reason: without it, this could delete a
    version file a concurrent restore is mid-snapshot into, or race
    another concurrent prune of the same notebook.

    A notebook with no version history at all, or none old enough to
    prune, simply contributes nothing to "results" -- an empty
    "results" list is still a valid (if uneventful) outcome of a bulk
    operation like this, not an error, the same "nothing to act on is
    still a valid outcome" reasoning DELETE /api/tags/{tag}'s own empty
    "affected_notebooks" already follows.

    "tag" (optional) scopes the prune to only notebooks currently
    carrying that exact tag, the same exact-match GET /api/notebooks?tag=
    already filters by -- an operator wanting to reclaim old snapshots
    from just, say, its "scratch" notebooks (without touching a
    "production" notebook's own older-but-still-wanted history) had no
    way to ask for that short of pruning the entire catalog. Applied
    before a notebook's version directory is ever touched, so an
    out-of-tag notebook's own version history is left untouched and
    never contributes to "results" or total_deleted_count.

    "dry_run" (optional, default false) reports the exact same "results"
    a real prune would -- which notebooks are affected, each one's own
    "deleted_version_ids"/"deleted_count" -- without deleting a single
    version file, the identical preview POST
    /api/notebooks/duplicates/resolve's own "dry_run" already provides
    for that endpoint's own irreversible batch deletion. This prune is
    permanent and catalog-wide (age- and, with "tag", category-scoped,
    but with no per-version undo), so an operator wanting to check
    "older_than_days"/"tag" actually catch what they expect -- before any
    version is gone for good -- had no safe way to preview that first
    short of a separate GET .../versions per notebook and working out
    each entry's own age by hand. The top-level response's own "dry_run"
    field echoes back whether this call actually deleted anything.
    """

    if older_than_days is None or older_than_days <= 0:

        raise HTTPException(
            status_code=400,
            detail="older_than_days is required and must be a positive integer"
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    upload_root = Path(UPLOAD_DIR)

    results = []
    total_deleted_count = 0

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if tag and tag not in _read_notebook_tags(entry.name):
            continue

        versions_dir = _notebook_versions_dir(entry.name)

        if not versions_dir.is_dir():
            continue

        with _version_lock_for(entry.name):

            deleted_version_ids = []

            for version_file in versions_dir.iterdir():

                if not version_file.is_file():
                    continue

                saved_at = datetime.fromtimestamp(
                    version_file.stat().st_mtime, tz=timezone.utc
                )

                if saved_at < cutoff:
                    if not dry_run:
                        version_file.unlink()
                    deleted_version_ids.append(version_file.name)

        if deleted_version_ids:

            deleted_version_ids.sort()

            results.append({
                "filename": entry.name,
                "deleted_version_ids": deleted_version_ids,
                "deleted_count": len(deleted_version_ids),
            })
            total_deleted_count += len(deleted_version_ids)

    return {
        "status": "success",
        "dry_run": dry_run,
        "older_than_days": older_than_days,
        "results": results,
        "notebook_count_affected": len(results),
        "total_deleted_count": total_deleted_count,
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

    Also removes this notebook's tags sidecar file, description sidecar
    file, and version history directory, if it has any of them -- see
    delete_all_notebooks' own identical cleanup above for why any one
    left behind must not silently carry over to a future notebook
    re-uploaded under the same filename.
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
        _tags_sidecar_path(file_path.name).unlink(missing_ok=True)
        _description_sidecar_path(file_path.name).unlink(missing_ok=True)
        shutil.rmtree(_notebook_versions_dir(file_path.name), ignore_errors=True)

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


@router.post("/notebooks/delete-batch")
def delete_notebooks_batch(data: dict):
    """Delete a caller-chosen set of already-uploaded notebooks in one
    call.

    DELETE /api/notebooks/{filename} already deletes one notebook, and
    DELETE /api/notebooks (with "?confirm=true") deletes literally every
    uploaded notebook -- but there was nothing in between: discarding a
    handful of specific notebooks (e.g. everything a preceding GET
    /api/notebooks?search=... turned up as stale scratch work) meant
    calling DELETE /api/notebooks/{filename} once per filename, or reaching
    for DELETE /api/notebooks and losing every *other* uploaded notebook
    along with them.

    Deliberately takes an explicit "filenames" list rather than a
    search/tag filter, the same reasoning POST /api/tags/{tag}/apply's own
    docstring already gives for its identical "filenames" body field: the
    caller already knows exactly which notebooks to remove, typically from
    a preceding GET /api/notebooks?search=... of its own, and an explicit
    list keeps this endpoint's effect fully predictable from its own
    request body alone.

    Reuses the exact per-file "one bad entry doesn't abort the batch"
    contract POST /api/upload/batch and POST /api/tags/{tag}/apply already
    established: each filename is processed independently, and "results"
    reports one {"filename", "status", ...} entry per filename --
    "success" (with that notebook's own "was_currently_compiled" flag, the
    same field DELETE /api/notebooks/{filename} already returns) or
    "error" (with the HTTPException detail that filename's own single-file
    delete would have raised on its own, e.g. a 404 for a filename that
    doesn't exist). The response is always 200 -- the batch request itself
    was handled, even if every filename in it failed -- with
    "succeeded_count"/"failed_count" summarizing "results" the same way
    those two endpoints' own identical fields already do.

    Also removes each successfully-deleted notebook's tags sidecar file,
    description sidecar file, and version history directory, exactly like
    DELETE /api/notebooks/{filename} and DELETE /api/notebooks already do
    -- a notebook re-uploaded later under the same filename must not
    silently inherit any of them left behind by this bulk delete.

    "dry_run" (optional, default false, in the request body) reports the
    exact same "results" a real batch would -- each filename's own
    "success" (with "was_currently_compiled") or "error" (with the same
    detail a real delete would raise, e.g. a 404 for a typo'd filename)
    -- without deleting anything at all, the identical preview POST
    /api/notebooks/duplicates/resolve's and DELETE
    /api/notebooks/versions's own "dry_run" already provide for their own
    irreversible batch operations. A caller building "filenames" from a
    preceding GET /api/notebooks?search=... had no way to confirm that
    list actually matches what they intended -- or catch a typo'd
    filename before it's reported as an error only *after* every other
    filename in the same batch was already deleted for good -- short of
    checking each one against a separate GET /api/notebooks/{filename}
    first. The top-level response's own "dry_run" field echoes back
    whether this call actually deleted anything.
    """

    filenames = data.get("filenames")

    if (
        not isinstance(filenames, list)
        or not filenames
        or not all(isinstance(f, str) for f in filenames)
    ):
        raise HTTPException(
            status_code=400,
            detail="filenames must be a non-empty list of strings"
        )

    dry_run = bool(data.get("dry_run", False))

    compiled_path, _, _ = _currently_compiled_notebook_metadata()

    results = []
    succeeded_count = 0
    failed_count = 0

    for filename in filenames:

        try:

            file_path = resolve_upload_path(filename)

            if not file_path.is_file():

                raise HTTPException(
                    status_code=404,
                    detail="Notebook file not found"
                )

            was_currently_compiled = (
                compiled_path is not None and file_path.resolve() == compiled_path
            )

            if not dry_run:
                os.remove(file_path)
                _tags_sidecar_path(file_path.name).unlink(missing_ok=True)
                _description_sidecar_path(file_path.name).unlink(missing_ok=True)
                shutil.rmtree(_notebook_versions_dir(file_path.name), ignore_errors=True)

            results.append({
                "filename": filename,
                "status": "success",
                "was_currently_compiled": was_currently_compiled,
            })
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "dry_run": dry_run,
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
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


@router.get("/notebooks/{filename}/info")
def get_notebook_info(filename: str):
    """Return one previously uploaded notebook's own metadata -- the
    exact entry GET /api/notebooks' own "notebooks" list already returns
    for it (filename, size_bytes, modified_at, currently_compiled, tags,
    and, only when it's the currently-compiled notebook,
    notebook_changed_since_compile/compiled_at) -- without fetching or
    filtering the entire list just to find one notebook's own record.

    GET /api/notebooks/{filename} already exists, but returns the
    notebook's raw *content* (a file download), not its metadata -- the
    same distinction GET /api/notebooks/{filename}/tags already draws
    for tags specifically. A caller wanting to know, say, whether one
    particular notebook is the one currently backing GENERATED_DIR (and
    whether it's since drifted from that compile) had to fetch every
    uploaded notebook via an unfiltered GET /api/notebooks and search the
    response for a matching "filename" itself -- wasteful, and requiring
    the exact same "search" trick GET /api/notebooks/{filename}/tags'
    own docstring already rejected for a single notebook's tags, applied
    here for its metadata as a whole instead.

    Reuses _notebook_metadata_entry -- the same helper list_notebooks
    itself now calls -- so this can never drift from what the identical
    notebook's own entry in that list already reports.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    compiled_path, compiled_sha256, compiled_at = _currently_compiled_notebook_metadata()

    return {
        "status": "success",
        **_notebook_metadata_entry(file_path, compiled_path, compiled_sha256, compiled_at),
    }


@router.post("/notebooks/info-batch")
def get_notebooks_info_batch(data: dict):
    """Return several previously uploaded notebooks' own metadata --
    each the exact entry GET /api/notebooks/{filename}/info already
    returns for it -- in one call.

    GET /api/notebooks/{filename}/info already avoids fetching or
    filtering the *entire* catalog just to find one notebook's own
    record, but a caller that already has a specific *set* of filenames
    in hand -- e.g. from a preceding GET /api/functions or GET
    /api/notebooks/search-content match, or GET /api/notebooks/duplicates'
    own "filenames" for one group -- had no way to fetch all of their
    metadata in one round trip either: it was back to one GET
    .../{filename}/info call per name, or falling back to the same
    unfiltered GET /api/notebooks that single-notebook endpoint's own
    docstring already calls wasteful for exactly one name.

    Deliberately takes an explicit "filenames" list rather than a
    search/tag filter, the same reasoning POST /api/tags/{tag}/apply's
    own docstring already gives for its identical "filenames" body field:
    the caller already knows exactly which notebooks it wants, and an
    explicit list keeps this endpoint's own result fully predictable from
    its own request body alone.

    Reuses the exact per-file "one bad entry doesn't abort the batch"
    contract POST /api/upload/batch and POST /api/tags/{tag}/apply already
    established: each filename is looked up independently, and "results"
    reports one {"filename", "status", ...} entry per filename --
    "success" (with that notebook's own full metadata entry, from the
    same _notebook_metadata_entry helper GET /api/notebooks and GET
    /api/notebooks/{filename}/info themselves already share, so this can
    never drift from either) or "error" (a 404-equivalent detail for a
    filename that doesn't exist). The response is always 200 -- the batch
    request itself was handled, even if every filename in it failed --
    with "succeeded_count"/"failed_count" summarizing "results" the same
    way those two endpoints' own identical fields already do.
    """

    filenames = data.get("filenames")

    if (
        not isinstance(filenames, list)
        or not filenames
        or not all(isinstance(f, str) for f in filenames)
    ):
        raise HTTPException(
            status_code=400,
            detail="filenames must be a non-empty list of strings"
        )

    compiled_path, compiled_sha256, compiled_at = _currently_compiled_notebook_metadata()

    results = []
    succeeded_count = 0
    failed_count = 0

    for filename in filenames:

        try:

            file_path = resolve_upload_path(filename)

            if not file_path.is_file():

                raise HTTPException(
                    status_code=404,
                    detail="Notebook file not found"
                )

            results.append({
                "status": "success",
                **_notebook_metadata_entry(
                    file_path, compiled_path, compiled_sha256, compiled_at
                ),
            })
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


# Serializes rename_notebook's own check-then-write critical section per
# *destination* filename within UPLOAD_DIR -- see _rename_lock_for's own
# docstring below for why this exists. A plain threading.Lock, not an
# asyncio.Lock like upload_notebook's own _upload_lock_for (routes/
# upload.py): rename_notebook is a plain `def`, not `async def` (see
# test_blocking_endpoints_are_declared_as_plain_def_not_async_def), so
# FastAPI runs concurrent calls to it in its worker threadpool -- genuine
# OS-thread parallelism, not single-event-loop cooperative scheduling --
# and an asyncio.Lock's `acquire()` isn't safe to call from a plain,
# non-async function running outside any event loop at all.
_rename_locks_by_filename = {}


def _rename_lock_for(filename: str) -> threading.Lock:
    """threading.Lock scoped to a single *destination* filename within
    UPLOAD_DIR, created lazily and kept for the lifetime of this process.

    Without this, two concurrent PATCH /api/notebooks/{filename} requests
    renaming two different notebooks to the same new_filename raced this
    endpoint's own check-then-write sequence -- confirmed exploitable,
    reproduced directly: two threads renaming two different existing
    notebooks to the same brand-new destination filename, fired against a
    live server in a tight loop, produced two 200s (no 409 from either)
    in 19 of 20 trials -- far more reliably than the identical class of
    bug in upload_notebook (see _upload_lock_for above), precisely
    because this endpoint's genuine OS-thread parallelism (see this
    variable's own comment above) makes the two requests' interleaving
    far less timing-sensitive than upload_notebook's single-event-loop
    cooperative scheduling. Worse still, this endpoint had no re-check at
    all immediately before its own os.replace() -- unlike upload_notebook,
    which at least re-checked right before its swap (closing the
    *sequential* case, if not the concurrent one) -- so this collision was
    even easier to hit than upload's was before its own fix: one rename
    silently clobbers the other's just-renamed file, and *both* callers
    see "status": "success", directly contradicting this endpoint's own
    explicit contract (see rename_notebook's own docstring: renaming onto
    an existing filename is supposed to be rejected with 409 unless the
    caller opts in via "overwrite": true).

    Scoped per destination filename, not a single global lock: two
    concurrent renames landing on two *different* destination filenames
    -- the overwhelmingly common case -- must stay fully concurrent; only
    genuinely colliding renames need to serialize at all.

    Never removed once created -- same deliberately simple tradeoff
    _upload_lock_for's own docstring already explains for the identical
    pattern there.
    """
    return _rename_locks_by_filename.setdefault(filename, threading.Lock())


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
    unless the caller passes "overwrite": true -- held for the whole
    check-then-write sequence by _rename_lock_for (see its own docstring
    for the concurrent collision this closes, keyed by destination
    filename).

    A notebook's tags (see PUT /api/notebooks/{filename}/tags) and its
    description (see PUT /api/notebooks/{filename}/description) both move
    along with it: without this, a rename would silently orphan the old
    filename's sidecar file(s) (never read again -- nothing looks up
    tags or a description by a name that no longer exists) while the
    notebook's new name read back as untagged/undescribed. If "overwrite":
    true replaces an existing destination notebook, that destination's
    own previous tags and description are discarded along with the rest
    of the file it belonged to, rather than left to be silently inherited
    by the just-renamed notebook.

    A notebook's version history (see PUT /api/upload?overwrite=true and
    GET/POST /api/notebooks/{filename}/versions[/{version_id}[/restore]])
    moves along with it for the identical reason -- otherwise a rename
    would silently strand a notebook's own undo history under a filename
    nothing points at anymore, while its new name reads back as never
    having been overwritten at all. The same overwrite semantics apply:
    the destination's own previous version history, if any, is discarded
    rather than merged with the just-renamed notebook's.
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
        # the same file). No locking needed either: nothing is written.
        return {
            "status": "success",
            "filename": filename,
            "new_filename": new_filename,
            "was_currently_compiled": False,
        }

    with _rename_lock_for(new_path.name):

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

        old_tags_path = _tags_sidecar_path(old_path.name)
        new_tags_path = _tags_sidecar_path(new_path.name)

        if old_tags_path.is_file():
            os.replace(old_tags_path, new_tags_path)
        else:
            new_tags_path.unlink(missing_ok=True)

        old_description_path = _description_sidecar_path(old_path.name)
        new_description_path = _description_sidecar_path(new_path.name)

        if old_description_path.is_file():
            os.replace(old_description_path, new_description_path)
        else:
            new_description_path.unlink(missing_ok=True)

        old_versions_dir = _notebook_versions_dir(old_path.name)
        new_versions_dir = _notebook_versions_dir(new_path.name)

        if new_versions_dir.exists():
            shutil.rmtree(new_versions_dir)

        if old_versions_dir.is_dir():
            shutil.move(str(old_versions_dir), str(new_versions_dir))

        if was_currently_compiled:

            # Held for the same reason every other read/write of
            # .compile_metadata.json already does (see COMPILE_LOCK in
            # backend/compiler.py): without it, a concurrent POST
            # /api/compile racing this rename could write a fresh
            # .compile_metadata.json for an unrelated notebook and have
            # this call immediately clobber it with the just-renamed path
            # instead.
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


@router.post("/notebooks/{filename}/copy")
def copy_notebook(filename: str, data: dict):
    """Duplicate a previously uploaded notebook under a new filename,
    leaving the source notebook (and whatever it currently backs in
    GENERATED_DIR) completely untouched.

    Before this, the only way to get an independent, editable variant of
    an uploaded notebook -- e.g. to try a risky change without touching a
    known-good "production" copy, or to start a new notebook from an
    existing one as a template -- was to download it and re-upload it
    under a new name by hand. PATCH /api/notebooks/{filename} (rename)
    doesn't help here: it moves the one notebook it operates on, it
    doesn't leave a second copy behind.

    Same "new_filename ends in .ipynb"/"reject a same-named collision
    unless 'overwrite': true is passed" validation as rename_notebook
    above, and reuses its own _rename_lock_for for the identical
    destination-filename check-then-write race that closes -- copying
    onto a destination is exactly the same kind of check-then-write as
    renaming onto one, just via shutil.copy2 instead of os.replace, and
    with the source left behind afterward instead of gone.

    Unlike a rename, copying `filename` onto itself is never a
    meaningful no-op -- there's no "new" copy to speak of -- so it's
    rejected with 400 rather than silently succeeding.

    A notebook's tags (see PUT /api/notebooks/{filename}/tags) and its
    description (see PUT /api/notebooks/{filename}/description) are both
    copied along with it: they describe the notebook's *content* (e.g.
    "production", "v2", "quarterly churn model"), and the copy starts out
    as an identical copy of that content, so inheriting them is more
    useful than the copy silently reading back as untagged/undescribed.
    Its version history (see GET/POST
    /api/notebooks/{filename}/versions[/{version_id}[/restore]]) is
    deliberately NOT copied, though -- that history belongs to
    `filename`'s own past overwrites, not to content in the abstract, and
    the new copy has no overwrites of its own yet to have a history of.
    If "overwrite": true replaces an existing destination notebook, that
    destination's own previous tags, description, and version history are
    discarded along with the rest of the file it belonged to, the same
    overwrite semantics rename_notebook already applies.

    Never touches .compile_metadata.json: the source notebook's own
    identity (and whatever it currently backs in GENERATED_DIR, if
    anything) doesn't change at all just because a copy of it now exists
    elsewhere. If "overwrite": true happens to replace the notebook
    currently backing GENERATED_DIR with a copy of a *different*
    notebook's content, GET /api/notebooks' own existing
    "notebook_changed_since_compile" staleness check (a content-hash
    comparison, not an identity one) already reports that correctly with
    no special-casing needed here -- the same way it already reports
    staleness for a currently-compiled notebook edited via POST
    /api/upload?overwrite=true.
    """

    overwrite = bool(data.get("overwrite", False))

    source_path = resolve_upload_path(filename)

    if not source_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    new_filename = _copy_notebook_to(source_path, data.get("new_filename"), overwrite)

    return {
        "status": "success",
        "filename": filename,
        "new_filename": new_filename,
    }


def _copy_notebook_to(source_path: Path, new_filename, overwrite: bool) -> str:
    """Copy `source_path` (an already-verified existing notebook file) to
    `new_filename` within UPLOAD_DIR, applying the exact same validation,
    overwrite semantics, and tag inheritance copy_notebook's own
    docstring above documents.

    Factored out of copy_notebook so POST
    /api/notebooks/{filename}/copy-batch (below) can reuse it once per
    destination instead of a second, inevitably-drifting copy of this
    logic -- copy_notebook itself now just calls this once, unchanged
    behavior for its own single-destination caller except that a request
    combining a missing source *and* an invalid new_filename now reports
    the 404 (checked by copy_notebook before calling this) rather than
    the 400 this raises -- not otherwise observable, since no caller
    depends on which of two independently-wrong things about the same
    request gets reported first.

    Raises the identical HTTPException copy_notebook always raised for
    each failure mode. copy_notebook_batch instead catches that
    HTTPException per destination, so one bad destination in a batch
    doesn't abort every other copy. Returns the validated new_filename on
    success.
    """

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

    dest_path = resolve_upload_path(new_filename)

    if dest_path == source_path:

        raise HTTPException(
            status_code=400,
            detail="new_filename must be different from filename"
        )

    with _rename_lock_for(dest_path.name):

        if dest_path.exists() and not overwrite:

            raise HTTPException(
                status_code=409,
                detail=(
                    f"A notebook named '{new_filename}' already exists. "
                    'Pass "overwrite": true to replace it.'
                )
            )

        try:

            shutil.copy2(source_path, dest_path)

        except OSError as e:

            raise HTTPException(
                status_code=500,
                detail=str(e)
            )

        dest_versions_dir = _notebook_versions_dir(dest_path.name)

        if dest_versions_dir.exists():
            shutil.rmtree(dest_versions_dir)

        _write_notebook_tags(dest_path.name, _read_notebook_tags(source_path.name))
        _write_notebook_description(
            dest_path.name, _read_notebook_description(source_path.name)
        )

    return new_filename


@router.post("/notebooks/{filename}/copy-batch")
def copy_notebook_batch(filename: str, data: dict):
    """Duplicate a previously uploaded notebook under several new
    filenames in one call, leaving the source notebook (and whatever it
    currently backs in GENERATED_DIR) completely untouched.

    POST /api/notebooks/{filename}/copy already duplicates a notebook
    under one new name -- exactly right for that, but seeding several
    variants from the same known-good template at once (e.g. a handful
    of per-customer demo notebooks from one template, or a batch of
    named starting points for a workshop) meant calling it once per
    desired name, each a separate round trip. Unlike POST
    /api/notebooks/delete-batch, POST /api/tags/{tag}/apply, and POST
    /api/notebooks/info-batch -- each of which fan a request out across
    several *source* notebooks named in its own "filenames" list -- this
    is the mirror shape: one fixed source (`filename`, from the URL path,
    same as the single-destination POST /api/notebooks/{filename}/copy
    already uses) fanned out across several *destinations* named in
    "new_filenames" instead.

    Reuses _copy_notebook_to (above) -- the exact same validation,
    overwrite semantics, and tag inheritance the single-destination
    POST /api/notebooks/{filename}/copy itself now calls too -- once per
    destination, so a notebook copied this way is indistinguishable from
    one copied individually. Follows the identical per-entry "one bad
    entry doesn't abort the batch" contract those three endpoints already
    established: each destination is attempted independently, and
    "results" reports one {"new_filename", "status", ...} entry per
    destination -- "success" or "error" (the HTTPException detail that
    destination's own single-copy call would have raised on its own,
    e.g. 409 for a same-name collision without "overwrite": true). The
    response is always 200 -- the batch request itself was handled, even
    if every destination in it failed -- with "succeeded_count"/
    "failed_count" summarizing "results" the same way those endpoints'
    own identical fields already do.

    "overwrite" applies uniformly to every destination, the same single
    flag POST /api/notebooks/{filename}/copy itself takes -- there's no
    per-destination override.
    """

    new_filenames = data.get("new_filenames")

    if (
        not isinstance(new_filenames, list)
        or not new_filenames
        or not all(isinstance(f, str) for f in new_filenames)
    ):
        raise HTTPException(
            status_code=400,
            detail="new_filenames must be a non-empty list of strings"
        )

    overwrite = bool(data.get("overwrite", False))

    source_path = resolve_upload_path(filename)

    if not source_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    results = []
    succeeded_count = 0
    failed_count = 0

    for new_filename in new_filenames:

        try:

            _copy_notebook_to(source_path, new_filename, overwrite)

            results.append({
                "new_filename": new_filename,
                "status": "success",
            })
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "new_filename": new_filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "filename": filename,
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.get("/notebooks/{filename}/tags")
def get_notebook_tags(filename: str):
    """Return the tags currently recorded for a previously uploaded
    notebook.

    GET /api/notebooks already lists every notebook's "tags" field
    alongside its other metadata, but had no equivalent for a single
    notebook without re-fetching (and re-filtering) the entire list --
    the same gap GET /api/notebooks/{filename} already closes for a
    notebook's raw content, just for its tags instead.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    return {
        "status": "success",
        "filename": filename,
        "tags": _read_notebook_tags(file_path.name),
    }


@router.put("/notebooks/{filename}/tags")
def set_notebook_tags(filename: str, data: dict):
    """Replace the full set of tags recorded for a previously uploaded
    notebook.

    Before this, there was no way to categorize or group uploaded
    notebooks at all beyond their filename -- GET /api/notebooks' own
    "search" only ever matches a substring of it. A dashboard with many
    uploaded notebooks (production ones, scratch experiments, notebooks
    for a specific project, ...) had no way to label and later filter by
    that beyond renaming files to encode it, which collides with
    filenames already being how a notebook's own identity is tracked
    elsewhere in this file (currently_compiled, .compile_metadata.json's
    "source_notebook", ...).

    A PUT, not a PATCH that adds/removes individual tags: this always
    replaces the notebook's entire tag set with "tags" from the request
    body, the simplest contract for a caller to reason about ("this is
    now the complete list") without needing separate add/remove
    endpoints. Pass an empty list to clear every tag.

    "tags" must be a list of non-empty, non-whitespace-only strings (see
    _validate_and_normalize_tags), each at most _MAX_TAG_LENGTH
    characters, with at most _MAX_TAGS_PER_NOTEBOOK distinct tags in
    total -- an invalid "tags" value is rejected with 400, the same way
    /api/deploy's own "tag" (a Docker image tag, an unrelated concept)
    and "platform" fields already are elsewhere in this file for a
    non-string value.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    tags = _validate_and_normalize_tags(data.get("tags", []))

    _write_notebook_tags(file_path.name, tags)

    return {
        "status": "success",
        "filename": filename,
        "tags": tags,
    }


@router.post("/notebooks/tags-batch")
def set_notebook_tags_batch(data: dict):
    """Replace the full tag set for a caller-chosen set of already-
    uploaded notebooks in one call, each notebook getting its own
    explicit tags.

    PUT /api/notebooks/{filename}/tags already replaces one notebook's
    entire tag set at a time, and POST /api/tags/{tag}/apply/POST
    /api/tags/{tag}/remove already add or remove a single *shared* tag
    across several notebooks at once -- but neither helps a caller
    re-establishing a whole tagging scheme across many notebooks in one
    pass (e.g. importing a filename->tags mapping from an external
    source, or restoring a previously exported one), where each notebook
    needs its *own*, potentially completely different, tag set replaced
    all at once. Before this, that meant one PUT
    /api/notebooks/{filename}/tags call per notebook -- no way to submit
    the whole mapping in a single request.

    Takes "entries", a list of {"filename", "tags"} objects rather than
    the single shared "tag" POST /api/tags/{tag}/apply/remove take, or the
    single shared "filenames" POST /api/notebooks/delete-batch and POST
    /api/notebooks/info-batch take -- each entry here carries its own
    independent value, the same shape restore/replace-many-different-
    things-at-once operations need that a single shared value/filter
    can't express.

    "entries" itself (a non-empty list, each element an object with a
    string "filename") is validated once, up front, as a 400 covering the
    whole request -- a malformed *shape* is this request's own fault, not
    any one entry's. Each entry's own "tags", though, is validated
    independently via _validate_and_normalize_tags (the exact same
    per-tag rules PUT .../tags already enforces) as part of that entry's
    own per-entry result, since -- unlike POST /api/tags/{tag}/apply's
    single shared tag string -- a bad "tags" value here belongs to
    exactly the one entry that supplied it, not the batch as a whole.
    Reuses the identical per-entry "one bad entry doesn't abort the
    batch" contract those endpoints already established: "results"
    reports one {"filename", "status", ...} entry per input entry --
    "success" (with that notebook's resulting tag set) or "error" (a 404
    for an unknown filename, or the same 400 a bad "tags" value alone
    would raise through PUT .../tags directly).
    """

    entries = data.get("entries")

    if not isinstance(entries, list) or not entries:

        raise HTTPException(
            status_code=400,
            detail="entries must be a non-empty list of objects"
        )

    for entry in entries:

        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):

            raise HTTPException(
                status_code=400,
                detail="each entry must be an object with a string 'filename'"
            )

    results = []
    succeeded_count = 0
    failed_count = 0

    for entry in entries:

        filename = entry["filename"]

        try:

            file_path = resolve_upload_path(filename)

            if not file_path.is_file():

                raise HTTPException(
                    status_code=404,
                    detail="Notebook file not found"
                )

            tags = _validate_and_normalize_tags(entry.get("tags", []))

            _write_notebook_tags(file_path.name, tags)

            results.append({
                "filename": filename,
                "status": "success",
                "tags": tags,
            })
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.get("/notebooks/{filename}/description")
def get_notebook_description(filename: str):
    """Return the freeform description currently recorded for a
    previously uploaded notebook, or "" if it has none.

    Tags (GET/PUT .../tags, just above) are good for categorizing and
    filtering a catalog of notebooks -- "production", "v2" -- but say
    nothing about *why* a given notebook exists, what it's for, or
    whether it's still needed. Before this, the only place that context
    could live was the notebook's own filename or its cells' own markdown
    -- neither of which is visible from GET /api/notebooks or any other
    listing endpoint without downloading and opening the notebook itself.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    return {
        "status": "success",
        "filename": filename,
        "description": _read_notebook_description(file_path.name),
    }


@router.put("/notebooks/{filename}/description")
def set_notebook_description(filename: str, data: dict):
    """Replace the freeform description recorded for a previously
    uploaded notebook.

    A PUT, not a PATCH that appends to or edits existing text: this
    always replaces the notebook's entire description with "description"
    from the request body, the same full-replace contract PUT
    .../tags already establishes for a notebook's tags, for the identical
    reason -- the simplest contract for a caller to reason about ("this
    is now the complete description") without a separate append/clear
    endpoint. Pass "" (or omit "description" entirely) to clear it.

    "description" must be a string, at most _MAX_DESCRIPTION_LENGTH
    characters after stripping leading/trailing whitespace -- an invalid
    value is rejected with 400, the same way PUT .../tags' own "tags"
    field already is for a non-list value.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    description = data.get("description", "")

    if not isinstance(description, str):

        raise HTTPException(
            status_code=400,
            detail="description must be a string"
        )

    description = description.strip()

    if len(description) > _MAX_DESCRIPTION_LENGTH:

        raise HTTPException(
            status_code=400,
            detail=(
                f"description must be at most {_MAX_DESCRIPTION_LENGTH} "
                "characters long"
            )
        )

    _write_notebook_description(file_path.name, description)

    return {
        "status": "success",
        "filename": filename,
        "description": description,
    }


@router.post("/notebooks/description-batch")
def set_notebook_description_batch(data: dict):
    """Replace the description for a caller-chosen set of already-
    uploaded notebooks in one call, each notebook getting its own
    explicit description.

    PUT /api/notebooks/{filename}/description already replaces one
    notebook's description at a time, and POST /api/notebooks/tags-batch
    already does the analogous "several notebooks, each its own value"
    replacement for tags -- but there was no equivalent for descriptions,
    so re-establishing descriptions across many notebooks at once (e.g.
    importing a filename->description mapping from an external source, or
    restoring a previously exported one) meant one PUT .../description
    call per notebook.

    Takes "entries", a list of {"filename", "description"} objects, the
    identical shape POST /api/notebooks/tags-batch takes for the same
    reason: each entry here carries its own independent value, unlike the
    single shared "tag" POST /api/tags/{tag}/apply/remove take.

    "entries" itself (a non-empty list, each element an object with a
    string "filename") is validated once, up front, as a 400 covering the
    whole request. Each entry's own "description", though, is validated
    independently -- must be a string, at most _MAX_DESCRIPTION_LENGTH
    characters after stripping -- as part of that entry's own per-entry
    result, since a bad "description" value here belongs to exactly the
    one entry that supplied it, not the batch as a whole. Reuses the
    identical per-entry "one bad entry doesn't abort the batch" contract
    POST /api/notebooks/tags-batch already established: "results" reports
    one {"filename", "status", ...} entry per input entry -- "success"
    (with that notebook's resulting description) or "error" (a 404 for an
    unknown filename, or the same 400 a bad "description" value alone
    would raise through PUT .../description directly). Omitting
    "description" from an entry clears it, the same default PUT
    .../description itself already applies.
    """

    entries = data.get("entries")

    if not isinstance(entries, list) or not entries:

        raise HTTPException(
            status_code=400,
            detail="entries must be a non-empty list of objects"
        )

    for entry in entries:

        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):

            raise HTTPException(
                status_code=400,
                detail="each entry must be an object with a string 'filename'"
            )

    results = []
    succeeded_count = 0
    failed_count = 0

    for entry in entries:

        filename = entry["filename"]

        try:

            file_path = resolve_upload_path(filename)

            if not file_path.is_file():

                raise HTTPException(
                    status_code=404,
                    detail="Notebook file not found"
                )

            description = entry.get("description", "")

            if not isinstance(description, str):

                raise HTTPException(
                    status_code=400,
                    detail="description must be a string"
                )

            description = description.strip()

            if len(description) > _MAX_DESCRIPTION_LENGTH:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"description must be at most {_MAX_DESCRIPTION_LENGTH} "
                        "characters long"
                    )
                )

            _write_notebook_description(file_path.name, description)

            results.append({
                "filename": filename,
                "status": "success",
                "description": description,
            })
            succeeded_count += 1

        except HTTPException as exc:

            results.append({
                "filename": filename,
                "status": "error",
                "detail": exc.detail,
            })
            failed_count += 1

    return {
        "status": "success",
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.get("/notebooks/{filename}/versions")
def list_notebook_versions(filename: str, limit: int = None, offset: int = 0):
    """List a previously uploaded notebook's snapshotted previous
    versions, newest first.

    Every overwrite of `filename` (POST /api/upload?overwrite=true) now
    snapshots the content it's about to replace (see
    _snapshot_current_notebook_version above) rather than destroying it
    outright -- but before this endpoint, there was no way to see what had
    actually been captured, or with what version_id to pass to GET/POST
    below.

    "limit"/"offset" page the returned "versions" the identical way GET
    /api/notebooks' own "limit"/"offset" already page the notebook
    catalog: before this, a notebook whose own NOTEBOOK_API_MAX_VERSIONS_PER_NOTEBOOK
    (MAX_NOTEBOOK_VERSIONS above) had been raised well past its default
    always returned every one of its snapshots in a single response, with
    no way for a caller -- e.g. a dashboard frontend rendering one page of
    "versions list" at a time -- to fetch only a slice of an otherwise
    long-lived notebook's own history. "offset" (default 0) skips this
    many of the newest-first entries before "limit" is applied; a
    negative "offset" is rejected with 400, the same way GET
    /api/notebooks' own negative "offset" already is. "limit" (optional)
    caps how many entries follow; omitted, every remaining version after
    "offset" is returned exactly as before. "total_count" reports how many
    versions exist in total (before "offset"/"limit" are applied), so a
    caller can tell whether it has reached the end of a notebook's own
    history without a separate, unpaginated request.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    if limit is not None and limit <= 0:

        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    versions = []

    if versions_dir.is_dir():

        for entry in sorted(versions_dir.iterdir(), reverse=True):

            if not entry.is_file():
                continue

            entry_stat = entry.stat()

            versions.append({
                "version_id": entry.name,
                "size_bytes": entry_stat.st_size,
                "saved_at": datetime.fromtimestamp(
                    entry_stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })

    total_count = len(versions)

    paginated_versions = (
        versions[offset:offset + limit] if limit is not None else versions[offset:]
    )

    return {
        "status": "success",
        "filename": filename,
        "versions": paginated_versions,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/notebooks/{filename}/versions/export")
def export_notebook_versions(filename: str):
    """Download a previously uploaded notebook's current content together
    with its entire snapshotted version history, bundled into a single
    .zip -- a complete, restorable backup of everything POST
    /api/upload?overwrite=true and POST .../restore have ever produced
    for this one notebook.

    GET /api/notebooks/export already bundles several *different*
    notebooks' own current content into one zip, and GET
    /api/notebooks/{filename}/versions/{version_id} already downloads one
    snapshot's raw bytes at a time -- but there was no way to get a
    complete backup of a single notebook's own full history (current
    content plus every past version) in one call: a caller wanting that
    (e.g. before DELETE /api/notebooks/{filename}/versions clears it, or
    just to archive a notebook's edit history off this server entirely)
    had to first GET .../versions to enumerate every version_id, then one
    GET .../versions/{version_id} call per snapshot plus a separate GET
    .../{filename} for the current content -- N+1 requests to reconstruct
    what this endpoint now returns in one.

    The archive's top-level entry is `filename` itself (the current
    content, exactly as GET /api/notebooks/{filename} already returns
    it), with every snapshot underneath a "versions/" prefix, named by its
    own "version_id" (the same name GET .../versions/{version_id} already
    downloads it by) -- so re-uploading the top-level entry and restoring
    from any "versions/<version_id>" entry (via POST
    .../versions/{version_id}/restore, after re-uploading it as a new
    version through some other means) reconstructs this notebook's exact
    prior state.

    A notebook with no version history at all still exports successfully
    -- just its current content, no "versions/" entries -- the same "an
    empty/absent version history is a valid state, not an error"
    reasoning GET /api/notebooks/{filename}/versions' own empty list
    already follows.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        archive.write(file_path, filename)

        if versions_dir.is_dir():

            for entry in sorted(versions_dir.iterdir()):

                if entry.is_file():
                    archive.write(entry, f"versions/{entry.name}")

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}.versions.zip"'
            ),
        },
    )


@router.post("/notebooks/{filename}/versions/import")
async def import_notebook_versions(
    filename: str, file: UploadFile = File(...), overwrite: bool = False
):
    """Restore a notebook's current content together with its entire
    snapshotted version history from a single .zip -- the counterpart to
    GET /api/notebooks/{filename}/versions/export, which produces exactly
    this archive shape.

    GET .../versions/export's own docstring already describes the archive
    it produces as "a complete, restorable backup", but restoring one back
    onto a dashboard meant unzipping it locally, re-uploading the
    top-level entry by hand, then one POST .../versions/{version_id}/copy
    (after somehow re-uploading each "versions/<version_id>" entry as its
    own version first) per snapshot to rebuild the history -- tedious
    enough that a caller archiving a notebook's history off this server
    (the exact use case that docstring names) had no practical way back
    in. This endpoint takes that same archive directly, restoring both
    halves in one call.

    `filename` (the path segment, not whatever name the archive's own
    top-level entry happens to carry -- GET .../versions/export always
    names it after the notebook it was exported from, but this endpoint
    doesn't require that to still match) is where the archive's
    current-content entry is written, via the identical
    _save_uploaded_notebook validation/atomic-write/versioning path POST
    /api/upload and POST /api/notebooks/import already share -- so a
    backup can be restored under its original name or a new one (e.g.
    recovering a notebook that was since renamed or deleted) equally well.
    "overwrite" (default false) is the identical query param POST
    /api/upload already takes: without it, restoring onto a filename that
    already exists is a 409, exactly as overwriting it any other way
    already is; with it, the pre-restore content is itself snapshotted
    first (via _snapshot_current_notebook_version, the same as any other
    overwrite), so restoring never silently destroys whatever was already
    at `filename`.

    Every "versions/<version_id>" entry is then written into `filename`'s
    own version history under that identical version_id -- the same name
    GET .../versions/{version_id} already downloads it by -- so a
    subsequent GET .../versions lists exactly the snapshots the archive
    carried, immediately restorable via POST .../versions/{version_id}/
    restore. Each one's "saved_at" (see list_notebook_versions above) is
    reconstructed from the timestamp already encoded in its own version_id
    (see _snapshot_current_notebook_version's own naming scheme) rather
    than left at this call's own import time, so a restored history still
    reports when each snapshot was *originally* saved, not when it was
    re-imported; a version_id that doesn't match that naming scheme (e.g.
    a hand-built archive) simply keeps whatever mtime writing it produced.
    Subject to the same MAX_NOTEBOOK_VERSIONS cap _prune_notebook_versions
    already enforces everywhere else -- restoring an archive whose own
    history is longer than the cap (or that lands on top of a notebook
    whose pre-restore content was itself just snapshotted) discards the
    oldest entries first, exactly as an ordinary overwrite already would.

    The archive must contain exactly one .ipynb entry outside a
    "versions/" prefix -- GET .../versions/export never produces anything
    else, so zero or several such entries means this isn't that kind of
    archive (or is corrupt) and the whole request is rejected with 400
    before anything is written, rather than guessing which one is the
    "real" current content.
    """

    if not file.filename.endswith(".zip"):

        raise HTTPException(
            status_code=400,
            detail="File must be a .zip archive"
        )

    try:

        zip_bytes = await file.read()

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    try:

        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))

    except zipfile.BadZipFile:

        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid zip archive"
        )

    version_entries = [
        name for name in archive.namelist()
        if name.startswith("versions/") and not name.endswith("/")
    ]

    current_content_entries = [
        name for name in archive.namelist()
        if (
            name.endswith(".ipynb")
            and not name.startswith("versions/")
            and not name.endswith("/")
        )
    ]

    if len(current_content_entries) != 1:

        raise HTTPException(
            status_code=400,
            detail=(
                "Zip archive must contain exactly one current-content "
                ".ipynb entry outside 'versions/' (found "
                f"{len(current_content_entries)}) -- the same shape GET "
                "/api/notebooks/{filename}/versions/export produces"
            )
        )

    current_content_bytes = archive.read(current_content_entries[0])

    upload_file = UploadFile(
        file=io.BytesIO(current_content_bytes), filename=filename
    )

    result = await _save_uploaded_notebook(upload_file, overwrite)

    file_path = resolve_upload_path(filename)
    versions_dir = _notebook_versions_dir(file_path.name)

    imported_version_ids = []

    with _version_lock_for(file_path.name):

        if version_entries:
            versions_dir.mkdir(parents=True, exist_ok=True)

        for entry_name in sorted(version_entries):

            version_id = os.path.basename(entry_name)

            if not version_id:
                continue

            version_path = versions_dir / version_id

            with open(version_path, "wb") as f:
                f.write(archive.read(entry_name))

            # Every version_id _snapshot_current_notebook_version ever
            # produces starts with this exact "%Y%m%dT%H%M%S%f" prefix --
            # reuse it as the restored file's own mtime so "saved_at"
            # (list_notebook_versions above, which reads mtime directly)
            # still reflects when this snapshot was originally saved
            # rather than this import call's own timestamp. A version_id
            # that doesn't parse this way (e.g. a hand-built archive) just
            # keeps whatever mtime the write above already produced.
            try:

                saved_at = datetime.strptime(
                    version_id.split("_", 1)[0], "%Y%m%dT%H%M%S%f"
                ).replace(tzinfo=timezone.utc)

                mtime = saved_at.timestamp()
                os.utime(version_path, (mtime, mtime))

            except ValueError:
                pass

            imported_version_ids.append(version_id)

        _prune_notebook_versions(versions_dir)

    return {
        "status": "success",
        "filename": filename,
        "overwritten": result["overwritten"],
        "imported_version_ids": sorted(imported_version_ids),
        "imported_version_count": len(imported_version_ids),
    }


@router.delete("/notebooks/{filename}/versions")
def clear_notebook_versions(filename: str, dry_run: bool = False):
    """Permanently discard every one of a notebook's snapshotted previous
    versions at once, without touching the notebook's own current content,
    tags, or currently-compiled status.

    DELETE /api/notebooks/{filename}/versions/{version_id} already removes
    one snapshot at a time, and _prune_notebook_versions (above) already
    trims the oldest ones automatically once a notebook accumulates more
    than MAX_NOTEBOOK_VERSIONS -- but there was no way to clear a
    notebook's entire version history in one call. An operator wanting to
    reclaim the disk space an actively-edited notebook's history has
    built up, or to discard a run of snapshots that turn out to contain
    something sensitive, had to first GET .../versions to enumerate every
    version_id, then call the single-version DELETE once per entry --
    mirroring exactly the gap DELETE /api/notebooks/delete-batch already
    closed for deleting several *notebooks* at once, just one level down,
    for a single notebook's own *version history* instead.

    Held under the same _version_lock_for restore_notebook_version's own
    snapshot-then-copy sequence already uses, for the identical reason:
    without it, this could rmtree a versions directory a concurrent
    POST .../restore is in the middle of snapshotting the current content
    into (see _snapshot_current_notebook_version), losing that snapshot
    before it was ever listed here.

    A no-op success (not a 404) when the notebook has no version history
    at all -- "nothing to clear" is a valid outcome of a bulk operation
    like this, not an error, the same reasoning DELETE /api/tags/{tag}'s
    own empty "affected_notebooks" already applies when nothing matched.

    "dry_run" (optional, default false) reports the exact same
    "deleted_version_ids"/"deleted_count" a real clear would, without
    discarding a single snapshot, the identical preview DELETE
    /api/notebooks/versions's own "dry_run" already provides for pruning
    every notebook's own old versions at once -- an operator wanting to
    confirm just how much of a single notebook's own history `clear`
    would wipe out (e.g. before reaching for it over the narrower
    `versions delete-batch`) had no safe way to check that first short of
    a separate GET .../versions of their own. The top-level response's
    own "dry_run" field echoes back whether this call actually deleted
    anything.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    with _version_lock_for(file_path.name):

        deleted_version_ids = sorted(
            entry.name for entry in versions_dir.iterdir()
            if entry.is_file()
        ) if versions_dir.is_dir() else []

        if not dry_run:
            shutil.rmtree(versions_dir, ignore_errors=True)

    return {
        "status": "success",
        "dry_run": dry_run,
        "filename": filename,
        "deleted_version_ids": deleted_version_ids,
        "deleted_count": len(deleted_version_ids),
    }


@router.post("/notebooks/{filename}/versions/delete-batch")
def delete_notebook_versions_batch(filename: str, data: dict):
    """Permanently discard a caller-chosen set of a notebook's own
    snapshotted versions in one call.

    DELETE /api/notebooks/{filename}/versions/{version_id} already
    discards one snapshot at a time, and DELETE
    /api/notebooks/{filename}/versions already discards a notebook's
    *entire* version history at once -- but there was nothing in
    between: discarding a handful of specific, known-bad snapshots (e.g.
    a run of versions a `versions list` review turned up as accidentally
    containing something sensitive) while keeping the rest of that same
    notebook's history intact meant one DELETE
    .../versions/{version_id} call per snapshot, or reaching for the
    "clear everything" endpoint and losing every *other* version along
    with them. delete_notebook_version's own docstring already names
    this exact gap ("an operator wanting that already has it, via
    `versions list` piped into one call per version_id") -- this closes
    it, the identical "several specific ones" middle ground POST
    /api/notebooks/delete-batch already provides one level up, for
    notebooks themselves.

    Deliberately takes an explicit "version_ids" list rather than a
    filter, the same reasoning POST /api/notebooks/delete-batch's own
    docstring already gives for its identical "filenames" field: the
    caller already knows exactly which versions to remove, typically
    from a preceding GET .../versions of its own.

    Reuses the exact per-entry "one bad entry doesn't abort the batch"
    contract POST /api/notebooks/delete-batch and POST
    /api/tags/{tag}/apply already established: each version_id is
    processed independently, and "results" reports one {"version_id",
    "status", ...} entry per version_id -- "success" or "error" (with the
    HTTPException detail that version_id's own single-version delete
    would have raised on its own, e.g. a 404 for an unknown version_id).
    The response is always 200 -- the batch request itself was handled,
    even if every version_id in it failed -- with
    "succeeded_count"/"failed_count" summarizing "results" the same way
    those two endpoints' own identical fields already do.

    Held under the same _version_lock_for restore_notebook_version's own
    snapshot-then-copy sequence already uses, for the whole batch (not
    reacquired per version_id) -- the identical reason
    delete_notebook_version's own single-entry delete already holds it,
    just for the batch's own full duration instead of one unlink.

    "dry_run" (optional, default false, in the request body) reports the
    exact same per-"version_id" "results" a real batch would -- each
    entry's own "success" or "error" (with the identical detail a real
    delete would raise, e.g. a 404 for a typo'd version_id) -- without
    discarding a single snapshot, the identical preview POST
    /api/notebooks/delete-batch's own "dry_run" already provides for
    deleting several *notebooks* at once. The top-level response's own
    "dry_run" field echoes back whether this call actually deleted
    anything.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    version_ids = data.get("version_ids")

    if (
        not isinstance(version_ids, list)
        or not version_ids
        or not all(isinstance(v, str) for v in version_ids)
    ):
        raise HTTPException(
            status_code=400,
            detail="version_ids must be a non-empty list of strings"
        )

    dry_run = bool(data.get("dry_run", False))

    versions_dir = _notebook_versions_dir(file_path.name)

    results = []
    succeeded_count = 0
    failed_count = 0

    with _version_lock_for(file_path.name):

        for version_id in version_ids:

            try:

                version_path = _resolve_path_within(
                    str(versions_dir), version_id, "notebook version"
                )

                if not version_path.is_file():

                    raise HTTPException(
                        status_code=404,
                        detail="Notebook version not found"
                    )

                if not dry_run:
                    version_path.unlink()

                results.append({
                    "version_id": version_id,
                    "status": "success",
                })
                succeeded_count += 1

            except HTTPException as exc:

                results.append({
                    "version_id": version_id,
                    "status": "error",
                    "detail": exc.detail,
                })
                failed_count += 1

    return {
        "status": "success",
        "dry_run": dry_run,
        "filename": filename,
        "results": results,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


@router.get("/notebooks/{filename}/versions/{version_id}")
def get_notebook_version(filename: str, version_id: str):
    """Download the raw content of one of a notebook's previously
    snapshotted versions, by the "version_id" GET
    /api/notebooks/{filename}/versions already lists.

    Reuses _resolve_path_within for the same traversal protection
    resolve_upload_path/resolve_generated_path already apply to their own
    respective root directories -- `version_id` here is exactly as much
    client input as an uploaded file's own name, just rooted at this one
    notebook's own version directory instead of UPLOAD_DIR or
    GENERATED_DIR directly.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    version_path = _resolve_path_within(
        str(versions_dir), version_id, "notebook version"
    )

    if not version_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook version not found"
        )

    return FileResponse(
        path=version_path,
        media_type="application/x-ipynb+json",
        filename=version_id,
    )


@router.get("/notebooks/{filename}/versions/{version_id}/inspect")
def inspect_notebook_version(filename: str, version_id: str):
    """Inspect one of a notebook's previously snapshotted versions --
    its functions, dependencies, reserved-name conflicts, would-be
    endpoints, and skipped functions -- exactly as POST /api/inspect
    already reports for an *uploaded* notebook, but for a past snapshot's
    own content instead.

    resolve_upload_path (used by POST /api/inspect, POST /api/validate,
    POST /api/requirements-preview, POST /api/app-preview, and POST
    /api/curl-preview for their own "notebook_path") deliberately rejects
    any value with a directory component of its own (see its own
    docstring) -- so none of those endpoints can be pointed at a version
    snapshot at all: they live under UPLOAD_DIR/.versions/<filename>/,
    not directly inside UPLOAD_DIR as a flat filename. Before this,
    answering "would this old version still compile cleanly, and what
    would it expose" meant first POST .../versions/{version_id}/restore-
    ing it over the notebook's own current content (destructive, even
    though restoring is itself undoable) or GET .../versions/{version_id}
    downloading it locally just to run the CLI's own `inspect` against
    that local copy.

    GET .../versions/{version_id}/diff already answers "what changed"
    between a version and something else -- this answers the
    complementary "what does this version look like on its own", the
    same full picture POST /api/inspect already gives for a live
    notebook, not just the function-level comparison diff reports.

    Reuses inspect_notebook_data (backend/inspector.py) completely
    unchanged, pointed at the version snapshot's own file path instead of
    an uploaded notebook's -- the exact same {"functions", "dependencies",
    "generated_files", "reserved_name_conflicts", "endpoints",
    "skipped_functions"} shape POST /api/inspect already returns, so this
    can never drift from what that endpoint already reports for
    identical content. "generated_files" still reflects whatever's
    currently sitting in GENERATED_DIR -- informational context about
    this dashboard's own current compiled state, the same way POST
    /api/inspect's own "generated_files" already is regardless of which
    notebook_path was actually inspected, not something tied to this
    particular version.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    version_path = _resolve_path_within(
        str(versions_dir), version_id, "notebook version"
    )

    if not version_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook version not found"
        )

    try:

        load_notebook(str(version_path))

    except MALFORMED_NOTEBOOK_ERRORS as e:

        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    # Held for the same reason POST /api/inspect's identical
    # inspect_notebook_data call already is (see its own comment): a
    # concurrent POST /api/compile's clear_stale_export_artifacts can
    # rmtree the sdk/ subdirectory this call's own "generated_files"
    # field walks, mid-walk.
    with COMPILE_LOCK:

        inspection = inspect_notebook_data(
            str(version_path),
            GENERATED_DIR
        )

    return {
        "status": "success",
        "filename": filename,
        "version_id": version_id,
        **inspection,
    }


@router.get("/notebooks/{filename}/versions/{version_id}/diff")
def diff_notebook_version(filename: str, version_id: str, against: str = None):
    """Compare the top-level functions a snapshotted version of `filename`
    would compile into endpoints against either another snapshotted
    version (`against`, a version_id) or -- if `against` is omitted --
    `filename`'s own current live content. Entirely server-side, without
    downloading either side.

    The CLI's own `versions diff` already computes this same comparison,
    but only by GETting both sides' raw bytes (GET
    /api/notebooks/{filename}/versions/{version_id} and/or GET
    /api/notebooks/{filename}) down to temporary files and diffing those
    local copies itself -- the same round trip GET /api/notebooks/diff's
    own docstring already closed for comparing two independently-uploaded
    notebooks. A caller that just wants to know "what would restoring this
    snapshot actually change" (a dashboard frontend's `versions list`
    entry showing an inline preview before a restore, for instance) had no
    way to get that without pulling the full notebook JSON for both sides
    across the wire first.

    Reuses diff_notebook_functions (backend/inspector.py) unchanged -- the
    exact same {"added", "removed", "changed", "unchanged"} report
    `versions diff`/`diff`/`remote-diff`/`diff-notebooks` already produce
    -- so this can never drift from what any of those already report for
    the same two sides.

    Both sides are resolved and validated (existence, then parseability)
    before diffing, each labeled by exactly which side it is ("version
    '<version_id>'" or "the current live content of '<filename>'"), the
    same per-side labeling GET /api/notebooks/diff's own "old"/"new"
    validation already applies -- so a 404 or 400 names exactly which side
    is the problem rather than one ambiguous error covering both.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    old_path = _resolve_path_within(
        str(versions_dir), version_id, "notebook version"
    )

    if not old_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook version not found"
        )

    if against is None:
        new_path = file_path
        new_label = f"the current live content of '{filename}'"
    else:

        new_path = _resolve_path_within(
            str(versions_dir), against, "notebook version"
        )

        if not new_path.is_file():

            raise HTTPException(
                status_code=404,
                detail="Notebook version not found"
            )

        new_label = f"version '{against}'"

    for label, path in (
        (f"version '{version_id}'", old_path),
        (new_label, new_path),
    ):

        try:

            load_notebook(str(path))

        except MALFORMED_NOTEBOOK_ERRORS as e:

            raise HTTPException(
                status_code=400,
                detail=f"{label} is not a valid Jupyter notebook: {e}"
            )

    diff = diff_notebook_functions(str(old_path), str(new_path))

    return {
        "status": "success",
        "filename": filename,
        "version_id": version_id,
        "against": against,
        **diff,
    }


@router.post("/notebooks/{filename}/versions/{version_id}/copy")
def copy_notebook_version(filename: str, version_id: str, data: dict):
    """Duplicate one of `filename`'s snapshotted past versions into a
    brand-new notebook under `new_filename`, leaving `filename`'s own
    current content, tags, and version history completely untouched.

    POST /notebooks/{filename}/versions/{version_id}/restore already lets
    a caller make a past version `filename`'s own current content again
    -- but that's inherently destructive to whatever `filename` currently
    holds (even though restoring is itself undoable, per its own
    docstring): there was no way to get an *independent* copy of an old
    snapshot to branch off of or compare against side-by-side without
    first overwriting the live notebook a caller might still be actively
    using. This is the version-history equivalent of what POST
    /notebooks/{filename}/copy already provides for a notebook's current
    content -- just sourced from one of its past snapshots instead.

    `new_filename` must differ from `filename` itself: copying a version
    "onto" its own source notebook is exactly what `.../restore` already
    exists for (and does properly -- snapshotting the current content
    first, which this endpoint has no reason to do for an unrelated
    destination), so that's rejected with 400 pointing there instead of
    silently overwriting `filename`'s own live content without a
    snapshot.

    Deliberately does NOT inherit `filename`'s current tags or
    description the way POST /notebooks/{filename}/copy inherits the
    source notebook's for a same-content copy: a tag like "production" or
    a description like "the model currently in prod" describes
    `filename`'s *current* content, which this snapshot may no longer
    match at all -- inheriting either here would misrepresent old content
    as whatever the live notebook happens to be tagged/described as
    today. The new copy starts untagged and undescribed, exactly like any
    other brand-new upload. If "overwrite": true replaces an existing
    destination notebook, that destination's own previous tags,
    description, and version history are discarded along with the rest of
    the file it belonged to, the same overwrite semantics
    copy_notebook/rename_notebook already apply.

    Same new_filename validation (".ipynb" suffix, collision rejected
    with 409 unless "overwrite": true) and _rename_lock_for(dest) usage
    as _copy_notebook_to, applied here to a version snapshot's bytes
    instead of a notebook's current ones.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    version_path = _resolve_path_within(
        str(versions_dir), version_id, "notebook version"
    )

    if not version_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook version not found"
        )

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

    dest_path = resolve_upload_path(new_filename)

    if dest_path == file_path:

        raise HTTPException(
            status_code=400,
            detail=(
                "new_filename must be different from filename -- to make "
                "this version filename's own current content again, use "
                "POST .../versions/{version_id}/restore instead."
            )
        )

    overwrite = bool(data.get("overwrite", False))

    with _rename_lock_for(dest_path.name):

        if dest_path.exists() and not overwrite:

            raise HTTPException(
                status_code=409,
                detail=(
                    f"A notebook named '{new_filename}' already exists. "
                    'Pass "overwrite": true to replace it.'
                )
            )

        try:

            shutil.copy2(version_path, dest_path)

        except OSError as e:

            raise HTTPException(
                status_code=500,
                detail=str(e)
            )

        dest_versions_dir = _notebook_versions_dir(dest_path.name)

        if dest_versions_dir.exists():
            shutil.rmtree(dest_versions_dir)

        _write_notebook_tags(dest_path.name, [])
        _write_notebook_description(dest_path.name, "")

    return {
        "status": "success",
        "filename": filename,
        "version_id": version_id,
        "new_filename": new_filename,
    }


@router.post("/notebooks/{filename}/versions/{version_id}/restore")
def restore_notebook_version(filename: str, version_id: str):
    """Make a previously snapshotted version `filename`'s current content
    again, undoing one or more overwrites (POST
    /api/upload?overwrite=true).

    The content currently in place is itself snapshotted first (via the
    same _snapshot_current_notebook_version every overwrite already goes
    through) before being replaced by the requested version -- so
    restoring is itself undoable, exactly like the overwrite it's
    reversing, rather than a one-way trip that could just as easily lose
    work if the wrong version_id were picked.

    Held under _version_lock_for(filename) for the same reason
    rename_notebook's own check-then-write sequence is held under
    _rename_lock_for: without it, two concurrent restores of the same
    notebook could interleave their own snapshot-then-copy steps.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    version_path = _resolve_path_within(
        str(versions_dir), version_id, "notebook version"
    )

    if not version_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook version not found"
        )

    with _version_lock_for(file_path.name):

        _snapshot_current_notebook_version(file_path)

        shutil.copy2(version_path, file_path)

    return {
        "status": "success",
        "filename": filename,
        "restored_version_id": version_id,
    }


@router.delete("/notebooks/{filename}/versions/{version_id}")
def delete_notebook_version(filename: str, version_id: str):
    """Permanently discard one of a notebook's snapshotted previous
    versions, by the "version_id" GET
    /api/notebooks/{filename}/versions already lists.

    _prune_notebook_versions (above) already discards a notebook's
    *oldest* snapshots once it accumulates more than MAX_NOTEBOOK_VERSIONS
    -- but that's an automatic, age-based eviction with no way for a
    caller to act sooner or more selectively: purging a specific
    snapshot immediately (e.g. one that turns out to contain something
    sensitive, without waiting for MAX_NOTEBOOK_VERSIONS more overwrites
    to age it out) or discarding a handful of known-bad snapshots to keep
    `versions list` legible had no way to happen at all short of waiting.

    Reuses _resolve_path_within for the same traversal protection
    GET/POST .../versions/{version_id} already apply to `version_id`, and
    _version_lock_for for the same reason restore_notebook_version's own
    write to this notebook's version history is already held under it --
    without it, this could race restore_notebook_version's own
    snapshot-then-copy sequence, deleting the very version_id it's in the
    middle of copying from.

    Deliberately narrower than DELETE /api/notebooks (which removes a
    notebook's version history *as a side effect* of removing the
    notebook itself): this only ever removes one snapshot, never the
    notebook it belongs to, and has no bulk/"delete every version"
    equivalent -- an operator wanting that already has it, via `versions
    list` piped into one call per version_id.
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    versions_dir = _notebook_versions_dir(file_path.name)

    version_path = _resolve_path_within(
        str(versions_dir), version_id, "notebook version"
    )

    if not version_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook version not found"
        )

    with _version_lock_for(file_path.name):

        version_path.unlink()

    return {
        "status": "success",
        "filename": filename,
        "deleted_version_id": version_id,
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


def _resolve_preview_content_path(notebook_path, version_id=None):
    """Resolve the file POST /api/validate, /api/requirements-preview,
    /api/app-preview, and /api/curl-preview should each read
    `notebook_path`'s content from -- its own current content, or, with
    `version_id` given, one of its previously snapshotted versions
    instead.

    resolve_upload_path (which every one of those four already calls for
    "notebook_path") deliberately rejects any value with a directory
    component of its own (see its own docstring) -- so none of them could
    previously be pointed at a version snapshot at all: those live under
    UPLOAD_DIR/.versions/<filename>/, not directly inside UPLOAD_DIR as a
    flat filename. inspect_notebook_version and
    .../versions/{version_id}/diff (above) already closed this identical
    gap for POST /api/inspect and for diffing a version against something
    else; before this, answering "would this old version still validate/
    compile cleanly, and what would its requirements.txt/app.py/curl
    commands actually look like" meant first POST
    .../versions/{version_id}/restore-ing it over the notebook's own
    current content (destructive, even though restoring is itself
    undoable) or GET .../versions/{version_id} downloading it locally
    just to run the CLI's own local equivalents against that copy.

    `version_id` reuses the exact same _resolve_path_within traversal
    protection GET/POST .../versions/{version_id}[/...] already apply to
    it. Raises the identical 404s each of these four endpoints already
    raised for a missing `notebook_path` on its own (now also covering an
    unknown `version_id` once one is given), so an invalid version_id
    doesn't read back as a different failure mode than an invalid
    notebook_path always has.
    """

    file_path = resolve_upload_path(notebook_path)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    if version_id is None:
        return file_path

    versions_dir = _notebook_versions_dir(file_path.name)

    version_path = _resolve_path_within(
        str(versions_dir), version_id, "notebook version"
    )

    if not version_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook version not found"
        )

    return version_path


@router.post("/validate")
def validate_notebook_endpoint(
    data: dict
):
    """CI-friendly pass/warn/fail verdict on whether a notebook already
    uploaded to this dashboard would compile cleanly -- without actually
    compiling it, or touching GENERATED_DIR at all.

    The CLI's own local `validate` already does exactly this (see
    _dispatch_core_command in cli.py) for a notebook on the caller's own
    filesystem, reusing inspect_notebook_data's existing
    "reserved_name_conflicts"/"skipped_functions" checks rather than a
    separate pass -- but there was no equivalent for a notebook already
    living on a running dashboard: a CI pipeline (or any other caller)
    that uploads a notebook here first had to either run `remote-compile`
    just to find out whether it would fail (mutating GENERATED_DIR, and
    the currently-compiled app along with it, just to ask a yes/no
    question), or download the notebook back down to disk purely to run
    the local `validate` against that downloaded copy.

    "status" is "fail" when the notebook has a reserved-name conflict
    (compilation would fail outright), "warn" when it has skipped
    functions but "strict" wasn't passed (compilation would still
    succeed for every other function), or "pass" otherwise -- identical
    to the CLI's own local verdict logic, so a caller sees the same
    answer regardless of which one it asks. Unlike sys.exit(1)/
    sys.exit(2) there, a "warn"/"fail" verdict here is still a normal 200
    response: the notebook itself failing this check is an expected,
    valid outcome of asking the question, not a server error -- the same
    reasoning GET /api/notebooks' own "currently_compiled" already
    follows for reporting a fact about a notebook's state rather than
    raising over it.

    "version_id" (optional, see _resolve_preview_content_path above) runs
    this same check against one of "notebook_path"'s own previously
    snapshotted versions instead of its current content -- e.g. to check
    whether an old version would still validate cleanly before POST
    .../versions/{version_id}/restore-ing it back over the notebook's
    current content.
    """

    notebook_path = data.get(
        "notebook_path"
    )

    if not notebook_path:

        raise HTTPException(
            status_code=400,
            detail="notebook_path is required"
        )

    strict = bool(data.get("strict", False))
    version_id = data.get("version_id")

    full_path = _resolve_preview_content_path(notebook_path, version_id)

    try:

        load_notebook(str(full_path))

    except MALFORMED_NOTEBOOK_ERRORS as e:

        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    # Held for the same reason POST /api/inspect's identical
    # inspect_notebook_data call already is -- see its own docstring: a
    # concurrent POST /api/compile's clear_stale_export_artifacts can
    # rmtree the sdk/ subdirectory this call's own "generated_files"
    # field walks, mid-walk.
    with COMPILE_LOCK:

        inspection = inspect_notebook_data(
            str(full_path),
            GENERATED_DIR
        )

    reserved_name_conflicts = inspection["reserved_name_conflicts"]
    skipped_functions = inspection["skipped_functions"]

    has_blocking_issues = bool(reserved_name_conflicts) or (
        strict and bool(skipped_functions)
    )
    has_warnings = bool(skipped_functions) and not has_blocking_issues

    if has_blocking_issues:
        status = "fail"
    elif has_warnings:
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "notebook": notebook_path,
        "version_id": version_id,
        "reserved_name_conflicts": reserved_name_conflicts,
        "skipped_functions": skipped_functions,
    }


@router.get("/validate-all")
def validate_all_notebooks(
    strict: bool = False, tag: str = None, limit: int = None, offset: int = 0
):
    """Run the identical pass/warn/fail check POST /api/validate already
    performs for one notebook, across every notebook already uploaded to
    this dashboard at once.

    POST /api/validate answers "would this one notebook compile cleanly"
    without a caller needing to already suspect which notebook to ask
    about -- but a CI job wanting to catch a reserved-name conflict or a
    newly-broken function *anywhere* in the catalog before it's compiled
    and deployed had no way to ask that in one call: it would have to
    already know every uploaded filename (a separate GET /api/notebooks)
    and then issue one POST /api/validate per name, aborting its own loop
    early only by choice, and stitching the per-notebook verdicts back
    together itself.

    Deliberately does NOT silently skip a notebook that fails to parse at
    all, the way GET /api/functions' own search across every notebook
    already does for a malformed one -- that precedent exists there
    because a parse failure is incidental to what that endpoint is
    answering (which notebooks define a matching function); here, a
    notebook that can't even be parsed is exactly the kind of problem
    this endpoint exists to surface, so it's reported as its own "fail"
    result (with a "detail" explaining why) rather than dropped from the
    report entirely.

    "strict" applies uniformly to every notebook, the same single flag
    POST /api/validate itself takes -- there's no per-notebook override.

    "tag" (optional) scopes the scan to only notebooks currently carrying
    that exact tag, the same exact-match GET /api/notebooks?tag= and GET
    /api/functions?tag= already filter by -- a CI job that only cares
    about, say, its "production" notebooks previously had to validate
    the entire catalog and filter the response down client-side
    afterward. Applied before a notebook is even parsed, so an
    out-of-tag notebook (including one that would otherwise fail to
    parse) never contributes a result or counts toward
    pass_count/warn_count/fail_count at all.

    "limit"/"offset" page the returned "results" the identical way GET
    /api/notebooks' own "limit"/"offset" already page the notebook
    catalog -- before this, a large catalog always validated (and
    returned a result for) every notebook in one response, with no way
    to page through it. Applied last, after every "tag"-matching notebook
    has already been validated (still needed in full to compute
    "pass_count"/"warn_count"/"fail_count" correctly) -- a negative
    "offset" is rejected with 400 and a non-positive "limit" the same way
    GET /api/notebooks' own do. "pass_count"/"warn_count"/"fail_count"
    (and "result_count") always reflect every "tag"-matching notebook,
    never just the current page, the same "totals describe the whole
    matching set" reasoning GET /api/notebooks/storage's own identically-
    paginated running totals already follow.
    """

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    if limit is not None and limit <= 0:

        raise HTTPException(
            status_code=400,
            detail="limit must be a positive integer"
        )

    upload_root = Path(UPLOAD_DIR)

    results = []
    pass_count = 0
    warn_count = 0
    fail_count = 0

    for entry in sorted(upload_root.iterdir()):

        if not (entry.is_file() and entry.suffix == ".ipynb"):
            continue

        if tag and tag not in _read_notebook_tags(entry.name):
            continue

        try:

            load_notebook(str(entry))

        except MALFORMED_NOTEBOOK_ERRORS as e:

            results.append({
                "filename": entry.name,
                "status": "fail",
                "reserved_name_conflicts": [],
                "skipped_functions": [],
                "detail": f"Uploaded file is not a valid Jupyter notebook: {e}",
            })
            fail_count += 1
            continue

        # Held per-notebook, not once around this whole loop, for the
        # same reason POST /api/validate's identical inspect_notebook_data
        # call already holds it (see that endpoint's own comment): a
        # concurrent POST /api/compile's clear_stale_export_artifacts can
        # rmtree the sdk/ subdirectory this walks, mid-walk. Scoping it
        # per notebook instead of around the whole loop lets a concurrent
        # compile still interleave between notebooks rather than blocking
        # on this endpoint for as long as the entire catalog takes to walk.
        with COMPILE_LOCK:

            inspection = inspect_notebook_data(str(entry), GENERATED_DIR)

        reserved_name_conflicts = inspection["reserved_name_conflicts"]
        skipped_functions = inspection["skipped_functions"]

        has_blocking_issues = bool(reserved_name_conflicts) or (
            strict and bool(skipped_functions)
        )
        has_warnings = bool(skipped_functions) and not has_blocking_issues

        if has_blocking_issues:
            status = "fail"
            fail_count += 1
        elif has_warnings:
            status = "warn"
            warn_count += 1
        else:
            status = "pass"
            pass_count += 1

        results.append({
            "filename": entry.name,
            "status": status,
            "reserved_name_conflicts": reserved_name_conflicts,
            "skipped_functions": skipped_functions,
            "detail": None,
        })

    result_count = len(results)

    paginated_results = (
        results[offset:offset + limit] if limit is not None else results[offset:]
    )

    return {
        "status": "success",
        "results": paginated_results,
        "result_count": result_count,
        "limit": limit,
        "offset": offset,
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
    }


@router.post("/requirements-preview")
def requirements_preview_endpoint(data: dict):
    """The exact, sorted requirements.txt lines POST /api/compile would
    write for an already-uploaded notebook -- without actually compiling
    it, or touching GENERATED_DIR at all.

    POST /api/inspect's own "dependencies" field already lists a
    notebook's third-party imports, resolved to their real PyPI
    distribution names (see _third_party_dependencies, backend/
    inspector.py) -- but that's deliberately not the same thing as
    requirements.txt: it's missing every one of write_requirements' own
    unconditional core_dependencies (fastapi/uvicorn/pydantic, shipped in
    every compiled app whether or not the notebook itself imports them),
    isn't version-pinned, and doesn't include anything a notebook author
    declared via a "# notebook-to-api: requires <spec>" directive (see
    _extract_explicit_requirements). A caller wanting to know exactly
    what `pip install -r requirements.txt` -- and from there `deploy`'s
    own Docker build -- would actually install for this notebook had no
    way to ask that short of actually compiling it first and then
    reading GENERATED_DIR/requirements.txt back, mutating GENERATED_DIR
    (and whatever it currently backs) just to answer a question that
    doesn't require compiling anything at all.

    Reuses extract_third_party_imports and resolve_requirements (backend/
    compiler.py) -- the exact same import-collection and pinning logic
    compile_notebook_to_api itself now calls before writing
    requirements.txt -- so this can never drift from what an actual
    compile of the same notebook would produce, the same "can't drift
    from the real thing" guarantee POST /api/validate's own reuse of
    inspect_notebook_data already provides for compile-success/failure.

    "version_id" (optional, see _resolve_preview_content_path above)
    previews requirements.txt for one of "notebook_path"'s own previously
    snapshotted versions instead of its current content -- e.g. to check
    whether an old version would still need a dependency that's since
    been removed, before restoring it back over the notebook's current
    content.
    """

    notebook_path = data.get("notebook_path")

    if not notebook_path:

        raise HTTPException(
            status_code=400,
            detail="notebook_path is required"
        )

    version_id = data.get("version_id")

    full_path = _resolve_preview_content_path(notebook_path, version_id)

    try:

        notebook = load_notebook(str(full_path))

    except MALFORMED_NOTEBOOK_ERRORS as e:

        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    code_cells = [
        cell for cell in extract_code_cells(notebook)
        if is_parseable_python(cell)
    ]

    requirements = resolve_requirements(
        extract_third_party_imports(code_cells),
        explicit_requirements=_extract_explicit_requirements(code_cells),
    )

    return {
        "status": "success",
        "notebook": notebook_path,
        "version_id": version_id,
        "requirements": requirements,
    }


@router.post("/app-preview")
def app_preview_endpoint(data: dict):
    """The exact FastAPI application source code POST /api/compile would
    write to app.py for an already-uploaded notebook -- without actually
    compiling it, or touching GENERATED_DIR (or whatever it currently
    backs) at all.

    POST /api/requirements-preview and POST /api/curl-preview already let
    a caller preview what compiling a notebook would produce without a
    real compile -- but neither shows the one thing a caller most likely
    wants to review before actually committing to it: the generated
    Python source itself. Before this, seeing that meant POST
    /api/compile-ing the notebook for real -- replacing whatever
    GENERATED_DIR currently serves, live, for every other caller of this
    dashboard -- and only then reading it back via
    GET /api/generated/app.py, just to answer "what would this actually
    generate."

    Reuses the exact same function-building steps compile_notebook_to_api
    itself performs, in the same order (extract_code_cells ->
    is_parseable_python filter -> extract_functions_from_code ->
    deduplicate_functions_by_name -> _filter_functions_by_name via
    "only"/"exclude" -> generate_fastapi_code), stopping right before its
    write phase -- so "app_code" here can never drift from what an actual
    compile of the same notebook (with the same only/exclude) would write
    to app.py. generate_fastapi_code (backend/generator/api_generator.py)
    is a pure function -- it returns a string and writes nothing to disk
    on its own -- so nothing here ever touches GENERATED_DIR, needs
    COMPILE_LOCK, or has any observable effect on whatever this dashboard
    currently has compiled.

    "package_name" always reflects GENERATED_DIR's own basename (see
    package_name_for_output_dir, backend/compiler.py) -- the same package
    name an actual POST /api/compile of this notebook would use, since
    every compile through this dashboard always targets that one fixed
    directory.

    "only"/"exclude" and their validation mirror POST /api/compile's own
    exactly (see its docstring) -- an invalid value gets the identical 400
    here that it would there.

    "version_id" (optional, see _resolve_preview_content_path above)
    previews app.py for one of "notebook_path"'s own previously
    snapshotted versions instead of its current content -- e.g. to review
    what an old version would generate before restoring it back over the
    notebook's current content.
    """

    notebook_path = data.get("notebook_path")

    if not notebook_path:

        raise HTTPException(
            status_code=400,
            detail="notebook_path is required"
        )

    only = data.get("only")
    exclude = data.get("exclude")
    version_id = data.get("version_id")

    for field_name, field_value in (("only", only), ("exclude", exclude)):

        if field_value is not None and (
            not isinstance(field_value, list)
            or not all(isinstance(item, str) for item in field_value)
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a list of strings"
            )

    if only and exclude:

        raise HTTPException(
            status_code=400,
            detail="only and exclude can't both be given -- choose one."
        )

    full_path = _resolve_preview_content_path(notebook_path, version_id)

    try:

        notebook = load_notebook(str(full_path))

        code_cells = [
            cell for cell in extract_code_cells(notebook)
            if is_parseable_python(cell)
        ]

        functions = []

        for cell in code_cells:
            functions.extend(extract_functions_from_code(cell))

        functions = deduplicate_functions_by_name(functions)

        functions = _filter_functions_by_name(functions, only, exclude)

        package_name = package_name_for_output_dir(GENERATED_DIR)

        app_code = generate_fastapi_code(functions, package_name)

    except ReservedFunctionNameError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except MALFORMED_NOTEBOOK_ERRORS as e:

        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Preview error: {str(e)}"
        )

    return {
        "status": "success",
        "notebook": notebook_path,
        "version_id": version_id,
        "package_name": package_name,
        "app_code": app_code,
    }


@router.post("/curl-preview")
def curl_preview_endpoint(data: dict):
    """A ready-to-run `curl` command for every function an already-
    uploaded notebook would compile into an endpoint -- without
    compiling it, or a caller needing to first download its raw bytes.

    The CLI's own `remote-curl` already gets this same result for a
    notebook already on a dashboard, but only by downloading the whole
    notebook first (GET /api/notebooks/{filename}), writing it to a local
    temp file, and running generate_curl_commands (backend/inspector.py)
    against that local copy -- a caller that just wants a quick preview
    (a web frontend showing "try it" snippets for an uploaded notebook,
    for instance) had no way to get the same list without also fetching
    and locally re-parsing the entire notebook itself. This runs
    generate_curl_commands server-side instead, against the notebook
    already sitting in UPLOAD_DIR, and returns just its own "commands"
    list -- no notebook bytes, no local temp file, no script written to
    disk (unlike `remote-curl`, which always writes one).

    "host"/"port"/"api_key" mirror generate_curl_commands' own identical
    keyword arguments -- see its docstring for their defaults ("localhost",
    8000, and the generated app's own DEFAULT_DEV_API_KEY respectively)
    and why a caller would override any of them (a non-default `serve`
    host/port, or a NOTEBOOK_API_KEY already configured to something other
    than the default dev key).

    generate_curl_commands internally calls inspect_notebook_data with
    its own default output_dir ("generated") rather than this dashboard's
    own configured GENERATED_DIR -- harmless in practice since it never
    reads inspect_notebook_data's "generated_files" field at all, only
    "functions" and "reserved_name_conflicts" -- but held under
    COMPILE_LOCK regardless, the same protection POST /api/inspect's own
    identical inspect_notebook_data call already needs against a
    concurrent recompile: under this dashboard's *default* configuration,
    "generated" (the relative default both GENERATED_DIR and
    inspect_notebook_data's own output_dir default to) are the exact same
    directory, so a concurrent POST /api/compile really can clear it
    mid-walk unless this holds the same lock that endpoint already does.

    "version_id" (optional, see _resolve_preview_content_path above)
    previews curl commands for one of "notebook_path"'s own previously
    snapshotted versions instead of its current content -- e.g. to see
    what an old version would have exposed before restoring it back over
    the notebook's current content.
    """

    notebook_path = data.get("notebook_path")

    if not notebook_path:

        raise HTTPException(
            status_code=400,
            detail="notebook_path is required"
        )

    version_id = data.get("version_id")

    full_path = _resolve_preview_content_path(notebook_path, version_id)

    try:

        load_notebook(str(full_path))

    except MALFORMED_NOTEBOOK_ERRORS as e:

        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is not a valid Jupyter notebook: {e}"
        )

    host = data.get("host", "localhost")
    port = data.get("port", 8000)
    api_key = data.get("api_key")

    if not isinstance(host, str):

        raise HTTPException(
            status_code=400,
            detail="host must be a string"
        )

    if not isinstance(port, int) or isinstance(port, bool):

        raise HTTPException(
            status_code=400,
            detail="port must be an integer"
        )

    if api_key is not None and not isinstance(api_key, str):

        raise HTTPException(
            status_code=400,
            detail="api_key must be a string"
        )

    with COMPILE_LOCK:

        commands = generate_curl_commands(
            str(full_path), host=host, port=port, api_key=api_key
        )

    return {
        "status": "success",
        "notebook": notebook_path,
        "version_id": version_id,
        "commands": commands,
    }


@router.post("/compile")
def compile_notebook_endpoint(
    data: dict
):
    """Compile notebook to API.

    "only"/"exclude" (each an optional list of function names) restrict
    which functions become endpoints, the same --only/--exclude the CLI's
    own local `compile`, `deploy`, `serve`, and `watch` commands already
    accept (see _filter_functions_by_name, backend/compiler.py) -- before
    this, a notebook uploaded to a running dashboard and compiled via this
    endpoint (or the CLI's `remote-compile`, which calls it) always
    exposed every function as its own endpoint, with no way to keep a
    slow or still-broken function's endpoint out of the compiled app
    short of deleting it from the notebook outright, then re-uploading.
    """

    notebook_path = data.get(
        "notebook_path"
    )

    if not notebook_path:

        raise HTTPException(
            status_code=400,
            detail="notebook_path is required"
        )

    only = data.get("only")
    exclude = data.get("exclude")

    # Mirrors the "tag"/"platform" string-type checks POST /api/deploy
    # already makes on its own client-supplied fields: `only`/`exclude`
    # flow straight into set(...) inside _filter_functions_by_name below,
    # so a non-list value (a bare string, a number, ...) would otherwise
    # either misbehave silently (a string iterates character-by-character)
    # or crash with an unhandled TypeError, instead of a clean 400.
    for field_name, field_value in (("only", only), ("exclude", exclude)):

        if field_value is not None and (
            not isinstance(field_value, list)
            or not all(isinstance(item, str) for item in field_value)
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a list of strings"
            )

    if only and exclude:

        raise HTTPException(
            status_code=400,
            detail="only and exclude can't both be given -- choose one."
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
            GENERATED_DIR,
            only=only,
            exclude=exclude,
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

        # inspect_notebook_data re-parses the notebook fresh, with no idea
        # only/exclude just restricted which functions the compile above
        # actually turned into endpoints -- the same fix-up the CLI's own
        # `compile --json --only ...` already applies to its identical
        # inspect_notebook_data call (see _dispatch_core_command in
        # cli.py). Without it, this response's "functions"/"endpoints"
        # would list every *other* function the notebook defines too,
        # claiming endpoints exist for functions the compiled app doesn't
        # actually have.
        if only or exclude:

            data["functions"] = _filter_functions_by_name(
                data["functions"], only, exclude
            )

            kept_names = {func["name"] for func in data["functions"]}

            data["endpoints"] = [
                endpoint for endpoint in data["endpoints"]
                if endpoint["path"].lstrip("/") in kept_names
            ]

        _append_compile_history_entry({
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "notebook_filename": notebook_path,
            "source_notebook_sha256": hash_notebook_file(str(full_path)),
            "only": only,
            "exclude": exclude,
            "endpoint_count": len(data["endpoints"]),
            "dependency_count": len(data["dependencies"]),
            "skipped_function_count": len(data["skipped_functions"]),
        })

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

    except ValueError as e:

        # _filter_functions_by_name (backend/compiler.py) raises this for
        # an only/exclude name this notebook doesn't actually define --
        # the request's own fault, not this server's, so this is a 400
        # the caller can act on (fix the typo'd name and recompile), not
        # a 500. Also covers compile_notebook_to_api's own "only and
        # exclude can't both be given" ValueError, as a defense-in-depth
        # backstop behind the identical check already made above, before
        # the compile even starts. Ordered after ReservedFunctionNameError
        # and MALFORMED_NOTEBOOK_ERRORS -- both include ValueError
        # subclasses of their own (see their definitions) -- so this only
        # ever catches a "plain" ValueError neither of those already
        # handled with its own, more specific message.
        raise HTTPException(
            status_code=400,
            detail=str(e)
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


def _deploy_history_path() -> Path:
    """Path to the hidden JSON file recording this dashboard's own past
    POST /api/deploy invocations.

    Stored directly inside UPLOAD_DIR (a hidden ".deploy_history.json",
    the same hidden-sidecar-file convention _tags_sidecar_path and
    _description_sidecar_path already use for their own per-notebook
    metadata), not inside GENERATED_DIR: every successful POST
    /api/compile already clears GENERATED_DIR down to a fresh build via
    clear_stale_export_artifacts (backend/compiler.py), and DELETE
    /api/generated removes it entirely -- neither should also wipe out a
    dashboard's own deploy history, which is bookkeeping about *this
    dashboard's* past deploys, not part of any one compile's own output.
    Unlike the per-notebook tags/description sidecars, this one isn't
    keyed to any single notebook's filename: a dashboard's deploy history
    spans every notebook ever compiled and deployed through it, so it
    isn't moved/copied/removed by rename_notebook/copy_notebook/
    delete_notebook the way those are.
    """
    return Path(UPLOAD_DIR) / ".deploy_history.json"


def _read_deploy_history() -> list:
    """This dashboard's own past deploys, oldest first -- the same
    fixed-width list _append_deploy_history_entry below maintains, or []
    if nothing has ever been deployed yet, or the history file is
    missing/unreadable/corrupt. Deploy history is optional, best-effort
    bookkeeping, the same way a notebook's tags already are (see
    _read_notebook_tags) -- a bad history file should never break POST
    /api/deploy or GET /api/deploy/history over it.
    """
    history_path = _deploy_history_path()

    if not history_path.is_file():
        return []

    try:

        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    except (OSError, ValueError):
        return []

    entries = data.get("entries")

    if not isinstance(entries, list):
        return []

    return entries


def _append_deploy_history_entry(entry: dict) -> None:
    """Record `entry` as this dashboard's most recent deploy, keeping at
    most the MAX_DEPLOY_HISTORY_ENTRIES most recent entries -- the oldest
    is dropped once that many already exist, the same bounded-history
    policy _prune_notebook_versions already applies to a single
    notebook's own version snapshots, just applied to deploy history
    instead of file snapshots.

    Writes via a temp-file-then-os.replace swap in UPLOAD_DIR -- the
    identical atomic-write pattern _write_notebook_tags already uses --
    so a concurrent GET /api/deploy/history never observes a
    partially-written history file.
    """
    entries = _read_deploy_history()
    entries.append(entry)
    entries = entries[-MAX_DEPLOY_HISTORY_ENTRIES:]

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_path = upload_root / f".deploy_history.{uuid.uuid4().hex}.part"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f)

    os.replace(temp_path, _deploy_history_path())


def _compile_history_path() -> Path:
    """Path to the hidden JSON file recording this dashboard's own past
    POST /api/compile invocations.

    Stored directly inside UPLOAD_DIR, the identical hidden-sidecar-file
    convention _deploy_history_path already uses for this dashboard's
    deploy history -- for the same reason: every successful compile
    already clears GENERATED_DIR down to a fresh build via
    clear_stale_export_artifacts (backend/compiler.py), and DELETE
    /api/generated removes it entirely, neither of which should also
    wipe out a dashboard's own compile history. .compile_metadata.json
    (write_compile_metadata, backend/compiler.py) only ever tracks the
    single most recent compile's own source notebook -- there was
    previously nowhere a dashboard recorded that a compile had happened
    at all once superseded by the next one, the same gap
    _deploy_history_path's own docstring already named for deploys before
    GET /api/deploy/history closed it.
    """
    return Path(UPLOAD_DIR) / ".compile_history.json"


def _read_compile_history() -> list:
    """This dashboard's own past compiles, oldest first -- the same
    fixed-width list _append_compile_history_entry below maintains, or []
    if nothing has ever been compiled through this dashboard yet, or the
    history file is missing/unreadable/corrupt. Compile history is
    optional, best-effort bookkeeping, the identical reasoning
    _read_deploy_history already applies to deploy history -- a bad
    history file should never break POST /api/compile or GET
    /api/compile/history over it.
    """
    history_path = _compile_history_path()

    if not history_path.is_file():
        return []

    try:

        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    except (OSError, ValueError):
        return []

    entries = data.get("entries")

    if not isinstance(entries, list):
        return []

    return entries


def _append_compile_history_entry(entry: dict) -> None:
    """Record `entry` as this dashboard's most recent compile, keeping at
    most the MAX_COMPILE_HISTORY_ENTRIES most recent entries -- the same
    bounded-history, oldest-dropped-first policy _append_deploy_history_
    entry already applies to deploy history, just applied to compile
    history instead.

    Writes via the identical temp-file-then-os.replace atomic swap in
    UPLOAD_DIR _append_deploy_history_entry itself already uses, so a
    concurrent GET /api/compile/history never observes a
    partially-written history file.
    """
    entries = _read_compile_history()
    entries.append(entry)
    entries = entries[-MAX_COMPILE_HISTORY_ENTRIES:]

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_path = upload_root / f".compile_history.{uuid.uuid4().hex}.part"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f)

    os.replace(temp_path, _compile_history_path())


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

    Every successful deploy (a successful build, and -- with "push": true
    -- a successful push) is also appended to this dashboard's own
    persistent deploy history (see _append_deploy_history_entry above),
    readable back via GET /api/deploy/history. Before this, a POST
    /api/deploy's own result was only ever visible in that one request's
    response, gone the moment it scrolled off a terminal or wasn't
    otherwise recorded by the caller -- an operator asking "when was this
    last deployed, with what tag, was it actually pushed" had nothing on
    this dashboard itself to answer that; .compile_metadata.json only
    ever tracks the most recent *compile*, not any deploy.
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

        compiled_path, compiled_sha256, _ = _currently_compiled_notebook_metadata()

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

    source_notebook_filename = None

    if compiled_path is not None:

        try:
            source_notebook_filename = str(
                compiled_path.relative_to(Path(UPLOAD_DIR).resolve())
            )
        except ValueError:
            source_notebook_filename = None

    _append_deploy_history_entry({
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "tag": tag,
        "platform": platform,
        "pushed": response["pushed"],
        "source_notebook_filename": source_notebook_filename,
        "source_notebook_sha256": compiled_sha256,
    })

    return response


@router.get("/deploy/history")
def deploy_history_endpoint(
    source_notebook_filename: str = None,
    source_notebook_sha256: str = None,
    platform: str = None,
    tag: str = None,
    pushed: bool = None,
    limit: int = None,
    offset: int = 0,
):
    """This dashboard's own past POST /api/deploy invocations, most
    recent first -- up to the last MAX_DEPLOY_HISTORY_ENTRIES.

    Every field mirrors exactly what the POST /api/deploy call that
    produced it either received or returned at the time: "tag" and
    "platform" are the request's own values (platform null when not
    given, the same way the request itself treats it), "pushed" is the
    response's own "pushed" flag, and "source_notebook_filename"/
    "source_notebook_sha256" identify which notebook (and which exact
    content of it) was actually compiled into the image that got built --
    the same "source_notebook_filename" GET /api/generated already
    resolves relative to UPLOAD_DIR, null if whatever was compiled came
    from outside UPLOAD_DIR entirely, or if nothing was compiled at all
    (confirmed unreachable in practice: POST /api/deploy itself already
    404s before ever reaching a build when no Dockerfile exists in
    GENERATED_DIR).

    "source_notebook_filename"/"platform"/"tag"/"pushed" filter the
    returned entries to ones with an exact match on that field before
    "offset"/"limit" are applied -- before this, a caller wanting only a
    specific notebook's own deploy history (e.g. to answer "when did we
    last deploy this one") had to fetch the entire log and filter it
    client-side, the same gap GET /api/notebooks' own "search"/"tag"
    query params already close for the notebook catalog.

    "tag" matches exactly against each entry's own "tag" -- the Docker
    image tag POST /api/deploy actually built (its own request "tag", or
    the "{generated_dir_name}:latest" default when omitted), not a
    notebook's category tag as GET /api/notebooks?tag= filters by. A
    caller who always deploys under one fixed image tag but rotates
    through several source notebooks now has an exact-match filter of
    its own, the same way "source_notebook_filename" already gives the
    inverse -- both compose with each other and every other filter
    below identically, since each only ever narrows by its own entry
    field.

    "source_notebook_sha256" matches exactly against each entry's own
    "source_notebook_sha256" -- the exact notebook *content* deployed,
    the same digest GET /api/notebooks/duplicates already groups uploads
    by and GET /api/compile/history's own "source_notebook_sha256" query
    param already filters compile history by -- rather than
    "source_notebook_filename"'s current name. A notebook can be renamed
    (PATCH /api/notebooks/{filename}) or overwritten (POST
    /api/upload?overwrite=true) between deploys, at which point
    "source_notebook_filename" alone can no longer answer "was *this
    exact content* ever actually deployed, and under what tag", since
    that content may now sit under a different filename, or several.
    Composes with every other filter here identically; an unknown or
    never-deployed hash simply yields no matching entries, not an error.

    "offset" (default 0) is how many of the (already-filtered) entries,
    still most-recent-first, to skip before "limit" is applied -- the
    identical pagination GET /api/notebooks' own "offset" already
    provides over the notebook catalog, closing the same gap here: without
    it, a caller paging through a deploy history longer than one page had
    no way to ask for "the next N" short of re-fetching everything up to
    that point via a larger "limit" and discarding the entries it had
    already seen. "limit" caps how many of the (already-offset) entries
    are returned, same "most recent first" ordering, without needing
    MAX_DEPLOY_HISTORY_ENTRIES raised or lowered just to change how many a
    single call gets back. A negative "offset" is rejected with 400, the
    same way a negative "limit" already is below.

    Deliberately read-only: this dashboard's deploy history is a record
    of what already happened, not something a caller edits or replays
    through this endpoint -- an entry age out only once
    MAX_DEPLOY_HISTORY_ENTRIES more deploys have happened since, the same
    fixed-window retention _prune_notebook_versions already applies to a
    single notebook's own version snapshots.
    """

    entries = list(reversed(_read_deploy_history()))

    if source_notebook_filename is not None:
        entries = [
            entry for entry in entries
            if entry.get("source_notebook_filename") == source_notebook_filename
        ]

    if source_notebook_sha256 is not None:
        entries = [
            entry for entry in entries
            if entry.get("source_notebook_sha256") == source_notebook_sha256
        ]

    if platform is not None:
        entries = [entry for entry in entries if entry.get("platform") == platform]

    if tag is not None:
        entries = [entry for entry in entries if entry.get("tag") == tag]

    if pushed is not None:
        entries = [entry for entry in entries if entry.get("pushed") == pushed]

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    entries = entries[offset:]

    if limit is not None:

        if limit < 0:

            raise HTTPException(
                status_code=400,
                detail="limit must be a non-negative integer"
            )

        entries = entries[:limit]

    return {
        "status": "success",
        "entries": entries,
        "entry_count": len(entries),
    }


@router.delete("/deploy/history")
def clear_deploy_history(source_notebook_filename: str = None):
    """Permanently discard this dashboard's deploy history log.

    GET /api/deploy/history's own docstring already notes it's read-only
    -- no way to edit or replay an individual entry through it -- but that
    left no way to discard the log *as a whole* either, short of an
    entry aging out only once MAX_DEPLOY_HISTORY_ENTRIES more deploys
    happen. An operator wanting to reclaim it sooner (e.g. before handing
    a dashboard instance off to someone else, or after a tag/platform
    value in it turns out to have been sensitive) had no way to act on
    that at all -- this dashboard's deploy history, unlike a notebook's
    own version history (DELETE /api/notebooks/{filename}/versions), had
    no bulk-clear equivalent whatsoever.

    Never touches GENERATED_DIR, whatever it currently backs, or any
    uploaded notebook's own tags/description/version history -- this
    dashboard's deploy history is bookkeeping about past POST /api/deploy
    calls, entirely independent of all of those (see
    _deploy_history_path's own docstring for why it isn't even stored
    alongside either one). A no-op success (not a 404) when nothing has
    ever been deployed yet -- "nothing to clear" is a valid outcome of a
    bulk operation like this, not an error, the same reasoning DELETE
    /api/notebooks/{filename}/versions' own no-op-for-no-history case
    already follows.

    "source_notebook_filename", when given, discards only entries with an
    exact match on that field (the same field GET /api/deploy/history's
    own "source_notebook_filename" already filters by) and leaves every
    other notebook's own deploy history entries in place -- the same
    all-or-nothing gap DELETE /api/compile/history's own
    "notebook_filename" query param already closes for compile history,
    just applied to deploy history instead: an operator wanting to drop
    just one deleted/renamed notebook's now-stale deploy history
    previously had no choice but to wipe every other notebook's history
    along with it. Omitted, this clears the entire log exactly as before.
    """

    entries = _read_deploy_history()

    if source_notebook_filename is not None:

        remaining = [
            entry for entry in entries
            if entry.get("source_notebook_filename") != source_notebook_filename
        ]
        deleted_count = len(entries) - len(remaining)

        if remaining:

            upload_root = Path(UPLOAD_DIR).resolve()
            temp_path = upload_root / f".deploy_history.{uuid.uuid4().hex}.part"

            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"entries": remaining}, f)

            os.replace(temp_path, _deploy_history_path())

        else:
            _deploy_history_path().unlink(missing_ok=True)

        return {
            "status": "success",
            "deleted_count": deleted_count,
        }

    _deploy_history_path().unlink(missing_ok=True)

    return {
        "status": "success",
        "deleted_count": len(entries),
    }


@router.get("/compile/history")
def compile_history_endpoint(
    notebook_filename: str = None,
    source_notebook_sha256: str = None,
    limit: int = None,
    offset: int = 0,
):
    """This dashboard's own past POST /api/compile invocations, most
    recent first -- up to the last MAX_COMPILE_HISTORY_ENTRIES.

    .compile_metadata.json (write_compile_metadata, backend/compiler.py)
    only ever tracks the single most recent compile's own source
    notebook -- overwritten by the very next one, the same way
    GENERATED_DIR's own contents are. An operator asking "how many times
    has this notebook been recompiled", "when did we last compile with
    --exclude", or "did compiling this notebook actually change what it
    exposed" had nothing on the dashboard itself to answer that once a
    later compile had superseded the one they cared about -- the exact
    gap GET /api/deploy/history already closed for this dashboard's past
    deploys, just for compiles instead.

    Every field mirrors exactly what the POST /api/compile call that
    produced it either received or computed at the time:
    "notebook_filename" is the request's own "notebook_path",
    "source_notebook_sha256" is that notebook's exact content at compile
    time (see hash_notebook_file, backend/compiler.py -- the same digest
    GET /api/notebooks/duplicates and GET /api/notebooks' own
    "notebook_changed_since_compile" already use), "only"/"exclude" are
    the request's own values (null when neither was given), and
    "endpoint_count"/"dependency_count"/"skipped_function_count" are
    computed from that compile's own response.

    "notebook_filename"/"offset"/"limit" filter and page the returned
    entries the identical way GET /api/deploy/history's own
    "source_notebook_filename"/"offset"/"limit" query params already do
    for deploy history -- an operator wanting only one specific notebook's
    own compile history (e.g. "how many times has this one actually been
    recompiled") had to fetch the entire log and filter it client-side
    otherwise. "notebook_filename" matches exactly against each entry's
    own "notebook_filename" (a 404-free filter -- an unknown or
    never-compiled filename just yields no matching entries, not an
    error); "offset" (default 0) skips this many of the matching entries,
    still most-recent-first, before "limit" is applied, the same
    "next page" gap GET /api/deploy/history's own "offset" already closes;
    "limit" applies after that, on the same most-recent-first ordering. A
    negative "offset" is rejected with 400, the same way a negative
    "limit" already is below.

    "source_notebook_sha256" matches exactly against each entry's own
    "source_notebook_sha256" -- the exact notebook *content* compiled,
    the same digest GET /api/notebooks/duplicates already groups uploads
    by -- rather than "notebook_filename"'s current name. A notebook can
    be renamed (PATCH /api/notebooks/{filename}) or overwritten
    (POST /api/upload?overwrite=true) between compiles, at which point
    "notebook_filename" alone can no longer answer "was *this exact
    content* -- the bytes I'm holding right now, or that GET
    /api/notebooks/duplicates just grouped under this hash -- ever
    actually compiled, and with what result", since that content may now
    sit under a different filename, or several. Composes with
    "notebook_filename"/"offset"/"limit" identically to every other
    filter here; an unknown or never-compiled hash simply yields no
    matching entries, not an error, the same as "notebook_filename".

    Deliberately read-only, the same reasoning GET /api/deploy/history's
    own docstring already gives: this dashboard's compile history is a
    record of what already happened, not something a caller edits or
    replays through this endpoint. Only recorded for POST /api/compile
    (a dashboard-tracked compile) -- the CLI's own local `compile` never
    touches a dashboard's UPLOAD_DIR at all, so it has nothing to record
    this history into.
    """

    entries = list(reversed(_read_compile_history()))

    if notebook_filename is not None:
        entries = [
            entry for entry in entries
            if entry.get("notebook_filename") == notebook_filename
        ]

    if source_notebook_sha256 is not None:
        entries = [
            entry for entry in entries
            if entry.get("source_notebook_sha256") == source_notebook_sha256
        ]

    if offset < 0:

        raise HTTPException(
            status_code=400,
            detail="offset must be a non-negative integer"
        )

    entries = entries[offset:]

    if limit is not None:

        if limit < 0:

            raise HTTPException(
                status_code=400,
                detail="limit must be a non-negative integer"
            )

        entries = entries[:limit]

    return {
        "status": "success",
        "entries": entries,
        "entry_count": len(entries),
    }


@router.delete("/compile/history")
def clear_compile_history(notebook_filename: str = None):
    """Permanently discard this dashboard's compile history log, the exact
    counterpart DELETE /api/deploy/history already provides for this
    dashboard's deploy history.

    An operator wanting to reclaim it sooner than
    MAX_COMPILE_HISTORY_ENTRIES more compiles would age it out on its own
    (e.g. before handing a dashboard instance off to someone else) had no
    way to act on that at all. Never touches GENERATED_DIR, whatever it
    currently backs, .compile_metadata.json, or any uploaded notebook's
    own tags/description/version history -- compile history is
    bookkeeping about past POST /api/compile calls, entirely independent
    of all of those. A no-op success (not a 404) when nothing has ever
    been compiled through this dashboard yet, the same "nothing to clear
    is still a valid outcome" reasoning DELETE /api/deploy/history's own
    no-op-for-no-history case already follows.

    "notebook_filename", when given, discards only entries with an exact
    match on that field (the same field GET /api/compile/history's own
    "notebook_filename" already filters by) and leaves every other
    notebook's own compile history entries in place -- before this, an
    operator wanting to drop just one notebook's stale history (e.g.
    after it was deleted or renamed) had no choice but to wipe the whole
    log, discarding every other notebook's history along with it, the
    same all-or-nothing gap DELETE /api/notebooks/{filename}/versions
    already solves for a single notebook's own version snapshots (as
    opposed to DELETE /api/notebooks/versions' own catalog-wide prune).
    Omitted, this clears the entire log exactly as before.
    """

    entries = _read_compile_history()

    if notebook_filename is not None:

        remaining = [
            entry for entry in entries
            if entry.get("notebook_filename") != notebook_filename
        ]
        deleted_count = len(entries) - len(remaining)

        if remaining:

            upload_root = Path(UPLOAD_DIR).resolve()
            temp_path = upload_root / f".compile_history.{uuid.uuid4().hex}.part"

            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"entries": remaining}, f)

            os.replace(temp_path, _compile_history_path())

        else:
            _compile_history_path().unlink(missing_ok=True)

        return {
            "status": "success",
            "deleted_count": deleted_count,
        }

    _compile_history_path().unlink(missing_ok=True)

    return {
        "status": "success",
        "deleted_count": len(entries),
    }


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

    Unlike POST /api/deploy, this never refuses a stale build outright
    (there's no "force" escape hatch to speak of, and a zip download --
    unlike a Docker build -- doesn't ship the stale build anywhere on its
    own): a caller downloading the zip to poke around locally,
    intentionally re-fetching a known-good previous compile, or simply
    not caring yet has no reason to be blocked. The
    "X-Notebook-Changed-Since-Compile" response header instead reports
    the identical _currently_compiled_notebook_is_stale() check
    POST /api/deploy already makes before building, as "true"/"false", so
    a caller who *does* care (like this CLI's own `remote-build`, which
    warns on it) can act on it without a separate, redundant GET
    /api/notebooks call just to read back the currently-compiled entry's
    own "notebook_changed_since_compile" field.
    """

    generated_path = Path(GENERATED_DIR)

    # Held while reading generated_path (see COMPILE_LOCK in
    # backend/compiler.py) so a concurrent POST /api/compile can't
    # rewrite it mid-walk, which could zip up a torn mix of files from
    # the old and new compile instead of one consistent output. Also
    # covers the staleness check just below, for the same reason: without
    # it, a concurrent recompile could race between the zip being built
    # and the staleness check running, reporting a header that no longer
    # matches the bytes just zipped.
    with COMPILE_LOCK:

        if not (generated_path / "app.py").is_file():

            raise HTTPException(
                status_code=404,
                detail="No compiled app found. Run /api/compile first."
            )

        is_stale = _currently_compiled_notebook_is_stale()

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
            ),
            "X-Notebook-Changed-Since-Compile": "true" if is_stale else "false",
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


@router.get("/config")
def get_config():
    """Expose this dashboard's own configured limits and accepted
    parameter values, so a client (a frontend, or a script driving this
    API directly) can adapt to them instead of hardcoding a guess that
    silently drifts from whatever the server is actually enforcing.

    Before this, every one of these limits was only discoverable the hard
    way: attempt the operation and read the resulting error. A dashboard
    frontend wanting to reject an oversized file before even starting the
    upload (rather than waiting on a real request just to get back the
    same 413 POST /api/upload already raises), grey out a batch-upload
    "add more files" control once MAX_BATCH_UPLOAD_FILES is reached, warn
    a user approaching PUT /api/notebooks/{filename}/tags' own per-tag or
    per-notebook caps, or populate a "sort by" control from
    GET /api/notebooks' own actual accepted "sort"/"order" values instead
    of a second, hardcoded copy of _NOTEBOOK_SORT_KEYS/_NOTEBOOK_SORT_ORDERS
    that could drift out of sync with this file's own definitions -- had
    no way to know any of this ahead of time.

    Every one of these is already independently configurable via its own
    NOTEBOOK_API_* environment variable (see each constant's own comment
    above) -- this endpoint doesn't add any new configuration, only a way
    to read back whatever was actually configured, in one place, without
    an operator or a client needing separate access to the server's own
    environment to know what it is.

    "max_deploy_history_entries" and "max_compile_history_entries" (added
    alongside this same docstring's original feature, not a separate
    change) close the identical gap for GET /api/deploy/history and GET
    /api/compile/history's own retention window: both were already
    independently configurable via NOTEBOOK_API_MAX_DEPLOY_HISTORY_ENTRIES/
    NOTEBOOK_API_MAX_COMPILE_HISTORY_ENTRIES since the endpoints
    themselves were added, but this endpoint predated both and never
    picked them up -- a caller wanting to know "how far back can this
    dashboard's own compile/deploy history actually go" (e.g. before
    deciding whether to also archive it elsewhere) had no way to ask
    that short of reading the server's own environment directly, the
    exact access this endpoint's own docstring already says a client
    shouldn't need.

    Deliberately omits UPLOAD_DIR and GENERATED_DIR: those are absolute
    (or process-relative) filesystem paths on the compiling server, the
    same category of information GET /api/health's own docstring already
    explains has no business leaking out of a dashboard API response.
    """

    return {
        "status": "success",
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_batch_upload_files": MAX_BATCH_UPLOAD_FILES,
        "max_notebook_versions": MAX_NOTEBOOK_VERSIONS,
        "max_tag_length": _MAX_TAG_LENGTH,
        "max_tags_per_notebook": _MAX_TAGS_PER_NOTEBOOK,
        "max_description_length": _MAX_DESCRIPTION_LENGTH,
        "max_deploy_history_entries": MAX_DEPLOY_HISTORY_ENTRIES,
        "max_compile_history_entries": MAX_COMPILE_HISTORY_ENTRIES,
        "deploy_subprocess_timeout_seconds": DEPLOY_SUBPROCESS_TIMEOUT_SECONDS,
        "notebook_sort_keys": sorted(_NOTEBOOK_SORT_KEYS),
        "notebook_sort_orders": sorted(_NOTEBOOK_SORT_ORDERS),
    }