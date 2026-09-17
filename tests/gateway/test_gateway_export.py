import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.api_gateway import APIGateway
from backend.gateway.gateway_dashboard_api import GatewayDashboardAPI
from backend.gateway.gateway_export import (
    ExportMetadata,
    GatewayExport,
    GatewayExportService,
    UnsupportedExportFormatError,
    get_export_service,
    router as export_router,
)
from backend.gateway.middleware import MiddlewarePipeline
from backend.gateway.route_registry import RouteMetadata, RouteRegistry


@pytest.fixture
def gateway() -> APIGateway:
    return APIGateway()


@pytest.fixture
def route_registry() -> RouteRegistry:
    return RouteRegistry()


@pytest.fixture
def middleware_pipeline() -> MiddlewarePipeline:
    return MiddlewarePipeline()


@pytest.fixture
def dashboard(gateway: APIGateway, route_registry: RouteRegistry, middleware_pipeline: MiddlewarePipeline) -> GatewayDashboardAPI:
    return GatewayDashboardAPI(
        gateway,
        gateway.analytics,
        route_registry,
        middleware_pipeline=middleware_pipeline,
    )


@pytest.fixture
def service(dashboard: GatewayDashboardAPI) -> GatewayExportService:
    return GatewayExportService(dashboard)


@pytest.fixture
def client(service: GatewayExportService) -> TestClient:
    app = FastAPI()
    app.include_router(export_router)
    app.dependency_overrides[get_export_service] = lambda: service
    return TestClient(app)


# --- export_routes ---


def test_export_routes_json_contains_registered_route(
    service: GatewayExportService, route_registry: RouteRegistry
):
    route_registry.register("/notebooks", ["GET"], RouteMetadata(owner="alice"))

    export = service.export_routes("json")

    assert isinstance(export, GatewayExport)
    assert isinstance(export.metadata, ExportMetadata)
    assert export.metadata.format == "json"
    assert export.data[0]["path"] == "/notebooks"


def test_export_routes_yaml_contains_path(service: GatewayExportService, route_registry: RouteRegistry):
    route_registry.register("/notebooks", ["GET"])

    export = service.export_routes("yaml")

    assert isinstance(export.data, str)
    assert "/notebooks" in export.data


def test_export_routes_csv_has_header_and_row(service: GatewayExportService, route_registry: RouteRegistry):
    route_registry.register("/notebooks", ["GET"])

    export = service.export_routes("csv")

    lines = export.data.strip().splitlines()
    assert lines[0].startswith("path,")
    assert "/notebooks" in lines[1]


def test_export_routes_empty_when_no_routes(service: GatewayExportService):
    export = service.export_routes("json")

    assert export.data == []


def test_export_unsupported_format_raises(service: GatewayExportService):
    with pytest.raises(UnsupportedExportFormatError):
        service.export_routes("xml")


# --- export_configuration ---


def test_export_configuration_json_contains_middleware(
    service: GatewayExportService, middleware_pipeline: MiddlewarePipeline
):
    middleware_pipeline.register("logging", before=lambda ctx: None)

    export = service.export_configuration("json")

    assert len(export.data["middleware"]) == 1
    assert export.data["middleware"][0]["name"] == "logging"


def test_export_configuration_yaml_contains_key(service: GatewayExportService):
    export = service.export_configuration("yaml")

    assert "middleware" in export.data


# --- export_metrics ---


def test_export_metrics_json_reflects_dispatch_activity(
    service: GatewayExportService, gateway: APIGateway
):
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})

    export = service.export_metrics("json")

    assert export.data["total_responses"] == 1


def test_export_metrics_csv_has_header(service: GatewayExportService):
    export = service.export_metrics("csv")

    lines = export.data.strip().splitlines()
    assert lines[0] == "key,value"


# --- export_all ---


def test_export_all_bundles_routes_configuration_metrics(
    service: GatewayExportService, route_registry: RouteRegistry, gateway: APIGateway
):
    route_registry.register("/notebooks", ["GET"])
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})

    export = service.export_all("json")

    assert "routes" in export.data
    assert "configuration" in export.data
    assert "metrics" in export.data
    assert export.data["routes"][0]["path"] == "/notebooks"
    assert export.data["metrics"]["total_responses"] == 1


def test_export_all_yaml_contains_top_level_sections(service: GatewayExportService):
    export = service.export_all("yaml")

    assert "routes:" in export.data
    assert "configuration:" in export.data
    assert "metrics:" in export.data


def test_export_metadata_records_timestamp_and_source(service: GatewayExportService):
    export = service.export_all("json")

    assert export.metadata.source == "gateway-export-service"
    assert export.metadata.exported_at is not None


# --- API ---


def test_api_export_routes(client: TestClient, route_registry: RouteRegistry):
    route_registry.register("/notebooks", ["GET"])

    response = client.get("/gateway/export/routes")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["format"] == "json"
    assert body["data"][0]["path"] == "/notebooks"


def test_api_export_routes_yaml_format(client: TestClient, route_registry: RouteRegistry):
    route_registry.register("/notebooks", ["GET"])

    response = client.get("/gateway/export/routes", params={"format": "yaml"})

    assert response.status_code == 200
    assert "/notebooks" in response.json()["data"]


def test_api_export_unsupported_format_returns_422(client: TestClient):
    response = client.get("/gateway/export/routes", params={"format": "xml"})

    assert response.status_code == 422


def test_api_export_configuration(client: TestClient, middleware_pipeline: MiddlewarePipeline):
    middleware_pipeline.register("logging", before=lambda ctx: None)

    response = client.get("/gateway/export/configuration")

    assert response.status_code == 200
    assert len(response.json()["data"]["middleware"]) == 1


def test_api_export_metrics(client: TestClient, gateway: APIGateway):
    gateway.register_route("echo", lambda payload: payload)
    gateway.start()
    gateway.dispatch("echo", {})

    response = client.get("/gateway/export/metrics")

    assert response.status_code == 200
    assert response.json()["data"]["total_responses"] == 1


def test_api_export_all(client: TestClient, route_registry: RouteRegistry):
    route_registry.register("/notebooks", ["GET"])

    response = client.get("/gateway/export/all")

    assert response.status_code == 200
    body = response.json()["data"]
    assert "routes" in body
    assert "configuration" in body
    assert "metrics" in body
