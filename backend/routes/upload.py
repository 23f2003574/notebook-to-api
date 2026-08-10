from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pathlib import Path
from datetime import datetime, timezone
import io
import json
import shutil
import os
import zipfile

from backend.compiler import compile_notebook, package_name_for_output_dir
from backend.inspector import inspect_notebook_data
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
GENERATED_DIR = "generated"

router = APIRouter(
    prefix="/api",
    tags=["dashboard"]
)

UPLOAD_DIR = "uploads"
os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
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
    file: UploadFile = File(...)
):
    """Upload a Jupyter notebook file."""

    if not file.filename.endswith(".ipynb"):

        raise HTTPException(
            status_code=400,
            detail="File must be a .ipynb notebook"
        )

    file_path = resolve_upload_path(file.filename)

    try:

        with open(
            file_path,
            "wb"
        ) as buffer:

            shutil.copyfileobj(
                file.file,
                buffer
            )

        return {
            "status": "success",
            "filename": file.filename,
            "path": str(file_path)
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.get("/notebooks")
async def list_notebooks():
    """List previously uploaded notebooks.

    /api/upload was previously a one-way door: notebooks could be
    uploaded but never listed or removed again through the API, so a
    dashboard frontend had no way to let a user pick a previously
    uploaded notebook without re-uploading it, and the uploads directory
    could only grow.
    """

    upload_root = Path(UPLOAD_DIR)

    notebooks = []

    for entry in sorted(upload_root.iterdir()):

        if entry.is_file() and entry.suffix == ".ipynb":

            entry_stat = entry.stat()

            notebooks.append({
                "filename": entry.name,
                "size_bytes": entry_stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    entry_stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })

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
    """

    file_path = resolve_upload_path(filename)

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
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
    }


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
            "generated"
        )

        endpoints = []

        for func in functions:

            if isinstance(func, dict):

                name = func.get("name")

                if name:

                    endpoints.append(
                        f"/{name}"
                    )

        return {
            "status": "success",
            "notebook": notebook_path,
            "functions": functions,
            "endpoints": endpoints,
            "message": "Notebook compiled successfully"
        }

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

            if file_path.is_file():

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