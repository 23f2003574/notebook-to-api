import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_lifecycle import (
    InvalidTransitionError,
    LifecycleEvent,
    PluginAlreadyInstalledError,
    PluginLifecycleManager,
    PluginState,
    get_plugin_lifecycle_manager,
    router as plugin_lifecycle_router,
)
from backend.plugins.plugin_loader import PluginLoader, PluginManifest
from backend.plugins.plugin_registry import PluginRegistry, UnknownPluginError


def _unique_module_name() -> str:
    return f"lifecycle_plugin_{uuid.uuid4().hex}"


@pytest.fixture
def plugin_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    def _write(value: int = 1) -> str:
        module_name = _unique_module_name()
        (tmp_path / f"{module_name}.py").write_text(f"VALUE = {value}\n")
        return module_name

    return _write


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


@pytest.fixture
def loader(registry: PluginRegistry) -> PluginLoader:
    return PluginLoader(registry)


@pytest.fixture
def lifecycle(loader: PluginLoader, registry: PluginRegistry) -> PluginLifecycleManager:
    return PluginLifecycleManager(loader, registry)


@pytest.fixture
def client(lifecycle: PluginLifecycleManager) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_lifecycle_router)
    app.dependency_overrides[get_plugin_lifecycle_manager] = lambda: lifecycle
    return TestClient(app)


def _manifest(entry_point: str, name: str = "sample", version: str = "1.0.0") -> PluginManifest:
    return PluginManifest(name=name, version=version, entry_point=entry_point)


def test_install_creates_installed_record(lifecycle: PluginLifecycleManager, plugin_module, registry: PluginRegistry):
    manifest = _manifest(plugin_module())

    event = lifecycle.install(manifest)

    assert isinstance(event, LifecycleEvent)
    assert event.to_state == PluginState.INSTALLED
    assert lifecycle.get_state("sample") == PluginState.INSTALLED
    assert registry.is_registered("sample", "1.0.0")


def test_install_twice_raises(lifecycle: PluginLifecycleManager, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)

    with pytest.raises(PluginAlreadyInstalledError):
        lifecycle.install(manifest)


def test_reinstall_after_uninstall_is_allowed(lifecycle: PluginLifecycleManager, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)
    lifecycle.uninstall("sample")

    event = lifecycle.install(manifest)

    assert event.to_state == PluginState.INSTALLED


def test_enable_loads_plugin_into_runtime(lifecycle: PluginLifecycleManager, loader: PluginLoader, plugin_module):
    manifest = _manifest(plugin_module(value=7))
    lifecycle.install(manifest)

    event = lifecycle.enable("sample")

    assert event.to_state == PluginState.ENABLED
    assert loader.is_loaded("sample")
    assert loader.get_loaded("sample").module.VALUE == 7


def test_enable_unknown_plugin_raises(lifecycle: PluginLifecycleManager):
    with pytest.raises(UnknownPluginError):
        lifecycle.enable("does-not-exist")


def test_enable_twice_raises_invalid_transition(lifecycle: PluginLifecycleManager, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)
    lifecycle.enable("sample")

    with pytest.raises(InvalidTransitionError):
        lifecycle.enable("sample")


def test_disable_unloads_plugin(lifecycle: PluginLifecycleManager, loader: PluginLoader, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)
    lifecycle.enable("sample")

    event = lifecycle.disable("sample")

    assert event.to_state == PluginState.DISABLED
    assert loader.is_loaded("sample") is False


def test_disable_before_enable_raises_invalid_transition(lifecycle: PluginLifecycleManager, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)

    with pytest.raises(InvalidTransitionError):
        lifecycle.disable("sample")


def test_disabled_plugin_can_be_reenabled(lifecycle: PluginLifecycleManager, loader: PluginLoader, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)
    lifecycle.enable("sample")
    lifecycle.disable("sample")

    event = lifecycle.enable("sample")

    assert event.to_state == PluginState.ENABLED
    assert loader.is_loaded("sample")


def test_uninstall_from_enabled_unloads_and_unregisters(
    lifecycle: PluginLifecycleManager, loader: PluginLoader, registry: PluginRegistry, plugin_module
):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)
    lifecycle.enable("sample")

    event = lifecycle.uninstall("sample")

    assert event.to_state == PluginState.UNINSTALLED
    assert loader.is_loaded("sample") is False
    assert registry.is_registered("sample") is False


def test_uninstall_twice_raises_invalid_transition(lifecycle: PluginLifecycleManager, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)
    lifecycle.uninstall("sample")

    with pytest.raises(InvalidTransitionError):
        lifecycle.uninstall("sample")


def test_uninstall_unknown_plugin_raises(lifecycle: PluginLifecycleManager):
    with pytest.raises(UnknownPluginError):
        lifecycle.uninstall("does-not-exist")


def test_get_history_records_transitions_in_order(lifecycle: PluginLifecycleManager, plugin_module):
    manifest = _manifest(plugin_module())
    lifecycle.install(manifest)
    lifecycle.enable("sample")
    lifecycle.disable("sample")

    history = lifecycle.get_history("sample")

    assert [event.to_state for event in history] == [
        PluginState.INSTALLED,
        PluginState.ENABLED,
        PluginState.DISABLED,
    ]
    assert history[1].from_state == PluginState.INSTALLED


def test_auto_enable_installed_enables_only_installed_plugins(
    lifecycle: PluginLifecycleManager, loader: PluginLoader, plugin_module
):
    stays_disabled = _manifest(plugin_module(), name="already-handled")
    to_auto_enable = _manifest(plugin_module(), name="fresh-install")
    lifecycle.install(stays_disabled)
    lifecycle.enable("already-handled")
    lifecycle.disable("already-handled")
    lifecycle.install(to_auto_enable)

    enabled = lifecycle.auto_enable_installed()

    assert enabled == ["fresh-install"]
    assert lifecycle.get_state("already-handled") == PluginState.DISABLED
    assert lifecycle.get_state("fresh-install") == PluginState.ENABLED


def test_auto_enable_installed_skips_plugins_that_fail_to_load(lifecycle: PluginLifecycleManager, plugin_module):
    good = _manifest(plugin_module(), name="good-plugin")
    bad = PluginManifest(name="bad-plugin", version="1.0.0", entry_point="module_that_does_not_exist_at_all")
    lifecycle.install(good)
    lifecycle.install(bad)

    enabled = lifecycle.auto_enable_installed()

    assert enabled == ["good-plugin"]
    assert lifecycle.get_state("bad-plugin") == PluginState.INSTALLED


def test_api_install_enable_disable_uninstall_flow(client: TestClient, plugin_module):
    module_name = plugin_module()

    install_response = client.post(
        "/plugins/install",
        json={"name": "sample", "version": "1.0.0", "entry_point": module_name},
    )
    assert install_response.status_code == 201
    assert install_response.json()["state"] == "installed"

    enable_response = client.post("/plugins/sample/enable")
    assert enable_response.status_code == 200
    assert enable_response.json()["state"] == "enabled"

    disable_response = client.post("/plugins/sample/disable")
    assert disable_response.status_code == 200
    assert disable_response.json()["state"] == "disabled"

    delete_response = client.delete("/plugins/sample")
    assert delete_response.status_code == 204


def test_api_install_duplicate_returns_409(client: TestClient, plugin_module):
    module_name = plugin_module()
    payload = {"name": "sample", "version": "1.0.0", "entry_point": module_name}
    client.post("/plugins/install", json=payload)

    response = client.post("/plugins/install", json=payload)

    assert response.status_code == 409


def test_api_enable_unknown_plugin_returns_404(client: TestClient):
    response = client.post("/plugins/does-not-exist/enable")

    assert response.status_code == 404


def test_api_disable_before_enable_returns_409(client: TestClient, plugin_module):
    module_name = plugin_module()
    client.post(
        "/plugins/install",
        json={"name": "sample", "version": "1.0.0", "entry_point": module_name},
    )

    response = client.post("/plugins/sample/disable")

    assert response.status_code == 409


def test_api_uninstall_unknown_plugin_returns_404(client: TestClient):
    response = client.delete("/plugins/does-not-exist")

    assert response.status_code == 404
