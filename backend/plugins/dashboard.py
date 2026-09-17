from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from .plugin_analytics import PluginAnalyticsService, get_plugin_analytics_service
from .plugin_lifecycle import PluginLifecycleManager, get_plugin_lifecycle_manager
from .plugin_loader import PluginLoader, get_plugin_loader
from .plugin_marketplace import PluginMarketplaceService, get_plugin_marketplace_service
from .plugin_registry import PluginRegistry, UnknownPluginError, get_plugin_registry


def _generated_at(timestamp: Optional[datetime] = None) -> str:
    return (timestamp or datetime.now(timezone.utc)).isoformat()


class PluginDashboardAPI:
    """Read-only aggregation over the plugin registry, runtime, marketplace, and analytics."""

    def __init__(
        self,
        registry: Optional[PluginRegistry] = None,
        loader: Optional[PluginLoader] = None,
        lifecycle: Optional[PluginLifecycleManager] = None,
        marketplace: Optional[PluginMarketplaceService] = None,
        analytics: Optional[PluginAnalyticsService] = None,
    ) -> None:
        self._registry = registry if registry is not None else get_plugin_registry()
        self._loader = loader if loader is not None else get_plugin_loader()
        self._lifecycle = lifecycle if lifecycle is not None else get_plugin_lifecycle_manager()
        self._marketplace = marketplace if marketplace is not None else get_plugin_marketplace_service()
        self._analytics = analytics if analytics is not None else get_plugin_analytics_service()

    def registry(self) -> dict:
        plugins = self._registry.list_plugins()
        return {
            "total_plugins": len(plugins),
            "plugins": [plugin.to_dict() for plugin in plugins],
            "generated_at": _generated_at(),
        }

    def _safe_state(self, name: str):
        try:
            return self._lifecycle.get_state(name).value
        except UnknownPluginError:
            return None

    def runtime(self) -> dict:
        loaded = self._loader.list_loaded()
        return {
            "loaded_count": len(loaded),
            "plugins": [
                {**entry.to_dict(), "state": self._safe_state(entry.manifest.name)} for entry in loaded
            ],
            "generated_at": _generated_at(),
        }

    def marketplace(self) -> dict:
        catalog = self._marketplace.search()
        trusted_count = sum(1 for listing in catalog if listing.source.trusted)
        return {
            "catalog_size": len(catalog),
            "trusted_count": trusted_count,
            "featured": [listing.to_dict() for listing in self._marketplace.featured()],
            "generated_at": _generated_at(),
        }

    def analytics(self) -> dict:
        return {
            **self._analytics.summary(),
            "recent_activity": [record.to_dict() for record in self._analytics.recent(limit=10)],
            "generated_at": _generated_at(),
        }

    def overview(self) -> dict:
        return {
            "registry": self.registry(),
            "runtime": self.runtime(),
            "marketplace": self.marketplace(),
            "analytics": self.analytics(),
            "generated_at": _generated_at(),
        }


_plugin_dashboard_api = PluginDashboardAPI()


def get_plugin_dashboard_api() -> PluginDashboardAPI:
    return _plugin_dashboard_api


router = APIRouter(prefix="/plugins/dashboard", tags=["plugins-dashboard"])


@router.get("")
def get_dashboard_overview(api: PluginDashboardAPI = Depends(get_plugin_dashboard_api)) -> dict:
    return api.overview()


@router.get("/registry")
def get_dashboard_registry(api: PluginDashboardAPI = Depends(get_plugin_dashboard_api)) -> dict:
    return api.registry()


@router.get("/runtime")
def get_dashboard_runtime(api: PluginDashboardAPI = Depends(get_plugin_dashboard_api)) -> dict:
    return api.runtime()


@router.get("/marketplace")
def get_dashboard_marketplace(api: PluginDashboardAPI = Depends(get_plugin_dashboard_api)) -> dict:
    return api.marketplace()


@router.get("/analytics")
def get_dashboard_analytics(api: PluginDashboardAPI = Depends(get_plugin_dashboard_api)) -> dict:
    return api.analytics()
