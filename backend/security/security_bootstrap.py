from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Optional

REQUIRED_SERVICES: tuple = (
    "identity_registry",
    "authentication_service",
    "jwt_token_manager",
    "session_manager",
    "rbac_engine",
    "permission_engine",
    "api_key_manager",
    "secret_vault_service",
    "security_audit_logger",
    "security_analytics_service",
    "dashboard_api",
    "export_service",
)

SUBSYSTEM_NAME = "security_authentication_access_control_v2"


class UnknownServiceError(KeyError):
    pass


class SecurityBootstrapError(RuntimeError):
    """Raised when the v2 security subsystem fails startup validation."""

    def __init__(self, missing_services: tuple, *, wired: bool = True) -> None:
        self.missing_services = missing_services
        self.wired = wired
        details = []
        if missing_services:
            details.append(f"missing: {', '.join(missing_services)}")
        if not wired:
            details.append("router prefixes not wired correctly")
        detail = f" ({'; '.join(details)})" if details else ""
        super().__init__("security subsystem v2 bootstrap validation failed" + detail)


class SecurityBootstrap:
    """Wires together every v2 Security, Authentication & Access Control service singleton."""

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._lock = Lock()

    def register_services(self) -> dict:
        from .identity_registry import get_identity_registry
        from .auth_service import get_authentication_service
        from .jwt_manager import get_jwt_token_manager
        from .session_lifecycle import get_session_manager as get_session_lifecycle_manager
        from .rbac_engine import get_rbac_engine
        from .permission_engine import get_permission_engine as get_fine_grained_permission_engine
        from .api_key_manager import get_api_key_manager as get_scoped_api_key_manager
        from .secret_vault import get_secret_vault_service
        from .audit_logger import get_security_audit_logger
        from .security_metrics import get_security_analytics_service as get_security_metrics_service
        from .ops_dashboard import get_security_dashboard_api as get_security_ops_dashboard_api
        from .security_export import get_security_export_service as get_security_export_bundle_service

        services = {
            "identity_registry": get_identity_registry(),
            "authentication_service": get_authentication_service(),
            "jwt_token_manager": get_jwt_token_manager(),
            "session_manager": get_session_lifecycle_manager(),
            "rbac_engine": get_rbac_engine(),
            "permission_engine": get_fine_grained_permission_engine(),
            "api_key_manager": get_scoped_api_key_manager(),
            "secret_vault_service": get_secret_vault_service(),
            "security_audit_logger": get_security_audit_logger(),
            "security_analytics_service": get_security_metrics_service(),
            "dashboard_api": get_security_ops_dashboard_api(),
            "export_service": get_security_export_bundle_service(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    def wire_components(self) -> bool:
        """Confirm every v2 security router is mounted under a "/security" path.

        There is no separate route-registration step to perform here: every
        endpoint from commits 1-12 is already registered, at import time, by
        each module's own @router.* decorators. This verifies every router
        keeps to the expected path convention, not a second registration.
        """
        from .identity_registry import router as identity_registry_router
        from .auth_service import router as auth_service_router
        from .jwt_manager import router as jwt_manager_router
        from .session_lifecycle import router as session_lifecycle_router
        from .rbac_engine import router as rbac_engine_router
        from .permission_engine import router as permission_engine_router
        from .api_key_manager import router as api_key_manager_router
        from .secret_vault import router as secret_vault_router
        from .audit_logger import router as audit_logger_router
        from .security_metrics import router as security_metrics_router
        from .ops_dashboard import router as ops_dashboard_router
        from .security_export import router as security_export_router

        routers = (
            identity_registry_router,
            auth_service_router,
            jwt_manager_router,
            session_lifecycle_router,
            rbac_engine_router,
            permission_engine_router,
            api_key_manager_router,
            secret_vault_router,
            audit_logger_router,
            security_metrics_router,
            ops_dashboard_router,
            security_export_router,
        )
        return all(router.prefix.startswith("/security") for router in routers)

    def initialize(self, *, timestamp: Optional[datetime] = None) -> dict:
        with self._lock:
            services = dict(self._services)
        if not services:
            services = self.register_services()

        wired = self.wire_components()
        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = {
            "valid": not missing and wired,
            "registered_services": tuple(sorted(services)),
            "missing_services": missing,
            "wired": wired,
            "checked_at": (timestamp or datetime.now(timezone.utc)).isoformat(),
        }
        if missing or not wired:
            raise SecurityBootstrapError(missing, wired=wired)
        return result

    def shutdown(self) -> None:
        with self._lock:
            self._services = {}


_security_bootstrap = SecurityBootstrap()


def get_security_bootstrap() -> SecurityBootstrap:
    return _security_bootstrap


def bootstrap_security_subsystem() -> dict:
    """Wire and validate the v2 Security, Authentication & Access Control subsystem.

    Safe to call more than once: each call re-registers the current singletons
    and re-runs validation.
    """
    return get_security_bootstrap().initialize()
