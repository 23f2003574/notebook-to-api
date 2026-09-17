import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.event_system import EventType, HookEventSystem
from backend.plugins.extension_api import (
    API_VERSION,
    EndpointAlreadyRegisteredError,
    ExtensionAPI,
    ExtensionCapability,
    ExtensionContext,
    IncompatibleApiVersionError,
    ServiceAlreadyRegisteredError,
    UnknownEndpointError,
    UnknownExtensionError,
    UnknownServiceError,
    get_extension_api,
    is_compatible_version,
    router as extension_api_router,
)


@pytest.fixture
def event_system() -> HookEventSystem:
    return HookEventSystem()


@pytest.fixture
def api(event_system: HookEventSystem) -> ExtensionAPI:
    return ExtensionAPI(event_system)


@pytest.fixture
def client(api: ExtensionAPI) -> TestClient:
    app = FastAPI()
    app.include_router(extension_api_router)
    app.dependency_overrides[get_extension_api] = lambda: api
    return TestClient(app)


def test_is_compatible_version_matches_major_version():
    assert is_compatible_version(API_VERSION) is True
    assert is_compatible_version("1.9") is True
    assert is_compatible_version("2.0") is False


def test_register_extension_creates_context(api: ExtensionAPI):
    context = api.register_extension("csv-exporter", "1.0", capabilities=["export"])

    assert isinstance(context, ExtensionContext)
    assert context.plugin == "csv-exporter"
    assert context.capabilities == (ExtensionCapability(name="export"),)


def test_register_extension_rejects_incompatible_version(api: ExtensionAPI):
    with pytest.raises(IncompatibleApiVersionError):
        api.register_extension("csv-exporter", "9.0")


def test_get_extension_unknown_raises(api: ExtensionAPI):
    with pytest.raises(UnknownExtensionError):
        api.get_extension("does-not-exist")


def test_register_extension_emits_plugin_enabled_event(api: ExtensionAPI, event_system: HookEventSystem):
    api.register_extension("csv-exporter", "1.0")

    events = event_system.list_events(event_type=EventType.PLUGIN_ENABLED.value)

    assert len(events) == 1
    assert events[0].source == "csv-exporter"
    assert events[0].payload["plugin"] == "csv-exporter"


def test_list_extensions_returns_sorted(api: ExtensionAPI):
    api.register_extension("zeta", "1.0")
    api.register_extension("alpha", "1.0")

    names = [context.plugin for context in api.list_extensions()]

    assert names == ["alpha", "zeta"]


def test_unregister_extension_removes_context(api: ExtensionAPI):
    api.register_extension("csv-exporter", "1.0")

    api.unregister_extension("csv-exporter")

    with pytest.raises(UnknownExtensionError):
        api.get_extension("csv-exporter")


def test_unregister_extension_is_idempotent(api: ExtensionAPI):
    api.unregister_extension("never-registered")


def test_register_endpoint_requires_known_extension(api: ExtensionAPI):
    with pytest.raises(UnknownExtensionError):
        api.register_endpoint("csv-exporter", "export", lambda: None)


def test_register_endpoint_duplicate_raises(api: ExtensionAPI):
    api.register_extension("csv-exporter", "1.0")
    api.register_endpoint("csv-exporter", "export", lambda: None)

    with pytest.raises(EndpointAlreadyRegisteredError):
        api.register_endpoint("csv-exporter", "export", lambda: None)


def test_invoke_calls_registered_handler(api: ExtensionAPI):
    api.register_extension("csv-exporter", "1.0")
    api.register_endpoint("csv-exporter", "export", lambda rows: f"exported {rows} rows")

    result = api.invoke("csv-exporter", "export", 42)

    assert result == "exported 42 rows"


def test_invoke_unknown_extension_raises(api: ExtensionAPI):
    with pytest.raises(UnknownExtensionError):
        api.invoke("does-not-exist", "export")


def test_invoke_unknown_endpoint_raises(api: ExtensionAPI):
    api.register_extension("csv-exporter", "1.0")

    with pytest.raises(UnknownEndpointError):
        api.invoke("csv-exporter", "does-not-exist")


def test_register_service_requires_known_extension(api: ExtensionAPI):
    with pytest.raises(UnknownExtensionError):
        api.register_service("csv-exporter", "formatter", object())


def test_register_service_duplicate_name_raises(api: ExtensionAPI):
    api.register_extension("csv-exporter", "1.0")
    api.register_extension("json-exporter", "1.0")
    api.register_service("csv-exporter", "formatter", object())

    with pytest.raises(ServiceAlreadyRegisteredError):
        api.register_service("json-exporter", "formatter", object())


def test_get_service_returns_registered_service(api: ExtensionAPI):
    api.register_extension("csv-exporter", "1.0")
    sentinel = object()
    api.register_service("csv-exporter", "formatter", sentinel)

    assert api.get_service("formatter") is sentinel


def test_get_service_unknown_raises(api: ExtensionAPI):
    with pytest.raises(UnknownServiceError):
        api.get_service("does-not-exist")


def test_unregister_extension_removes_owned_services(api: ExtensionAPI):
    api.register_extension("csv-exporter", "1.0")
    api.register_service("csv-exporter", "formatter", object())

    api.unregister_extension("csv-exporter")

    with pytest.raises(UnknownServiceError):
        api.get_service("formatter")


def test_api_register_and_list(client: TestClient):
    response = client.post(
        "/plugins/extensions",
        json={"plugin": "csv-exporter", "api_version": "1.0", "capabilities": ["export"]},
    )
    assert response.status_code == 201
    assert response.json()["plugin"] == "csv-exporter"

    listed = client.get("/plugins/extensions")
    assert listed.status_code == 200
    assert [item["plugin"] for item in listed.json()] == ["csv-exporter"]


def test_api_register_incompatible_version_returns_409(client: TestClient):
    response = client.post(
        "/plugins/extensions", json={"plugin": "csv-exporter", "api_version": "9.0"}
    )

    assert response.status_code == 409


def test_api_get_unknown_extension_returns_404(client: TestClient):
    response = client.get("/plugins/extensions/does-not-exist")

    assert response.status_code == 404


def test_api_get_registered_extension(client: TestClient):
    client.post("/plugins/extensions", json={"plugin": "csv-exporter", "api_version": "1.0"})

    response = client.get("/plugins/extensions/csv-exporter")

    assert response.status_code == 200
    assert response.json()["plugin"] == "csv-exporter"
