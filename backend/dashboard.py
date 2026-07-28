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
from backend.observability.deployment_governance_api import (
    register_governance_metrics_middleware,
    router as governance_metrics_router,
    health_router as governance_health_router,
)
from backend.security.authentication import router as security_authentication_router
from backend.security.api_keys import router as security_api_keys_router
from backend.security.jwt_service import router as security_jwt_router
from backend.security.rbac import router as security_rbac_router
from backend.security.permissions import router as security_permissions_router
from backend.security.session_manager import router as security_session_router
from backend.security.audit_logs import router as security_audit_router
from backend.security.security_policy import router as security_policy_router
from backend.security.secrets import router as security_secrets_router
from backend.security.security_analytics import router as security_analytics_router
from backend.security.dashboard import router as security_dashboard_router
from backend.security.export_service import router as security_export_router
from backend.security.bootstrap import bootstrap_security_subsystem

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

# Collect request metrics for the governance API endpoints
register_governance_metrics_middleware(app)

# Include API routes
app.include_router(upload_router)
app.include_router(governance_metrics_router)
app.include_router(governance_health_router)

# Security, Authentication & Access Control subsystem
app.include_router(security_authentication_router)
app.include_router(security_api_keys_router)
app.include_router(security_jwt_router)
app.include_router(security_rbac_router)
app.include_router(security_permissions_router)
app.include_router(security_session_router)
app.include_router(security_audit_router)
app.include_router(security_policy_router)
app.include_router(security_secrets_router)
app.include_router(security_analytics_router)
app.include_router(security_dashboard_router)
app.include_router(security_export_router)
bootstrap_security_subsystem()


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
