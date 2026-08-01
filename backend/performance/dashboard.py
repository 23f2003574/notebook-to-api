from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, HTTPException

from .export_service import (
    ExportFormat,
    PerformanceExportService,
    get_performance_export_service,
)

if TYPE_CHECKING:
    from .cache_manager import CacheManager
    from .in_memory_cache import InMemoryCache
    from .cache_eviction import CacheEvictionEngine
    from .response_cache import ResponseCacheMiddleware
    from .distributed_cache import DistributedCacheAdapter
    from .resource_pool import ResourcePoolManager
    from .compression import CompressionEngine
    from .profiler import PerformanceProfiler


class PerformanceDashboardAPI:
    """Aggregates metrics from every performance subsystem into read-only sections."""

    def __init__(
        self,
        *,
        cache_manager: "CacheManager",
        memory_cache: "InMemoryCache",
        eviction_engine: "CacheEvictionEngine",
        response_cache: "ResponseCacheMiddleware",
        distributed_adapter: "DistributedCacheAdapter",
        pool_manager: "ResourcePoolManager",
        profiler: "PerformanceProfiler",
        compression_engine: "CompressionEngine",
    ) -> None:
        self._cache_manager = cache_manager
        self._memory_cache = memory_cache
        self._eviction_engine = eviction_engine
        self._response_cache = response_cache
        self._distributed = distributed_adapter
        self._pools = pool_manager
        self._profiler = profiler
        self._compression = compression_engine

    def cache(self) -> dict:
        return {
            "cache_manager": self._cache_manager.stats().to_dict(),
            "memory_cache": self._memory_cache.stats().to_dict(),
            "eviction": self._eviction_engine.stats(),
            "responses": self._response_cache.stats(),
        }

    def resources(self) -> dict:
        pools = self._pools.list_pools()
        return {"pools": [self._pools.stats(pool.name) for pool in pools]}

    def connections(self) -> dict:
        return {
            "backends": self._distributed.list_backends(),
            "health": self._distributed.health_check(),
        }

    def _compression_summary(self) -> dict:
        return self._compression.stats()

    def _profiler_summary(self) -> dict:
        sessions = self._profiler.list_sessions()
        return {
            "total_sessions": len(sessions),
            "running": sum(1 for session in sessions if session.status == "running"),
            "stopped": sum(1 for session in sessions if session.status == "stopped"),
        }

    def overview(self) -> dict:
        return {
            "cache": self.cache(),
            "resources": self.resources(),
            "connections": self.connections(),
            "compression": self._compression_summary(),
            "profiler": self._profiler_summary(),
        }


_performance_dashboard_api: Optional[PerformanceDashboardAPI] = None


def get_performance_dashboard_api() -> PerformanceDashboardAPI:
    global _performance_dashboard_api
    if _performance_dashboard_api is None:
        from .cache_manager import get_cache_manager
        from .in_memory_cache import get_in_memory_cache
        from .cache_eviction import get_cache_eviction_engine
        from .response_cache import get_response_cache_middleware
        from .distributed_cache import get_distributed_cache_adapter
        from .resource_pool import get_resource_pool_manager
        from .compression import get_compression_engine
        from .profiler import get_performance_profiler

        _performance_dashboard_api = PerformanceDashboardAPI(
            cache_manager=get_cache_manager(),
            memory_cache=get_in_memory_cache(),
            eviction_engine=get_cache_eviction_engine(),
            response_cache=get_response_cache_middleware(),
            distributed_adapter=get_distributed_cache_adapter(),
            pool_manager=get_resource_pool_manager(),
            profiler=get_performance_profiler(),
            compression_engine=get_compression_engine(),
        )
    return _performance_dashboard_api


export_router = APIRouter(prefix="/performance/export", tags=["performance-export"])


def _parse_format(value: str) -> ExportFormat:
    try:
        return ExportFormat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown export format")


@export_router.get("/metrics")
def export_metrics_endpoint(
    format: str = "json",
    sections: Optional[str] = None,
    service: PerformanceExportService = Depends(get_performance_export_service),
) -> dict:
    fmt = _parse_format(format)
    section_list = [s.strip() for s in sections.split(",")] if sections else None
    export = service.export_metrics(fmt=fmt, sections=section_list)
    return export.to_dict()


@export_router.get("/cache")
def export_cache_endpoint(
    format: str = "json",
    service: PerformanceExportService = Depends(get_performance_export_service),
) -> dict:
    fmt = _parse_format(format)
    export = service.export_cache(fmt=fmt)
    return export.to_dict()


@export_router.get("/profiles")
def export_profiles_endpoint(
    format: str = "json",
    session_ids: Optional[str] = None,
    service: PerformanceExportService = Depends(get_performance_export_service),
) -> dict:
    fmt = _parse_format(format)
    id_list = [s.strip() for s in session_ids.split(",")] if session_ids else None
    export = service.export_profiles(fmt=fmt, session_ids=id_list)
    return export.to_dict()


@export_router.get("/all")
def export_all_endpoint(
    format: str = "json",
    service: PerformanceExportService = Depends(get_performance_export_service),
) -> dict:
    fmt = _parse_format(format)
    manifest = service.export_all(fmt=fmt)
    return manifest.to_dict()
