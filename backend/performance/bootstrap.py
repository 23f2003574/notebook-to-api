from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from .cache_manager import get_cache_manager
from .in_memory_cache import get_in_memory_cache
from .distributed_cache import get_distributed_cache_adapter
from .cache_eviction import get_cache_eviction_engine
from .response_cache import get_response_cache_middleware
from .cache_invalidation import get_cache_invalidation_service
from .profiler import get_performance_profiler
from .resource_pool import get_resource_pool_manager
from .compression import get_compression_engine
from .dashboard import get_performance_dashboard_api
from .export_service import get_performance_export_service

REQUIRED_SERVICES: tuple = (
    "cache_manager",
    "memory_cache",
    "distributed_cache_adapter",
    "eviction_engine",
    "response_cache",
    "invalidation_service",
    "profiler",
    "resource_pool_manager",
    "compression_engine",
    "dashboard_api",
    "export_service",
)

SUBSYSTEM_NAME = "caching_and_performance_optimization"

# The bootstrap spec for this subsystem also names a "ConnectionPoolManager"
# service. No such service was ever implemented (the connection-pooling
# commit was skipped), so it's intentionally absent from REQUIRED_SERVICES
# and registered_services() rather than faked.
_WIRED_MIDDLEWARE_TYPES: tuple = (
    ("response_caching", 20),
    ("compression_engine", 30),
)


class UnknownServiceError(KeyError):
    pass


class PerformanceNotInitializedError(RuntimeError):
    pass


@dataclass(frozen=True)
class PerformanceBootstrapValidationResult:
    """One immutable outcome of validating the performance subsystem's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    wired_middleware: tuple = field(default_factory=tuple)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "wired_middleware": list(self.wired_middleware),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class PerformanceBootstrapError(RuntimeError):
    """Raised when the performance subsystem fails startup validation."""

    def __init__(self, result: PerformanceBootstrapValidationResult) -> None:
        self.result = result
        detail = (
            f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        )
        super().__init__("performance subsystem bootstrap validation failed" + detail)


class PerformanceBootstrap:
    """Wires together every Caching & Performance Optimization service singleton."""

    def __init__(self) -> None:
        self._services: dict = {}
        self._wired_middleware: tuple = ()
        self._initialized = False
        self._lock = Lock()

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def register_services(self) -> dict:
        services = {
            "cache_manager": get_cache_manager(),
            "memory_cache": get_in_memory_cache(),
            "distributed_cache_adapter": get_distributed_cache_adapter(),
            "eviction_engine": get_cache_eviction_engine(),
            "response_cache": get_response_cache_middleware(),
            "invalidation_service": get_cache_invalidation_service(),
            "profiler": get_performance_profiler(),
            "resource_pool_manager": get_resource_pool_manager(),
            "compression_engine": get_compression_engine(),
            "dashboard_api": get_performance_dashboard_api(),
            "export_service": get_performance_export_service(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def wire_components(self) -> tuple:
        """Register the response-cache and compression middleware into the shared gateway pipeline."""

        from backend.gateway.middleware import BUILTIN_MIDDLEWARE_FACTORIES, get_middleware_pipeline

        pipeline = get_middleware_pipeline()
        registered_names = {middleware.name for middleware in pipeline.list_middleware()}
        wired = []
        for name, priority in _WIRED_MIDDLEWARE_TYPES:
            if name not in registered_names:
                before, after = BUILTIN_MIDDLEWARE_FACTORIES[name]({})
                pipeline.register(name, before=before, after=after, priority=priority)
            wired.append(name)
        with self._lock:
            self._wired_middleware = tuple(wired)
        return self._wired_middleware

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    def initialize(
        self, *, timestamp: Optional[datetime] = None
    ) -> PerformanceBootstrapValidationResult:
        services = self.register_services()
        wired = self.wire_components()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = PerformanceBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            wired_middleware=wired,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise PerformanceBootstrapError(result)

        with self._lock:
            self._initialized = True
        return result

    def health_check(self) -> dict:
        if not self._initialized:
            raise PerformanceNotInitializedError("performance bootstrap is not initialized")
        dashboard = self.discover("dashboard_api")
        return {"status": "ok", **dashboard.overview()}

    def shutdown(self) -> None:
        if not self._initialized:
            raise PerformanceNotInitializedError("performance bootstrap is not initialized")

        from backend.gateway.middleware import get_middleware_pipeline

        pipeline = get_middleware_pipeline()
        for name in self._wired_middleware:
            try:
                pipeline.remove(name)
            except KeyError:
                continue

        cache_manager = self._services.get("cache_manager")
        memory_cache = self._services.get("memory_cache")
        if cache_manager is not None:
            cache_manager.clear()
        if memory_cache is not None:
            memory_cache.clear()

        with self._lock:
            self._initialized = False
            self._wired_middleware = ()


_bootstrap = PerformanceBootstrap()


def get_performance_bootstrap() -> PerformanceBootstrap:
    return _bootstrap


def bootstrap_performance_subsystem() -> PerformanceBootstrapValidationResult:
    """Wire and validate the full Caching & Performance Optimization subsystem.

    Safe to call more than once: each call re-registers the current singletons,
    re-wires the shared middleware pipeline (idempotently), and re-runs validation.
    """
    bootstrap = get_performance_bootstrap()
    return bootstrap.initialize()
