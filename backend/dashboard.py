"""
Dashboard API Server
Serves the React dashboard frontend and provides API endpoints for compilation
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from backend.routes.upload import router as upload_router

app = FastAPI(
    title="notebook-to-api Dashboard",
    description="Transform Jupyter notebooks into production APIs",
    version="0.1.0"
)

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:5174", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(upload_router)


@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "notebook-to-api Dashboard API",
        "version": "0.1.0",
        "docs": "/docs"
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.dashboard:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )
