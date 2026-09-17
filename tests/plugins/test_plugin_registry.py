import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_analytics import MetricType, PluginAnalyticsService
from backend.plugins.plugin_registry import (
    Plugin,
    PluginAlreadyRegisteredError,
    PluginMetadata,
    PluginRegistry,
    UnknownPluginError,
    get_plugin_registry,
    router as plugin_registry_router,
)


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


@pytest.fixture
def client(registry: PluginRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_registry_router)
    app.dependency_overrides[get_plugin_registry] = lambda: registry
    return TestClient(app)


def test_register_creates_plugin(registry: PluginRegistry):
    plugin = registry.register(
        "csv-exporter", "1.0.0", PluginMetadata(description="Exports CSVs", author="alice")
    )

    assert isinstance(plugin, Plugin)
    assert plugin.name == "csv-exporter"
    assert plugin.version == "1.0.0"
    assert plugin.metadata.author == "alice"


def test_metadata_source_defaults_to_empty_string():
    assert PluginMetadata().source == ""


def test_metadata_source_round_trips_through_dict():
    metadata = PluginMetadata.from_dict({"source": "marketplace:official"})

    assert metadata.source == "marketplace:official"
    assert metadata.to_dict()["source"] == "marketplace:official"


def test_register_records_install_metric_when_analytics_provided(registry: PluginRegistry):
    analytics = PluginAnalyticsService()

    registry.register("csv-exporter", "1.0.0", analytics=analytics)

    records = analytics.list_records("csv-exporter", MetricType.INSTALL)
    assert len(records) == 1


def test_register_without_analytics_does_not_error(registry: PluginRegistry):
    plugin = registry.register("csv-exporter", "1.0.0")

    assert plugin.name == "csv-exporter"


def test_register_rejects_duplicate_version(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")

    with pytest.raises(PluginAlreadyRegisteredError):
        registry.register("csv-exporter", "1.0.0")


def test_register_allows_new_version(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")
    second = registry.register("csv-exporter", "2.0.0")

    assert second.version == "2.0.0"


def test_get_returns_latest_version_by_default(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")
    registry.register("csv-exporter", "2.0.0")

    assert registry.get("csv-exporter").version == "2.0.0"


def test_get_returns_specific_version(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")
    registry.register("csv-exporter", "2.0.0")

    assert registry.get("csv-exporter", version="1.0.0").version == "1.0.0"


def test_get_unknown_plugin_raises(registry: PluginRegistry):
    with pytest.raises(UnknownPluginError):
        registry.get("does-not-exist")


def test_get_unknown_version_raises(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")

    with pytest.raises(UnknownPluginError):
        registry.get("csv-exporter", version="9.9.9")


def test_is_registered_checks_name_and_version(registry: PluginRegistry):
    assert registry.is_registered("csv-exporter") is False

    registry.register("csv-exporter", "1.0.0")

    assert registry.is_registered("csv-exporter") is True
    assert registry.is_registered("csv-exporter", version="1.0.0") is True
    assert registry.is_registered("csv-exporter", version="9.9.9") is False


def test_list_plugins_returns_latest_of_each(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")
    registry.register("csv-exporter", "2.0.0")
    registry.register("json-exporter", "1.0.0")

    listed = {plugin.name: plugin.version for plugin in registry.list_plugins()}

    assert listed == {"csv-exporter": "2.0.0", "json-exporter": "1.0.0"}


def test_list_plugins_filters_by_tag(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0", PluginMetadata(tags=("export",)))
    registry.register("auth-plugin", "1.0.0", PluginMetadata(tags=("security",)))

    listed = registry.list_plugins(tag="export")

    assert [plugin.name for plugin in listed] == ["csv-exporter"]


def test_unregister_removes_all_versions(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")
    registry.register("csv-exporter", "2.0.0")

    registry.unregister("csv-exporter")

    with pytest.raises(UnknownPluginError):
        registry.get("csv-exporter")


def test_unregister_removes_single_version(registry: PluginRegistry):
    registry.register("csv-exporter", "1.0.0")
    registry.register("csv-exporter", "2.0.0")

    registry.unregister("csv-exporter", version="1.0.0")

    assert registry.get("csv-exporter").version == "2.0.0"


def test_unregister_unknown_plugin_raises(registry: PluginRegistry):
    with pytest.raises(UnknownPluginError):
        registry.unregister("does-not-exist")


def test_api_register_and_list(client: TestClient):
    response = client.post(
        "/plugins",
        json={"name": "csv-exporter", "version": "1.0.0", "metadata": {"author": "alice"}},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "csv-exporter"

    listed = client.get("/plugins")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/plugins", json={"name": "csv-exporter", "version": "1.0.0"})
    response = client.post("/plugins", json={"name": "csv-exporter", "version": "1.0.0"})

    assert response.status_code == 409


def test_api_get_unknown_plugin_returns_404(client: TestClient):
    response = client.get("/plugins/does-not-exist")

    assert response.status_code == 404


def test_api_delete_removes_plugin(client: TestClient):
    client.post("/plugins", json={"name": "csv-exporter", "version": "1.0.0"})

    response = client.delete("/plugins/csv-exporter")
    assert response.status_code == 204

    assert client.get("/plugins/csv-exporter").status_code == 404
