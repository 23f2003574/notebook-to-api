import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_config import (
    ConfigField,
    ConfigSchema,
    ConfigValidationError,
    PluginConfig,
    PluginConfigurationManager,
    UnknownPluginConfigError,
    get_plugin_configuration_manager,
    router as plugin_config_router,
)


@pytest.fixture
def manager() -> PluginConfigurationManager:
    return PluginConfigurationManager()


@pytest.fixture
def client(manager: PluginConfigurationManager) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_config_router)
    app.dependency_overrides[get_plugin_configuration_manager] = lambda: manager
    return TestClient(app)


def _register_sample_schema(manager: PluginConfigurationManager) -> ConfigSchema:
    return manager.register_schema(
        "csv-exporter",
        [
            ConfigField(name="delimiter", type="string", default=","),
            ConfigField(name="max_rows", type="integer", required=True),
            ConfigField(name="include_header", type="boolean", default=True),
        ],
    )


def test_register_schema_returns_schema(manager: PluginConfigurationManager):
    schema = _register_sample_schema(manager)

    assert isinstance(schema, ConfigSchema)
    assert manager.get_schema("csv-exporter") == schema


def test_validate_without_schema_accepts_anything(manager: PluginConfigurationManager):
    normalized = manager.validate("no-schema-plugin", {"anything": "goes"})

    assert normalized == {"anything": "goes"}


def test_validate_fills_in_defaults(manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    normalized = manager.validate("csv-exporter", {"max_rows": 100})

    assert normalized == {"delimiter": ",", "max_rows": 100, "include_header": True}


def test_validate_rejects_missing_required_field(manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    with pytest.raises(ConfigValidationError):
        manager.validate("csv-exporter", {})


def test_validate_rejects_wrong_type(manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    with pytest.raises(ConfigValidationError):
        manager.validate("csv-exporter", {"max_rows": "not-a-number"})


def test_validate_rejects_unknown_field(manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    with pytest.raises(ConfigValidationError):
        manager.validate("csv-exporter", {"max_rows": 10, "bogus": True})


def test_save_persists_normalized_config(manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    config = manager.save("csv-exporter", {"max_rows": 50})

    assert isinstance(config, PluginConfig)
    assert config.version == 1
    assert config.values["max_rows"] == 50
    assert config.values["delimiter"] == ","


def test_save_increments_version_on_each_call(manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    first = manager.save("csv-exporter", {"max_rows": 10})
    second = manager.save("csv-exporter", {"max_rows": 20})

    assert first.version == 1
    assert second.version == 2
    assert [config.version for config in manager.get_history("csv-exporter")] == [1, 2]


def test_load_returns_saved_config(manager: PluginConfigurationManager):
    _register_sample_schema(manager)
    manager.save("csv-exporter", {"max_rows": 5})

    loaded = manager.load("csv-exporter")

    assert loaded.values["max_rows"] == 5


def test_load_without_saved_config_returns_defaults(manager: PluginConfigurationManager):
    manager.register_schema(
        "json-exporter",
        [ConfigField(name="pretty", type="boolean", default=False)],
    )

    loaded = manager.load("json-exporter")

    assert loaded.version == 0
    assert loaded.values == {"pretty": False}


def test_load_without_schema_or_saved_config_raises(manager: PluginConfigurationManager):
    with pytest.raises(UnknownPluginConfigError):
        manager.load("does-not-exist")


def test_load_with_schema_missing_required_default_raises(manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    with pytest.raises(ConfigValidationError):
        manager.load("csv-exporter")


def test_save_triggers_hot_reload_when_plugin_enabled(manager: PluginConfigurationManager):
    calls = []

    class FakeLifecycle:
        def reload(self, name):
            calls.append(name)

    manager.save("csv-exporter", {"anything": "goes"}, lifecycle=FakeLifecycle())

    assert calls == ["csv-exporter"]


def test_save_hot_reload_ignores_when_plugin_not_enabled():
    from backend.plugins.plugin_lifecycle import InvalidTransitionError

    manager = PluginConfigurationManager()

    class FakeLifecycle:
        def reload(self, name):
            raise InvalidTransitionError("not enabled")

    config = manager.save("csv-exporter", {"anything": "goes"}, lifecycle=FakeLifecycle())

    assert config.plugin == "csv-exporter"


def test_api_put_then_get_config(client: TestClient, manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    put_response = client.put("/plugins/csv-exporter/config", json={"max_rows": 25})
    assert put_response.status_code == 200
    assert put_response.json()["values"]["max_rows"] == 25

    get_response = client.get("/plugins/csv-exporter/config")
    assert get_response.status_code == 200
    assert get_response.json()["values"]["max_rows"] == 25


def test_api_get_unknown_config_returns_404(client: TestClient):
    response = client.get("/plugins/does-not-exist/config")

    assert response.status_code == 404


def test_api_put_invalid_config_returns_422(client: TestClient, manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    response = client.put("/plugins/csv-exporter/config", json={"max_rows": "nope"})

    assert response.status_code == 422


def test_api_validate_endpoint_returns_normalized_values(client: TestClient, manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    response = client.post("/plugins/csv-exporter/config/validate", json={"max_rows": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["values"]["max_rows"] == 10


def test_api_validate_endpoint_returns_422_for_invalid(client: TestClient, manager: PluginConfigurationManager):
    _register_sample_schema(manager)

    response = client.post("/plugins/csv-exporter/config/validate", json={})

    assert response.status_code == 422
