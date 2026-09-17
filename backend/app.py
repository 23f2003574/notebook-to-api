"""
Application runtime integration for the v2 Security, Authentication & Access
Control subsystem (commits 1-12).

Reuses the project's actual ASGI entry point, `app` from backend.dashboard,
rather than constructing a second FastAPI instance. The v2 routers are
mounted under an additional "/v2" prefix: seven of the twelve v2 routers
reuse the exact paths their commit-1-through-12 legacy counterparts already
occupy in backend.dashboard (e.g. both define "POST /security/roles"), and
Starlette resolves overlapping routes by first registration - so mounting
the v2 stack at the legacy paths would silently shadow it. "/v2" keeps both
stacks live and reachable with no shadowing.
"""

from backend.dashboard import app

from backend.security.identity_registry import router as identity_registry_router
from backend.security.auth_service import router as auth_service_router
from backend.security.jwt_manager import router as jwt_manager_router
from backend.security.session_lifecycle import router as session_lifecycle_router
from backend.security.rbac_engine import router as rbac_engine_router
from backend.security.permission_engine import router as permission_engine_router
from backend.security.api_key_manager import router as api_key_manager_router
from backend.security.secret_vault import router as secret_vault_router
from backend.security.audit_logger import router as audit_logger_router
from backend.security.security_metrics import router as security_metrics_router
from backend.security.ops_dashboard import router as ops_dashboard_router
from backend.security.security_export import router as security_export_router
from backend.security.security_bootstrap import bootstrap_security_subsystem

# Security, Authentication & Access Control subsystem (v2)
app.include_router(identity_registry_router, prefix="/v2")
app.include_router(auth_service_router, prefix="/v2")
app.include_router(jwt_manager_router, prefix="/v2")
app.include_router(session_lifecycle_router, prefix="/v2")
app.include_router(rbac_engine_router, prefix="/v2")
app.include_router(permission_engine_router, prefix="/v2")
app.include_router(api_key_manager_router, prefix="/v2")
app.include_router(secret_vault_router, prefix="/v2")
app.include_router(audit_logger_router, prefix="/v2")
app.include_router(security_metrics_router, prefix="/v2")
app.include_router(ops_dashboard_router, prefix="/v2")
app.include_router(security_export_router, prefix="/v2")
bootstrap_security_subsystem()

__all__ = ["app"]
