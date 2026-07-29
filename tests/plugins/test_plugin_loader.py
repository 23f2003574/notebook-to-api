import json
import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_dependencies import PluginDependencyManager
from backend.plugins.plugin_loader import (
    LoadedPlugin,
    ManifestValidationError,
    PluginAlreadyLoadedError,
    PluginLoader,
    PluginManifest,
    PluginNotLoadedError,
    UnmetDependencyError,
    get_plugin_loader,
    router as plugin_loader_router,
)
from backend.plugins.plugin_registry import PluginRegistry, get_plugin_registry


def _unique_module_name() -> str:
    return f"sample_plugin_{uuid.uuid4().hex}"


@pytest.fixture
def plugin_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    # Avoid stale __pycache__ bytecode being reused when a test rewrites the
    # source file and reloads within the same filesystem mtime tick.
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
def client(loader: PluginLoader, registry: PluginRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_loader_router)
    app.dependency_overrides[get_plugin_loader] = lambda: loader
    app.dependency_overrides[get_plugin_registry] = lambda: registry
    return TestClient(app)


def test_manifest_rejects_missing_fields():
    with pytest.raises(ManifestValidationError):
        PluginManifest.from_dict({"name": "sample", "version": "1.0.0"})


def test_manifest_rejects_invalid_entry_point():
    with pytest.raises(ManifestValidationError):
        PluginManifest.from_dict(
            {"name": "sample", "version": "1.0.0", "entry_point": "not a module!"}
        )


def test_discover_finds_manifests(tmp_path):
    (tmp_path / "sample.plugin.json").write_text(
        json.dumps({"name": "sample", "version": "1.0.0", "entry_point": "sample_plugin"})
    )
    (tmp_path / "other.plugin.json").write_text(
        json.dumps({"name": "other", "version": "1.0.0", "entry_point": "other_plugin"})
    )

    manifests = PluginLoader().discover(str(tmp_path))

    assert [manifest.name for manifest in manifests] == ["other", "sample"]


def test_discover_rejects_invalid_manifest(tmp_path):
    (tmp_path / "broken.plugin.json").write_text(json.dumps({"name": "broken"}))

    with pytest.raises(ManifestValidationError):
        PluginLoader().discover(str(tmp_path))


def test_load_imports_module_and_registers(loader: PluginLoader, registry: PluginRegistry, plugin_module):
    module_name = plugin_module(value=42)
    manifest = PluginManifest(name="sample", version="1.0.0", entry_point=module_name)

    loaded = loader.load(manifest)

    assert isinstance(loaded, LoadedPlugin)
    assert loaded.module.VALUE == 42
    assert registry.is_registered("sample", "1.0.0")


def test_load_twice_raises(loader: PluginLoader, plugin_module):
    module_name = plugin_module()
    manifest = PluginManifest(name="sample", version="1.0.0", entry_point=module_name)
    loader.load(manifest)

    with pytest.raises(PluginAlreadyLoadedError):
        loader.load(manifest)


def test_load_with_unmet_dependency_raises(loader: PluginLoader, plugin_module):
    module_name = plugin_module()
    manifest = PluginManifest(name="app", version="1.0.0", entry_point=module_name)
    dependencies = PluginDependencyManager()
    dependencies.add_dependency("app", "auth-plugin")

    with pytest.raises(UnmetDependencyError):
        loader.load(manifest, dependencies=dependencies)


def test_load_with_satisfied_dependency_succeeds(loader: PluginLoader, plugin_module):
    auth_module = plugin_module()
    app_module = plugin_module()
    dependencies = PluginDependencyManager()
    dependencies.add_dependency("app", "auth-plugin")

    loader.load(PluginManifest(name="auth-plugin", version="1.0.0", entry_point=auth_module))
    loaded = loader.load(
        PluginManifest(name="app", version="1.0.0", entry_point=app_module), dependencies=dependencies
    )

    assert isinstance(loaded, LoadedPlugin)


def test_reload_picks_up_source_changes(loader: PluginLoader, plugin_module, tmp_path):
    module_name = plugin_module(value=1)
    manifest = PluginManifest(name="sample", version="1.0.0", entry_point=module_name)
    loaded = loader.load(manifest)
    assert loaded.module.VALUE == 1

    (tmp_path / f"{module_name}.py").write_text("VALUE = 2\n")
    reloaded = loader.reload("sample")

    assert reloaded.module.VALUE == 2
    assert reloaded.loaded_at >= loaded.loaded_at


def test_reload_unloaded_plugin_raises(loader: PluginLoader):
    with pytest.raises(PluginNotLoadedError):
        loader.reload("does-not-exist")


def test_unload_removes_from_loaded_list(loader: PluginLoader, plugin_module):
    module_name = plugin_module()
    manifest = PluginManifest(name="sample", version="1.0.0", entry_point=module_name)
    loader.load(manifest)

    loader.unload("sample")

    assert loader.is_loaded("sample") is False
    with pytest.raises(PluginNotLoadedError):
        loader.unload("sample")


def test_list_loaded_returns_all_loaded_plugins(loader: PluginLoader, plugin_module):
    first = plugin_module()
    second = plugin_module()
    loader.load(PluginManifest(name="first", version="1.0.0", entry_point=first))
    loader.load(PluginManifest(name="second", version="1.0.0", entry_point=second))

    names = [loaded.manifest.name for loaded in loader.list_loaded()]

    assert names == ["first", "second"]


def test_api_load_then_list(client: TestClient, plugin_module):
    module_name = plugin_module()

    response = client.post(
        "/plugins/load",
        json={"name": "sample", "version": "1.0.0", "entry_point": module_name},
    )
    assert response.status_code == 201

    listed = client.get("/plugins/loaded")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["sample"]


def test_api_load_invalid_manifest_returns_422(client: TestClient):
    response = client.post("/plugins/load", json={"name": "sample"})

    assert response.status_code == 422


def test_api_reload_unloaded_returns_404(client: TestClient):
    response = client.post("/plugins/reload/does-not-exist")

    assert response.status_code == 404


def test_api_unload_removes_plugin(client: TestClient, plugin_module):
    module_name = plugin_module()
    client.post(
        "/plugins/load",
        json={"name": "sample", "version": "1.0.0", "entry_point": module_name},
    )

    response = client.post("/plugins/unload/sample")
    assert response.status_code == 204

    assert client.get("/plugins/loaded").json() == []
