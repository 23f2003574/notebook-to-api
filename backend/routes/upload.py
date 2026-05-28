from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import shutil
import os
from pathlib import Path
from backend.compiler import compile_notebook
from backend.parser.notebook_parser import load_notebook, extract_code_cells
from backend.parser.ast_parser import extract_functions_from_code

router = APIRouter(prefix="/api", tags=["dashboard"])

# Create uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload")
async def upload_notebook(file: UploadFile = File(...)):
    """Upload a Jupyter notebook file."""
    if not file.filename.endswith(".ipynb"):
        raise HTTPException(status_code=400, detail="File must be a .ipynb notebook")

    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return {
            "status": "success",
            "filename": file.filename,
            "path": file_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compile")
async def compile_notebook_endpoint(data: dict):
    """Compile a notebook to FastAPI app."""
    notebook_path = data.get("notebook_path")
    
    if not notebook_path:
        raise HTTPException(status_code=400, detail="notebook_path is required")

    full_path = os.path.join(UPLOAD_DIR, notebook_path)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Notebook file not found")

    try:
        # Load and parse the notebook
        notebook = load_notebook(full_path)
        code_cells = extract_code_cells(notebook)
        
        # Extract functions
        functions = []
        for cell in code_cells:
            funcs = extract_functions_from_code(cell)
            functions.extend(funcs)

        # Compile the notebook
        compile_notebook(full_path, "generated")

        return {
            "status": "success",
            "notebook": notebook_path,
            "functions": [
                {
                    "name": func.name,
                    "type": "function",
                    "params": [arg.arg for arg in func.args.args] if hasattr(func, 'args') else [],
                    "return_type": "Any"
                }
                for func in functions
            ],
            "message": "Notebook compiled successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Compilation error: {str(e)}")


@router.get("/health")
async def health_check():
    """Check if the API is running."""
    return {"status": "healthy", "service": "notebook-to-api"}
