from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil
import os

from backend.compiler import compile_notebook
from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells,
)
from backend.parser.ast_parser import (
    extract_functions_from_code,
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

    try:

        file_path = os.path.join(
            UPLOAD_DIR,
            file.filename
        )

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
            "path": file_path
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

    full_path = os.path.join(
        UPLOAD_DIR,
        notebook_path
    )

    if not os.path.exists(
        full_path
    ):

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    try:

        notebook = load_notebook(
            full_path
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

    full_path = os.path.join(
        UPLOAD_DIR,
        notebook_path
    )

    if not os.path.exists(
        full_path
    ):

        raise HTTPException(
            status_code=404,
            detail="Notebook file not found"
        )

    try:

        notebook = load_notebook(
            full_path
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

        compile_notebook(
            full_path,
            "generated"
        )

        return {
            "status": "success",
            "notebook": notebook_path,
            "functions": functions,
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