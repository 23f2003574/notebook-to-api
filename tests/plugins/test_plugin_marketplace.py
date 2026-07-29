import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.event_system import HookEventSystem
from backend.plugins.extension_api import ExtensionAPI, IncompatibleApiVersionError
from backend.plugins.plugin_lifecycle import PluginAlreadyInstalledError, PluginLifecycleManager, PluginState
from backend.plugins.plugin_loader import PluginLoader, PluginManifest
from backend.plugins.plugin_marketplace import (
    MarketplacePlugin,
    MarketplacePluginAlreadyListedError,
    MarketplaceSource,
    NoUpdateAvailableError,
    PluginMarketplaceService,
    UnknownMarketplacePluginError,
    UntrustedSourceError,
    get_plugin_marketplace_service,
    router as plugin_marketplace_router,
)
from backend.plugins.plugin_registry import PluginRegistry, UnknownPluginError


TRUSTED = MarketplaceSource(name="official", url="https://plugins.example/official", trusted=True)
UNTRUSTED = MarketplaceSource(name="community", url="https://plugins.example/community", trusted=False)


def _listing(name="csv-exporter", version="1.0.0", source=TRUSTED, **kwargs) -> MarketplacePlugin:
    return MarketplacePlugin(
        name=name,
        version=version,
        entry_point=kwargs.pop("entry_point", "csv_exporter_plugin.main"),
        source=source,
        **kwargs,
    )


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


@pytest.fixture
def lifecycle(registry: PluginRegistry) -> PluginLifecycleManager:
    loader = PluginLoader(registry)
    extension_api = ExtensionAPI(HookEventSystem())
    return PluginLifecycleManager(loader, registry, extension_api)


@pytest.fixture
def marketplace(lifecycle: PluginLifecycleManager, registry: PluginRegistry) -> PluginMarketplaceService:
    return PluginMarketplaceService(lifecycle, registry)


@pytest.fixture
def client(marketplace: PluginMarketplaceService) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_marketplace_router)
    app.dependency_overrides[get_plugin_marketplace_service] = lambda: marketplace
    return TestClient(app)


def test_list_plugin_duplicate_version_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing())

    with pytest.raises(MarketplacePluginAlreadyListedError):
        marketplace.list_plugin(_listing())


def test_search_matches_by_name(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(name="csv-exporter"))
    marketplace.list_plugin(_listing(name="json-exporter", entry_point="json_exporter_plugin.main"))

    results = marketplace.search(query="csv")

    assert [listing.name for listing in results] == ["csv-exporter"]


def test_search_matches_by_description(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(description="Exports notebooks to CSV"))

    results = marketplace.search(query="notebooks")

    assert len(results) == 1


def test_search_filters_by_tags(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(name="csv-exporter", tags=("export", "csv")))
    marketplace.list_plugin(
        _listing(name="auth-plugin", entry_point="auth_plugin.main", tags=("security",))
    )

    results = marketplace.search(tags=["export"])

    assert [listing.name for listing in results] == ["csv-exporter"]


def test_search_returns_only_latest_version_per_plugin(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(version="1.0.0"))
    marketplace.list_plugin(_listing(version="2.0.0"))

    results = marketplace.search()

    assert len(results) == 1
    assert results[0].version == "2.0.0"


def test_featured_returns_only_featured_listings(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(name="csv-exporter", featured=True))
    marketplace.list_plugin(
        _listing(name="json-exporter", entry_point="json_exporter_plugin.main", featured=False)
    )

    results = marketplace.featured()

    assert [listing.name for listing in results] == ["csv-exporter"]


def test_install_unknown_plugin_raises(marketplace: PluginMarketplaceService):
    with pytest.raises(UnknownMarketplacePluginError):
        marketplace.install("does-not-exist")


def test_install_untrusted_source_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(source=UNTRUSTED))

    with pytest.raises(UntrustedSourceError):
        marketplace.install("csv-exporter")


def test_install_untrusted_source_allowed_with_flag(
    marketplace: PluginMarketplaceService, lifecycle: PluginLifecycleManager
):
    marketplace.list_plugin(_listing(source=UNTRUSTED))

    event = marketplace.install("csv-exporter", allow_untrusted=True)

    assert event.to_state == PluginState.INSTALLED
    assert lifecycle.get_state("csv-exporter") == PluginState.INSTALLED


def test_install_incompatible_api_version_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(min_api_version="9.0"))

    with pytest.raises(IncompatibleApiVersionError):
        marketplace.install("csv-exporter")


def test_install_defaults_to_latest_version(marketplace: PluginMarketplaceService, registry: PluginRegistry):
    marketplace.list_plugin(_listing(version="1.0.0"))
    marketplace.list_plugin(_listing(version="2.0.0"))

    marketplace.install("csv-exporter")

    assert registry.get("csv-exporter").version == "2.0.0"


def test_install_specific_version(marketplace: PluginMarketplaceService, registry: PluginRegistry):
    marketplace.list_plugin(_listing(version="1.0.0"))
    marketplace.list_plugin(_listing(version="2.0.0"))

    marketplace.install("csv-exporter", version="1.0.0")

    assert registry.get("csv-exporter").version == "1.0.0"


def test_install_unknown_version_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(version="1.0.0"))

    with pytest.raises(UnknownMarketplacePluginError):
        marketplace.install("csv-exporter", version="9.9.9")


def test_install_already_installed_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing())
    marketplace.install("csv-exporter")

    with pytest.raises(PluginAlreadyInstalledError):
        marketplace.install("csv-exporter")


def test_update_not_installed_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing())

    with pytest.raises(UnknownPluginError):
        marketplace.update("csv-exporter")


def test_update_unknown_in_marketplace_raises(
    marketplace: PluginMarketplaceService, lifecycle: PluginLifecycleManager
):
    # Installed directly through the lifecycle manager (not the marketplace),
    # so it's a known, installed plugin that simply has no marketplace listing.
    lifecycle.install(PluginManifest(name="other-plugin", version="1.0.0", entry_point="other_plugin.main"))

    with pytest.raises(UnknownMarketplacePluginError):
        marketplace.update("other-plugin")


def test_update_no_update_available_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(version="1.0.0"))
    marketplace.install("csv-exporter")

    with pytest.raises(NoUpdateAvailableError):
        marketplace.update("csv-exporter")


def test_update_installs_newer_version(
    marketplace: PluginMarketplaceService, registry: PluginRegistry, lifecycle: PluginLifecycleManager
):
    marketplace.list_plugin(_listing(version="1.0.0"))
    marketplace.install("csv-exporter")
    marketplace.list_plugin(_listing(version="2.0.0"))

    event = marketplace.update("csv-exporter")

    assert event.to_state == PluginState.INSTALLED
    assert registry.get("csv-exporter").version == "2.0.0"
    assert lifecycle.get_state("csv-exporter") == PluginState.INSTALLED


def test_update_untrusted_source_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(version="1.0.0"))
    marketplace.install("csv-exporter")
    marketplace.list_plugin(_listing(version="2.0.0", source=UNTRUSTED))

    with pytest.raises(UntrustedSourceError):
        marketplace.update("csv-exporter")


def test_update_incompatible_api_version_raises(marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(version="1.0.0"))
    marketplace.install("csv-exporter")
    marketplace.list_plugin(_listing(version="2.0.0", min_api_version="9.0"))

    with pytest.raises(IncompatibleApiVersionError):
        marketplace.update("csv-exporter")


# --- API tests -------------------------------------------------------------


def test_api_browse_returns_catalog(client: TestClient, marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing())

    response = client.get("/plugins/marketplace")

    assert response.status_code == 200
    assert [listing["name"] for listing in response.json()] == ["csv-exporter"]


def test_api_browse_featured_only(client: TestClient, marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(name="csv-exporter", featured=True))
    marketplace.list_plugin(
        _listing(name="json-exporter", entry_point="json_exporter_plugin.main", featured=False)
    )

    response = client.get("/plugins/marketplace", params={"featured": "true"})

    assert [listing["name"] for listing in response.json()] == ["csv-exporter"]


def test_api_search(client: TestClient, marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(name="csv-exporter"))
    marketplace.list_plugin(_listing(name="json-exporter", entry_point="json_exporter_plugin.main"))

    response = client.get("/plugins/marketplace/search", params={"q": "csv"})

    assert [listing["name"] for listing in response.json()] == ["csv-exporter"]


def test_api_install(client: TestClient, marketplace: PluginMarketplaceService, registry: PluginRegistry):
    marketplace.list_plugin(_listing())

    response = client.post("/plugins/marketplace/install", json={"plugin": "csv-exporter"})

    assert response.status_code == 201
    assert response.json()["state"] == "installed"
    assert registry.is_registered("csv-exporter")


def test_api_install_untrusted_returns_403(client: TestClient, marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(source=UNTRUSTED))

    response = client.post("/plugins/marketplace/install", json={"plugin": "csv-exporter"})

    assert response.status_code == 403


def test_api_install_unknown_returns_404(client: TestClient):
    response = client.post("/plugins/marketplace/install", json={"plugin": "does-not-exist"})

    assert response.status_code == 404


def test_api_update(client: TestClient, marketplace: PluginMarketplaceService, registry: PluginRegistry):
    marketplace.list_plugin(_listing(version="1.0.0"))
    client.post("/plugins/marketplace/install", json={"plugin": "csv-exporter"})
    marketplace.list_plugin(_listing(version="2.0.0"))

    response = client.post("/plugins/marketplace/update/csv-exporter")

    assert response.status_code == 200
    assert registry.get("csv-exporter").version == "2.0.0"


def test_api_update_no_update_available_returns_409(client: TestClient, marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing(version="1.0.0"))
    client.post("/plugins/marketplace/install", json={"plugin": "csv-exporter"})

    response = client.post("/plugins/marketplace/update/csv-exporter")

    assert response.status_code == 409


def test_api_update_not_installed_returns_404(client: TestClient, marketplace: PluginMarketplaceService):
    marketplace.list_plugin(_listing())

    response = client.post("/plugins/marketplace/update/csv-exporter")

    assert response.status_code == 404
