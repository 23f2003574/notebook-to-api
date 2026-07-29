import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.dashboard import (
    PluginDashboardAPI,
    get_plugin_dashboard_api,
    router as plugin_dashboard_router,
)
from backend.plugins.event_system import HookEventSystem
from backend.plugins.extension_api import ExtensionAPI
from backend.plugins.plugin_analytics import MetricType, PluginAnalyticsService
from backend.plugins.plugin_lifecycle import PluginLifecycleManager, PluginState
from backend.plugins.plugin_loader import PluginLoader, PluginManifest
from backend.plugins.plugin_marketplace import MarketplacePlugin, MarketplaceSource, PluginMarketplaceService
from backend.plugins.plugin_registry import PluginRegistry


TRUSTED = MarketplaceSource(name="official", url="https://plugins.example/official", trusted=True)


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


@pytest.fixture
def loader(registry: PluginRegistry) -> PluginLoader:
    return PluginLoader(registry)


@pytest.fixture
def lifecycle(loader: PluginLoader, registry: PluginRegistry) -> PluginLifecycleManager:
    return PluginLifecycleManager(loader, registry, ExtensionAPI(HookEventSystem()))


@pytest.fixture
def marketplace(lifecycle: PluginLifecycleManager, registry: PluginRegistry) -> PluginMarketplaceService:
    return PluginMarketplaceService(lifecycle, registry)


@pytest.fixture
def analytics() -> PluginAnalyticsService:
    return PluginAnalyticsService()


@pytest.fixture
def plugin_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    def _write(value: int = 1) -> str:
        module_name = f"dashboard_plugin_{uuid.uuid4().hex}"
        (tmp_path / f"{module_name}.py").write_text(f"VALUE = {value}\n")
        return module_name

    return _write


@pytest.fixture
def dashboard(
    registry: PluginRegistry,
    loader: PluginLoader,
    lifecycle: PluginLifecycleManager,
    marketplace: PluginMarketplaceService,
    analytics: PluginAnalyticsService,
) -> PluginDashboardAPI:
    return PluginDashboardAPI(registry, loader, lifecycle, marketplace, analytics)


@pytest.fixture
def client(dashboard: PluginDashboardAPI) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_dashboard_router)
    app.dependency_overrides[get_plugin_dashboard_api] = lambda: dashboard
    return TestClient(app)


def test_registry_section_lists_registered_plugins(dashboard: PluginDashboardAPI, registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")
    registry.register("json-exporter", "1.0.0")

    section = dashboard.registry()

    assert section["total_plugins"] == 2
    assert {plugin["name"] for plugin in section["plugins"]} == {"csv-exporter", "json-exporter"}
    assert "generated_at" in section


def test_registry_section_empty_by_default(dashboard: PluginDashboardAPI):
    section = dashboard.registry()

    assert section["total_plugins"] == 0
    assert section["plugins"] == []


def test_runtime_section_lists_loaded_plugins(dashboard: PluginDashboardAPI, loader: PluginLoader, plugin_module):
    module_name = plugin_module()
    loader.load(PluginManifest(name="csv-exporter", version="1.0.0", entry_point=module_name))

    section = dashboard.runtime()

    assert section["loaded_count"] == 1
    assert section["plugins"][0]["name"] == "csv-exporter"


def test_runtime_section_reports_lifecycle_state_when_known(
    dashboard: PluginDashboardAPI, lifecycle: PluginLifecycleManager, plugin_module
):
    module_name = plugin_module()
    lifecycle.install(PluginManifest(name="csv-exporter", version="1.0.0", entry_point=module_name))
    lifecycle.enable("csv-exporter")

    section = dashboard.runtime()

    assert section["plugins"][0]["state"] == PluginState.ENABLED.value


def test_runtime_section_state_is_none_when_loaded_outside_lifecycle(
    dashboard: PluginDashboardAPI, loader: PluginLoader, plugin_module
):
    module_name = plugin_module()
    loader.load(PluginManifest(name="csv-exporter", version="1.0.0", entry_point=module_name))

    section = dashboard.runtime()

    assert section["plugins"][0]["state"] is None


def test_runtime_section_empty_by_default(dashboard: PluginDashboardAPI):
    section = dashboard.runtime()

    assert section["loaded_count"] == 0
    assert section["plugins"] == []


def test_marketplace_section_reports_catalog_and_featured(
    dashboard: PluginDashboardAPI, marketplace: PluginMarketplaceService
):
    marketplace.list_plugin(
        MarketplacePlugin(
            name="csv-exporter",
            version="1.0.0",
            entry_point="csv_exporter_plugin.main",
            source=TRUSTED,
            featured=True,
        )
    )
    marketplace.list_plugin(
        MarketplacePlugin(
            name="json-exporter",
            version="1.0.0",
            entry_point="json_exporter_plugin.main",
            source=TRUSTED,
            featured=False,
        )
    )

    section = dashboard.marketplace()

    assert section["catalog_size"] == 2
    assert section["trusted_count"] == 2
    assert [listing["name"] for listing in section["featured"]] == ["csv-exporter"]


def test_marketplace_section_empty_by_default(dashboard: PluginDashboardAPI):
    section = dashboard.marketplace()

    assert section["catalog_size"] == 0
    assert section["featured"] == []


def test_analytics_section_includes_summary_and_recent_activity(
    dashboard: PluginDashboardAPI, analytics: PluginAnalyticsService
):
    analytics.record("csv-exporter", MetricType.INSTALL)
    analytics.record("csv-exporter", MetricType.EXECUTION, success=True)

    section = dashboard.analytics()

    assert section["install_count"] == 1
    assert section["execution_count"] == 1
    assert len(section["recent_activity"]) == 2


def test_overview_combines_all_sections(
    dashboard: PluginDashboardAPI, registry: PluginRegistry, analytics: PluginAnalyticsService
):
    registry.register("csv-exporter", "1.0.0")
    analytics.record("csv-exporter", MetricType.INSTALL)

    overview = dashboard.overview()

    assert set(overview.keys()) == {"registry", "runtime", "marketplace", "analytics", "generated_at"}
    assert overview["registry"]["total_plugins"] == 1
    assert overview["analytics"]["install_count"] == 1


# --- API tests -------------------------------------------------------------


def test_api_overview(client: TestClient, registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")

    response = client.get("/plugins/dashboard")

    assert response.status_code == 200
    assert response.json()["registry"]["total_plugins"] == 1


def test_api_registry_endpoint(client: TestClient, registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")

    response = client.get("/plugins/dashboard/registry")

    assert response.status_code == 200
    assert response.json()["total_plugins"] == 1


def test_api_runtime_endpoint(client: TestClient):
    response = client.get("/plugins/dashboard/runtime")

    assert response.status_code == 200
    assert response.json()["loaded_count"] == 0


def test_api_marketplace_endpoint(client: TestClient, marketplace: PluginMarketplaceService):
    marketplace.list_plugin(
        MarketplacePlugin(
            name="csv-exporter", version="1.0.0", entry_point="csv_exporter_plugin.main", source=TRUSTED
        )
    )

    response = client.get("/plugins/dashboard/marketplace")

    assert response.status_code == 200
    assert response.json()["catalog_size"] == 1


def test_api_analytics_endpoint(client: TestClient, analytics: PluginAnalyticsService):
    analytics.record("csv-exporter", MetricType.INSTALL)

    response = client.get("/plugins/dashboard/analytics")

    assert response.status_code == 200
    assert response.json()["install_count"] == 1
