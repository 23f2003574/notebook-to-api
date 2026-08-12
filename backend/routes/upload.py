from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from datetime import datetime, timezone
import io
import json
import os
import subprocess
import uuid
import zipfile

from backend.compiler import (
    COMPILE_METADATA_FILENAME,
    compile_notebook,
    hash_notebook_file,
    package_name_for_output_dir,
)
from backend.generator.api_generator import (
    LONG_RUNNING_KEYWORDS,
    ReservedFunctionNameError,
)
from backend.inspector import EXCLUDED_GENERATED_DIR_NAMES, inspect_notebook_data
from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells,
)
from backend.parser.ast_parser import (
    extract_functions_from_code,
    deduplicate_functions_by_name,
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

router = APIRouter(
    prefix="/api",
    tags=["dashboard"]
)

UPLOAD_DIR = "uploads"
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


def resolve_upload_path(name: str) -> Path:
    """Resolve `name` against UPLOAD_DIR, rejecting anything that would
    escape it.

    `name` comes straight from client input (an uploaded file's filename,
    or a notebook_path field in a JSON body). Both os.path.join and
    pathlib's `/` operator discard the left-hand side entirely when the
    right-hand side is absolute (`Path("uploads") / "/etc/passwd" ==
    Path("/etc/passwd")`), and plain `../` segments escape just as
    easily. Without this check, /upload allows writing arbitrary files
    outside UPLOAD_DIR, and /inspect and /compile allow reading them
    (confirmed: both were exploitable before this check existed).
    """

    if not name or Path(name).is_absolute():
        raise HTTPException(
            status_code=400,
            detail="Invalid path: must be a relative filename within the uploads directory"
        )

    upload_root = Path(UPLOAD_DIR).resolve()
    candidate = (upload_root / name).resolve()

    try:
        candidate.relative_to(upload_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid path: must stay within the uploads directory"
        )

    return candidate


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


@router.get("/notebooks")
async def list_notebooks():
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
    """

    upload_root = Path(UPLOAD_DIR)

    compiled_path, compiled_sha256, compiled_at = _currently_compiled_notebook_metadata()

    notebooks = []

    for entry in sorted(upload_root.iterdir()):

        if entry.is_file() and entry.suffix == ".ipynb":

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

            notebooks.append(notebook_entry)

    return {
        "status": "success",
        "notebooks": notebooks,
    }


@router.delete("/notebooks/{filename}")
async def delete_notebook(filename: str):
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
async def get_notebook(filename: str):
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


@router.post("/inspect")
async def inspect_notebook_endpoint(
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

    if not full_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    try:

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
async def compile_notebook_endpoint(
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

    if not full_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    try:

        notebook = load_notebook(
            str(full_path)
        )

        code_cells = extract_code_cells(
            notebook
        )

        functions = []

        for cell in code_cells:

            funcs = extract_functions_from_code(
                cell
            )

            functions.extend(funcs)

        functions = deduplicate_functions_by_name(functions)

        compile_notebook(
            str(full_path),
            GENERATED_DIR
        )

        endpoints = []

        for func in functions:

            if isinstance(func, dict):

                name = func.get("name")

                if name:

                    # Mirrors generate_fastapi_code's own is_background
                    # check (generator/api_generator.py) exactly, so this
                    # never drifts from which endpoints the compiled app
                    # actually generates as background/task_id-based vs
                    # synchronous. Before this, a caller building a UI
                    # from /api/compile's response (rather than the
                    # separately-fetched OpenAPI schema, which already
                    # marks these with x-notebook-to-api-async) had no way
                    # to tell the two apart short of re-implementing this
                    # same keyword check itself.
                    is_async = any(
                        kw in name.lower()
                        for kw in LONG_RUNNING_KEYWORDS
                    )

                    endpoints.append({
                        "path": f"/{name}",
                        "method": "POST",
                        "is_async": is_async,
                    })

        return {
            "status": "success",
            "notebook": notebook_path,
            "functions": functions,
            "endpoints": endpoints,
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

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Compilation error: {str(e)}"
        )

@router.post("/export-openapi")
async def export_openapi_endpoint(
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

        package_name = package_name_for_output_dir(GENERATED_DIR)

        export_openapi_schema(
            output_path,
            package_name,
            format=export_format
        )

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

    with open(output_path, "r", encoding="utf-8") as f:
        content = f.read()

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
async def export_sdk_endpoint(
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

    openapi_path = os.path.join(GENERATED_DIR, "openapi.json")

    if not os.path.isfile(openapi_path):

        raise HTTPException(
            status_code=404,
            detail="No exported OpenAPI schema found. Run /api/export-openapi first."
        )

    if language == "typescript":
        output_path = os.path.join(GENERATED_DIR, "sdk", "typescript_client.ts")
    else:
        output_path = os.path.join(GENERATED_DIR, "sdk", "python_client.py")

    try:

        if language == "typescript":
            generate_typescript_sdk(openapi_path, output_path)
        else:
            generate_python_sdk(openapi_path, output_path)

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
async def deploy_generated_app(data: dict = None):
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

    if not (generated_path / "Dockerfile").is_file():

        raise HTTPException(
            status_code=404,
            detail="No compiled app found. Run /api/compile first."
        )

    data = data or {}

    force = bool(data.get("force", False))

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

    tag = data.get("tag") or f"{generated_path.name.lower()}:latest"
    push = bool(data.get("push", False))

    build_result = _run_docker_command(
        ["docker", "build", "-t", tag, "."], generated_path
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
async def download_generated_app():
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
    """

    generated_path = Path(GENERATED_DIR)

    if not (generated_path / "app.py").is_file():

        raise HTTPException(
            status_code=404,
            detail="No compiled app found. Run /api/compile first."
        )

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        for file_path in sorted(generated_path.rglob("*")):

            if file_path.is_file() and not (
                EXCLUDED_GENERATED_DIR_NAMES & set(file_path.relative_to(generated_path).parts)
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


@router.get("/health")
async def health_check():

    return {
        "status": "healthy",
        "service": "notebook-to-api"
    }