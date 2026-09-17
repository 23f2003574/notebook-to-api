from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

REQUIRED_SERVICES: tuple = (
    "authentication_manager",
    "api_key_manager",
    "jwt_service",
    "rbac",
    "permission_engine",
    "session_manager",
    "audit_log_service",
    "security_policy_engine",
    "secret_management_service",
    "security_analytics_service",
    "dashboard_api",
    "export_service",
)

SUBSYSTEM_NAME = "security_authentication_access_control"

DEFAULT_PERMISSIONS: tuple = (
    ("Viewer", "*", "Read"),
    ("Developer", "*", "Write"),
    ("Admin", "*", "Admin"),
    ("Service", "*", "Execute"),
)

DEFAULT_POLICY_NAMES: tuple = (
    "Password Strength",
    "MFA Required",
    "Session Timeout",
    "API Key Rotation",
)


class UnknownServiceError(KeyError):
    pass


@dataclass(frozen=True)
class SecurityBootstrapValidationResult:
    """One immutable outcome of validating the security subsystem's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class SecurityBootstrapError(RuntimeError):
    """Raised when the security subsystem fails startup validation."""

    def __init__(self, result: SecurityBootstrapValidationResult) -> None:
        self.result = result
        detail = (
            f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        )
        super().__init__("security subsystem bootstrap validation failed" + detail)


class SecurityBootstrap:
    """Wires together every Security, Authentication & Access Control service singleton and validates startup."""

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._lock = Lock()

    def register(self) -> dict:
        from .authentication import get_authentication_manager
        from .api_keys import get_api_key_manager
        from .jwt_service import get_jwt_token_service
        from .rbac import get_rbac
        from .permissions import get_permission_engine
        from .session_manager import get_session_manager
        from .audit_logs import get_audit_log_service
        from .security_policy import get_security_policy_engine
        from .secrets import get_secret_management_service
        from .security_analytics import get_security_analytics_service
        from .dashboard import get_security_dashboard_api
        from .export_service import get_security_export_service

        services = {
            "authentication_manager": get_authentication_manager(),
            "api_key_manager": get_api_key_manager(),
            "jwt_service": get_jwt_token_service(),
            "rbac": get_rbac(),
            "permission_engine": get_permission_engine(),
            "session_manager": get_session_manager(),
            "audit_log_service": get_audit_log_service(),
            "security_policy_engine": get_security_policy_engine(),
            "secret_management_service": get_secret_management_service(),
            "security_analytics_service": get_security_analytics_service(),
            "dashboard_api": get_security_dashboard_api(),
            "export_service": get_security_export_service(),
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

    def initialize_default_roles(self) -> tuple:
        from .rbac import DEFAULT_ROLES, RoleAlreadyExistsError, get_rbac

        rbac = get_rbac()
        for name, inherits in DEFAULT_ROLES.items():
            try:
                rbac.create_role(name, inherits=inherits)
            except RoleAlreadyExistsError:
                continue
        return tuple(DEFAULT_ROLES)

    def initialize_default_permissions(self) -> tuple:
        from .permissions import get_permission_engine

        engine = get_permission_engine()
        for role, resource, action in DEFAULT_PERMISSIONS:
            engine.grant(role, resource, action)
        return DEFAULT_PERMISSIONS

    def initialize_security_policies(self) -> tuple:
        from .security_policy import PolicyAlreadyExistsError, get_security_policy_engine

        engine = get_security_policy_engine()
        for name in DEFAULT_POLICY_NAMES:
            try:
                engine.register_policy(name)
            except PolicyAlreadyExistsError:
                continue
        return DEFAULT_POLICY_NAMES

    def register_api(self) -> bool:
        """
        Confirm every security endpoint is mounted under "/security".

        There is no separate route-registration step to perform here: every
        endpoint from commits 1-12 is already registered, at import time, by
        each module's own @router.* decorators. This is a verification that
        every router shares the expected prefix, not a second, redundant
        registration.
        """

        from .authentication import router as authentication_router
        from .api_keys import router as api_keys_router
        from .jwt_service import router as jwt_router
        from .rbac import router as rbac_router
        from .permissions import router as permissions_router
        from .session_manager import router as session_router
        from .audit_logs import router as audit_router
        from .security_policy import router as security_policy_router
        from .secrets import router as secrets_router
        from .security_analytics import router as security_analytics_router
        from .dashboard import router as dashboard_router
        from .export_service import router as export_router

        routers = (
            authentication_router,
            api_keys_router,
            jwt_router,
            rbac_router,
            permissions_router,
            session_router,
            audit_router,
            security_policy_router,
            secrets_router,
            security_analytics_router,
            dashboard_router,
            export_router,
        )
        return all(router.prefix == "/security" for router in routers)

    def validate(
        self, *, timestamp: Optional[datetime] = None
    ) -> SecurityBootstrapValidationResult:
        with self._lock:
            services = dict(self._services)
        if not services:
            services = self.register()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = SecurityBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise SecurityBootstrapError(result)
        return result

    def health_check(self) -> dict:
        with self._lock:
            dashboard = self._services.get("dashboard_api")
            registered = tuple(sorted(self._services))
        if dashboard is None:
            raise SecurityBootstrapError(
                SecurityBootstrapValidationResult(
                    valid=False,
                    registered_services=registered,
                    missing_services=("dashboard_api",),
                )
            )
        return {"status": "ok", **dashboard.overview()}


_bootstrap = SecurityBootstrap()


def get_security_bootstrap() -> SecurityBootstrap:
    return _bootstrap


def bootstrap_security_subsystem() -> SecurityBootstrapValidationResult:
    """Wire, validate, and register the Security, Authentication & Access Control subsystem.

    Safe to call more than once: each call re-registers the current singletons, re-applies
    default roles/permissions/policies (idempotently), and re-runs validation.
    """

    bootstrap = get_security_bootstrap()
    bootstrap.register()
    bootstrap.initialize_default_roles()
    bootstrap.initialize_default_permissions()
    bootstrap.initialize_security_policies()
    return bootstrap.validate()
