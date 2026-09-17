import json
import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.bootstrap import (
    PLUGINS_DIR,
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    PluginBootstrap,
    PluginBootstrapError,
    UnknownBootstrapServiceError,
    bootstrap_plugin_framework,
    get_plugin_bootstrap,
)
from backend.plugins.plugin_analytics import MetricType


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


@pytest.fixture
def plugin_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    def _write(*, extension_api_version: str = None) -> str:
        module_name = f"bootstrap_plugin_{uuid.uuid4().hex}"
        lines = ["VALUE = 1"]
        if extension_api_version is not None:
            lines.append(f"EXTENSION_API_VERSION = {extension_api_version!r}")
        (tmp_path / f"{module_name}.py").write_text("\n".join(lines) + "\n")
        return module_name

    return _write


def test_register_wires_every_required_service():
    bootstrap = PluginBootstrap()

    services = bootstrap.register()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = PluginBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = PluginBootstrap()
    bootstrap.register()

    registry = bootstrap.discover("plugin_registry")

    assert registry is bootstrap.registered_services()["plugin_registry"]


def test_discover_unknown_service_raises():
    bootstrap = PluginBootstrap()
    bootstrap.register()

    with pytest.raises(UnknownBootstrapServiceError):
        bootstrap.discover("does-not-exist")


def test_validate_registers_automatically_if_not_yet_registered():
    bootstrap = PluginBootstrap()

    result = bootstrap.validate()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()


def test_validate_raises_when_a_required_service_is_missing():
    bootstrap = PluginBootstrap()
    with bootstrap._lock:
        bootstrap._services = {
            name: object() for name in REQUIRED_SERVICES if name != "plugin_dashboard_api"
        }

    with pytest.raises(PluginBootstrapError) as exc_info:
        bootstrap.validate()

    assert exc_info.value.result.missing_services == ("plugin_dashboard_api",)
    assert exc_info.value.result.valid is False


def test_health_check_delegates_to_the_dashboard():
    bootstrap = PluginBootstrap()
    bootstrap.register()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "registry" in report


def test_health_check_raises_when_dashboard_not_registered():
    bootstrap = PluginBootstrap()

    with pytest.raises(PluginBootstrapError):
        bootstrap.health_check()


def test_register_api_confirms_plugins_prefix():
    bootstrap = PluginBootstrap()

    assert bootstrap.register_api() is True


def test_discover_plugins_finds_manifest_files(tmp_path):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    name = _unique("discoverable")
    (tmp_path / f"{name}.plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "entry_point": "some.module"})
    )

    manifests = bootstrap.discover_plugins(str(tmp_path))

    assert [manifest.name for manifest in manifests] == [name]


def test_discover_plugins_creates_default_directory_if_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bootstrap = PluginBootstrap()
    bootstrap.register()

    manifests = bootstrap.discover_plugins()

    assert manifests == []
    assert (tmp_path / PLUGINS_DIR).is_dir()


def test_install_discovered_plugins_installs_new_manifests(tmp_path, plugin_module):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    name = _unique("fresh-plugin")
    module_name = plugin_module()
    (tmp_path / f"{name}.plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "entry_point": module_name})
    )

    installed = bootstrap.install_discovered_plugins(str(tmp_path))

    assert installed == [name]
    lifecycle = bootstrap.discover("plugin_lifecycle_manager")
    from backend.plugins.plugin_lifecycle import PluginState

    assert lifecycle.get_state(name) == PluginState.INSTALLED


def test_install_discovered_plugins_skips_already_installed(tmp_path, plugin_module):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    name = _unique("repeat-plugin")
    module_name = plugin_module()
    (tmp_path / f"{name}.plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "entry_point": module_name})
    )

    first = bootstrap.install_discovered_plugins(str(tmp_path))
    second = bootstrap.install_discovered_plugins(str(tmp_path))

    assert first == [name]
    assert second == []


def test_initialize_event_hooks_records_activation_metric(plugin_module):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    bootstrap.initialize_event_hooks()

    name = _unique("hooked-plugin")
    module_name = plugin_module(extension_api_version="1.0")
    lifecycle = bootstrap.discover("plugin_lifecycle_manager")
    analytics = bootstrap.discover("plugin_analytics_service")
    from backend.plugins.plugin_loader import PluginManifest

    lifecycle.install(PluginManifest(name=name, version="1.0.0", entry_point=module_name))
    lifecycle.enable(name)

    records = analytics.list_records(name, MetricType.ACTIVATION)
    assert len(records) == 1


def test_initialize_event_hooks_does_not_double_subscribe_on_repeated_calls(plugin_module):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    bootstrap.initialize_event_hooks()
    bootstrap.initialize_event_hooks()
    bootstrap.initialize_event_hooks()

    name = _unique("no-double-count")
    module_name = plugin_module(extension_api_version="1.0")
    lifecycle = bootstrap.discover("plugin_lifecycle_manager")
    analytics = bootstrap.discover("plugin_analytics_service")
    from backend.plugins.plugin_loader import PluginManifest

    lifecycle.install(PluginManifest(name=name, version="1.0.0", entry_point=module_name))
    lifecycle.enable(name)

    records = analytics.list_records(name, MetricType.ACTIVATION)
    assert len(records) == 1


def test_enable_startup_plugins_enables_installed_plugins(tmp_path, plugin_module):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    name = _unique("startup-plugin")
    module_name = plugin_module()
    (tmp_path / f"{name}.plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "entry_point": module_name})
    )
    bootstrap.install_discovered_plugins(str(tmp_path))

    enabled = bootstrap.enable_startup_plugins()

    assert name in enabled
    from backend.plugins.plugin_lifecycle import PluginState

    lifecycle = bootstrap.discover("plugin_lifecycle_manager")
    assert lifecycle.get_state(name) == PluginState.ENABLED


def test_bootstrap_plugin_framework_is_valid(tmp_path):
    result = bootstrap_plugin_framework(plugins_dir=str(tmp_path))

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)


def test_bootstrap_plugin_framework_is_idempotent(tmp_path):
    first = bootstrap_plugin_framework(plugins_dir=str(tmp_path))
    second = bootstrap_plugin_framework(plugins_dir=str(tmp_path))

    assert first.valid is True
    assert second.valid is True


def test_get_plugin_bootstrap_returns_singleton():
    assert get_plugin_bootstrap() is get_plugin_bootstrap()


def test_subsystem_name_is_stable():
    assert SUBSYSTEM_NAME == "plugin_extension_framework"


def test_end_to_end_plugin_lifecycle(tmp_path, plugin_module):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    bootstrap.initialize_event_hooks()

    name = _unique("e2e-plugin")
    module_name = plugin_module(extension_api_version="1.0")
    (tmp_path / f"{name}.plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "entry_point": module_name,
                "description": "End-to-end bootstrap test plugin",
                "author": "bootstrap-tests",
            }
        )
    )

    installed = bootstrap.install_discovered_plugins(str(tmp_path))
    assert installed == [name]

    enabled = bootstrap.enable_startup_plugins()
    assert name in enabled

    registry = bootstrap.discover("plugin_registry")
    loader = bootstrap.discover("plugin_loader")
    extension_api = bootstrap.discover("extension_api")
    analytics = bootstrap.discover("plugin_analytics_service")
    dashboard = bootstrap.discover("plugin_dashboard_api")

    assert registry.is_registered(name, "1.0.0")
    assert loader.is_loaded(name)
    assert extension_api.get_extension(name).api_version == "1.0"

    events = analytics.list_records(name, MetricType.ACTIVATION)
    assert len(events) == 1

    overview = dashboard.overview()
    assert overview["runtime"]["loaded_count"] >= 1
    assert any(plugin["name"] == name for plugin in overview["registry"]["plugins"])


@pytest.fixture
def client() -> TestClient:
    from backend.plugins.dashboard import router as plugin_dashboard_router
    from backend.plugins.event_system import router as event_system_router
    from backend.plugins.extension_api import router as extension_api_router
    from backend.plugins.plugin_analytics import router as plugin_analytics_router
    from backend.plugins.plugin_config import router as plugin_config_router
    from backend.plugins.plugin_dependencies import router as plugin_dependencies_router
    from backend.plugins.plugin_lifecycle import router as plugin_lifecycle_router
    from backend.plugins.plugin_loader import router as plugin_loader_router
    from backend.plugins.plugin_marketplace import router as plugin_marketplace_router
    from backend.plugins.plugin_packaging import router as plugin_packaging_router
    from backend.plugins.plugin_registry import router as plugin_registry_router
    from backend.plugins.plugin_sandbox import router as plugin_sandbox_router

    app = FastAPI()
    # Registry must be included last: its "/plugins/{name}" route would
    # otherwise shadow several other routers' static sub-paths (established
    # in commits 2-11).
    for router in (
        plugin_loader_router,
        plugin_lifecycle_router,
        extension_api_router,
        event_system_router,
        plugin_dependencies_router,
        plugin_config_router,
        plugin_sandbox_router,
        plugin_packaging_router,
        plugin_marketplace_router,
        plugin_analytics_router,
        plugin_dashboard_router,
        plugin_registry_router,
    ):
        app.include_router(router)
    return TestClient(app)


def test_route_registration_covers_every_service_area(client: TestClient):
    bootstrap_plugin_framework()

    assert client.get("/plugins/dashboard").status_code == 200
    assert client.get("/plugins/marketplace").status_code == 200
    assert client.get("/plugins/analytics/summary").status_code == 200
    assert client.get("/plugins").status_code == 200
    assert client.get("/plugins/load-order").status_code == 200
