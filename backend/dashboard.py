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
from backend.plugins.plugin_loader import router as plugin_loader_router
from backend.plugins.plugin_lifecycle import router as plugin_lifecycle_router
from backend.plugins.extension_api import router as extension_api_router
from backend.plugins.event_system import router as event_system_router
from backend.plugins.plugin_dependencies import router as plugin_dependencies_router
from backend.plugins.plugin_config import router as plugin_config_router
from backend.plugins.plugin_sandbox import router as plugin_sandbox_router
from backend.plugins.plugin_packaging import router as plugin_packaging_router
from backend.plugins.plugin_marketplace import router as plugin_marketplace_router
from backend.plugins.plugin_analytics import router as plugin_analytics_router
from backend.plugins.dashboard import router as plugin_dashboard_router
from backend.plugins.plugin_registry import router as plugin_registry_router

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

# Plugin subsystem
# Route ordering matters here: plugin_registry_router's catch-all
# "/plugins/{name}" route must be included LAST, since any other router
# adding a static second-segment path under "/plugins/" (e.g. "/loaded",
# "/extensions") would otherwise be shadowed by it.
#   - loader's static "/plugins/loaded" would otherwise be shadowed by the
#     registry's "/plugins/{name}" route.
#   - lifecycle's "DELETE /plugins/{plugin}" (uninstall) intentionally takes
#     over that path from the registry's plain "DELETE /plugins/{name}"
#     (unregister), since uninstall unloads the plugin and unregisters it
#     from the catalog as part of the same transition.
#   - extension_api's static "/plugins/extensions" would otherwise be
#     shadowed by the registry's "/plugins/{name}" route.
#   - event_system's static "/plugins/events" would otherwise be shadowed
#     by the registry's "/plugins/{name}" route (its "/plugins/hooks" and
#     "/plugins/hooks/{hook}" paths don't collide with anything).
#   - plugin_dependencies's static "/plugins/load-order" would otherwise be
#     shadowed by the registry's "/plugins/{name}" route (its
#     "/plugins/dependencies" and "/plugins/dependencies/{plugin}" paths
#     don't collide with anything).
app.include_router(plugin_loader_router)
app.include_router(plugin_lifecycle_router)
app.include_router(extension_api_router)
app.include_router(event_system_router)
app.include_router(plugin_dependencies_router)
# plugin_config's routes are all 3+ segments ("/plugins/{plugin}/config",
# "/plugins/{plugin}/config/validate"), so unlike the routers above it
# doesn't collide with the registry's "/plugins/{name}" and can be included
# in any order relative to it.
app.include_router(plugin_config_router)
# plugin_sandbox's routes are all under "/plugins/sandbox" (2+ segments,
# with "sandbox" as a static literal), and the registry has no POST route
# at "/plugins/{name}" for it to collide with, so this is order-independent
# relative to plugin_registry_router too.
app.include_router(plugin_sandbox_router)
# plugin_packaging's routes are "POST /plugins/package", "GET
# /plugins/package/{plugin}", and "POST /plugins/import" - the registry has
# no POST route at "/plugins/{name}" and its GET route is 2 segments (not 3),
# so this is order-independent relative to plugin_registry_router too.
app.include_router(plugin_packaging_router)
# plugin_marketplace's bare "GET /plugins/marketplace" would otherwise be
# shadowed by the registry's "/plugins/{name}" route (its "/search",
# "/install", and "/update/{plugin}" sub-paths don't collide with anything).
app.include_router(plugin_marketplace_router)
# plugin_analytics's bare "GET /plugins/analytics" would otherwise be
# shadowed by the registry's "/plugins/{name}" route (its "/summary" and
# "/trends" sub-paths don't collide with anything).
app.include_router(plugin_analytics_router)
# plugin_dashboard's bare "GET /plugins/dashboard" would otherwise be
# shadowed by the registry's "/plugins/{name}" route (its "/registry",
# "/runtime", "/marketplace", and "/analytics" sub-paths don't collide with
# anything).
app.include_router(plugin_dashboard_router)
app.include_router(plugin_registry_router)


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
