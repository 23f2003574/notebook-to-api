from fastapi import APIRouter, UploadFile, File, HTTPException
from pathlib import Path
import shutil
import os

from backend.compiler import compile_notebook
from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells,
)
from backend.parser.ast_parser import (
    extract_functions_from_code,
    deduplicate_functions_by_name,
)

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


@router.post("/inspect")
async def inspect_notebook_endpoint(
    data: dict
):
    """Inspect notebook and return extracted functions."""

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

        return {
            "status": "success",
            "functions": functions
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

@router.get("/health")
async def health_check():

    return {
        "status": "healthy",
        "service": "notebook-to-api"
    }