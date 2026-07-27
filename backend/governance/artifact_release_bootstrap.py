from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from . import runtime

REQUIRED_SERVICES: tuple = (
    "artifact_registry",
    "version_manager",
    "promotion_engine",
    "release_manager",
    "notes_generator",
    "channel_manager",
    "policy_engine",
    "retention_manager",
    "replication_service",
    "verification_engine",
    "analytics_service",
    "dashboard_api",
)

SUBSYSTEM_NAME = "artifact_release_management"

DEFAULT_CHANNELS: tuple = (
    ("alpha", "Alpha", True),
    ("beta", "Beta", False),
    ("stable", "Stable", False),
    ("lts", "LTS", False),
)

DEFAULT_POLICY_NAMES: tuple = (
    "Approval Required",
    "Artifact Verified",
    "Release Notes Present",
    "Channel Assigned",
)

DEFAULT_RETENTION_MAX_VERSIONS = 5


class UnknownServiceError(KeyError):
    pass


@dataclass(frozen=True)
class ArtifactReleaseBootstrapValidationResult:
    """One immutable outcome of validating the subsystem's startup wiring."""

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


class ArtifactReleaseBootstrapError(RuntimeError):
    """Raised when the artifact & release management subsystem fails startup validation."""

    def __init__(self, result: ArtifactReleaseBootstrapValidationResult) -> None:
        self.result = result
        detail = (
            f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        )
        super().__init__("artifact & release management bootstrap validation failed" + detail)


class ArtifactReleaseBootstrap:
    """Wires together every Artifact & Release Management service singleton and validates startup."""

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._lock = Lock()

    def register(self) -> dict:
        from .artifact_registry import get_artifact_registry
        from .artifact_versioning import get_artifact_version_manager
        from .artifact_promotion import get_artifact_promotion_engine
        from .release_manager import get_release_manager
        from .release_notes import get_release_notes_generator
        from .release_channels import get_release_channel_manager
        from .release_policy import get_release_policy_engine
        from .artifact_retention import get_artifact_retention_manager
        from .artifact_replication import get_artifact_replication_service
        from .release_verification import get_release_verification_engine
        from .release_analytics import get_release_analytics_service
        from .release_dashboard import get_release_dashboard_api

        services = {
            "artifact_registry": get_artifact_registry(),
            "version_manager": get_artifact_version_manager(),
            "promotion_engine": get_artifact_promotion_engine(),
            "release_manager": get_release_manager(),
            "notes_generator": get_release_notes_generator(),
            "channel_manager": get_release_channel_manager(),
            "policy_engine": get_release_policy_engine(),
            "retention_manager": get_artifact_retention_manager(),
            "replication_service": get_artifact_replication_service(),
            "verification_engine": get_release_verification_engine(),
            "analytics_service": get_release_analytics_service(),
            "dashboard_api": get_release_dashboard_api(),
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

    def initialize_default_channels(self) -> tuple:
        from .release_channels import ChannelAlreadyExistsError, get_release_channel_manager

        manager = get_release_channel_manager()
        for name, kind, is_default in DEFAULT_CHANNELS:
            try:
                manager.create_channel(name, kind, is_default=is_default)
            except ChannelAlreadyExistsError:
                continue
        return tuple(name for name, _, _ in DEFAULT_CHANNELS)

    def initialize_default_policies(self) -> tuple:
        from .release_policy import PolicyAlreadyExistsError, get_release_policy_engine

        engine = get_release_policy_engine()
        for name in DEFAULT_POLICY_NAMES:
            try:
                engine.register_policy(name, lambda release: False)
            except PolicyAlreadyExistsError:
                continue
        return DEFAULT_POLICY_NAMES

    def default_retention_config(self) -> dict:
        return {"max_versions": DEFAULT_RETENTION_MAX_VERSIONS}

    def ensure_retention_policy(self, name: str) -> None:
        from .artifact_retention import (
            RetentionPolicyAlreadyExistsError,
            get_artifact_retention_manager,
        )

        try:
            get_artifact_retention_manager().register_policy(
                name, max_versions=DEFAULT_RETENTION_MAX_VERSIONS
            )
        except RetentionPolicyAlreadyExistsError:
            pass

    def register_api(self) -> bool:
        """
        Confirm every artifact & release management endpoint is mounted
        under "/governance".

        There is no separate route-registration step to perform here:
        every endpoint from commits 1-12 is already registered, at
        import time, by each module's own @router.* decorators. This is
        a verification that every router shares the expected prefix,
        not a second, redundant registration.
        """

        from .artifact_registry import router as artifact_registry_router
        from .artifact_versioning import router as artifact_versioning_router
        from .artifact_promotion import router as artifact_promotion_router
        from .release_manager import router as release_manager_router
        from .release_notes import router as release_notes_router
        from .release_channels import router as release_channels_router
        from .release_policy import router as release_policy_router
        from .artifact_retention import router as artifact_retention_router
        from .artifact_replication import router as artifact_replication_router
        from .release_verification import router as release_verification_router
        from .release_analytics import router as release_analytics_router
        from .release_dashboard import router as release_dashboard_router

        routers = (
            artifact_registry_router,
            artifact_versioning_router,
            artifact_promotion_router,
            release_manager_router,
            release_notes_router,
            release_channels_router,
            release_policy_router,
            artifact_retention_router,
            artifact_replication_router,
            release_verification_router,
            release_analytics_router,
            release_dashboard_router,
        )
        return all(router.prefix == "/governance" for router in routers)

    def validate(
        self, *, timestamp: Optional[datetime] = None
    ) -> ArtifactReleaseBootstrapValidationResult:
        with self._lock:
            services = dict(self._services)
        if not services:
            services = self.register()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = ArtifactReleaseBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise ArtifactReleaseBootstrapError(result)
        return result

    def health_check(self) -> dict:
        with self._lock:
            dashboard = self._services.get("dashboard_api")
            registered = tuple(sorted(self._services))
        if dashboard is None:
            raise ArtifactReleaseBootstrapError(
                ArtifactReleaseBootstrapValidationResult(
                    valid=False,
                    registered_services=registered,
                    missing_services=("dashboard_api",),
                )
            )
        return {"status": "ok", **dashboard.overview()}


_bootstrap = ArtifactReleaseBootstrap()


def get_artifact_release_bootstrap() -> ArtifactReleaseBootstrap:
    return _bootstrap


def bootstrap_artifact_release_subsystem() -> ArtifactReleaseBootstrapValidationResult:
    """Wire, validate, and register the Artifact & Release Management subsystem with the governance runtime.

    Safe to call more than once: each call re-registers the current singletons, re-applies
    default channels/policies (idempotently), and re-runs validation.
    """

    bootstrap = get_artifact_release_bootstrap()
    bootstrap.register()
    bootstrap.initialize_default_channels()
    bootstrap.initialize_default_policies()
    result = bootstrap.validate()
    runtime.register_subsystem(SUBSYSTEM_NAME, bootstrap.validate)
    runtime.register_health_check(SUBSYSTEM_NAME, bootstrap.health_check)
    return result
