import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.data_sources import (
    ConnectionProfile,
    DataSource,
    DataSourceAlreadyRegisteredError,
    DataSourceManager,
    InvalidConnectionProfileError,
    SourceType,
    UnknownDataSourceError,
    get_data_source_manager,
    router as data_sources_router,
)
from backend.pipeline.pipeline_registry import PipelineRegistry


@pytest.fixture
def manager() -> DataSourceManager:
    return DataSourceManager()


@pytest.fixture
def client(manager: DataSourceManager) -> TestClient:
    app = FastAPI()
    app.include_router(data_sources_router)
    app.dependency_overrides[get_data_source_manager] = lambda: manager
    return TestClient(app)


def test_register_creates_source(manager: DataSourceManager):
    source = manager.register(
        "orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders")
    )

    assert isinstance(source, DataSource)
    assert source.name == "orders-db"
    assert source.connection.source_type == SourceType.SQL
    assert source.healthy is None


def test_connection_profile_round_trips_through_dict():
    profile = ConnectionProfile.from_dict(
        {"source_type": "rest_api", "uri": "https://api.example.com", "credential_ref": "vault:api-key"}
    )

    assert profile.source_type == SourceType.REST_API
    assert profile.to_dict()["credential_ref"] == "vault:api-key"


def test_register_rejects_empty_uri(manager: DataSourceManager):
    with pytest.raises(InvalidConnectionProfileError):
        manager.register("orders-db", ConnectionProfile(source_type=SourceType.CSV, uri=""))


def test_register_rejects_rest_api_without_http_scheme(manager: DataSourceManager):
    with pytest.raises(InvalidConnectionProfileError):
        manager.register(
            "orders-api", ConnectionProfile(source_type=SourceType.REST_API, uri="ftp://example.com")
        )


def test_register_rejects_sql_without_scheme(manager: DataSourceManager):
    with pytest.raises(InvalidConnectionProfileError):
        manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="db/orders"))


def test_register_rejects_object_storage_without_scheme(manager: DataSourceManager):
    with pytest.raises(InvalidConnectionProfileError):
        manager.register(
            "raw-events", ConnectionProfile(source_type=SourceType.OBJECT_STORAGE, uri="my-bucket/events")
        )


def test_register_allows_csv_with_plain_path(manager: DataSourceManager):
    source = manager.register("local-csv", ConnectionProfile(source_type=SourceType.CSV, uri="/data/in.csv"))

    assert source.connection.uri == "/data/in.csv"


def test_register_rejects_duplicate(manager: DataSourceManager):
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))

    with pytest.raises(DataSourceAlreadyRegisteredError):
        manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))


def test_connect_marks_source_healthy(manager: DataSourceManager):
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))

    source = manager.connect("orders-db")

    assert source.healthy is True
    assert source.last_checked_at is not None


def test_disconnect_marks_source_unhealthy(manager: DataSourceManager):
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    manager.connect("orders-db")

    source = manager.disconnect("orders-db")

    assert source.healthy is False


def test_connect_unknown_source_raises(manager: DataSourceManager):
    with pytest.raises(UnknownDataSourceError):
        manager.connect("does-not-exist")


def test_get_unknown_source_raises(manager: DataSourceManager):
    with pytest.raises(UnknownDataSourceError):
        manager.get("does-not-exist")


def test_list_sources_sorted_by_name(manager: DataSourceManager):
    manager.register("z-source", ConnectionProfile(source_type=SourceType.CSV, uri="/z.csv"))
    manager.register("a-source", ConnectionProfile(source_type=SourceType.CSV, uri="/a.csv"))

    listed = [source.name for source in manager.list_sources()]

    assert listed == ["a-source", "z-source"]


def test_remove_deletes_source(manager: DataSourceManager):
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))

    manager.remove("orders-db")

    with pytest.raises(UnknownDataSourceError):
        manager.get("orders-db")


def test_remove_unknown_source_raises(manager: DataSourceManager):
    with pytest.raises(UnknownDataSourceError):
        manager.remove("does-not-exist")


def test_pipeline_registry_connects_bound_source(manager: DataSourceManager):
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    registry = PipelineRegistry()

    registry.register("ingest-orders", "1.0.0", sources=manager, source_name="orders-db")

    assert manager.get("orders-db").healthy is True


def test_pipeline_registry_raises_for_unknown_bound_source(manager: DataSourceManager):
    registry = PipelineRegistry()

    with pytest.raises(UnknownDataSourceError):
        registry.register("ingest-orders", "1.0.0", sources=manager, source_name="does-not-exist")


def test_api_register_and_list(client: TestClient):
    response = client.post(
        "/pipelines/sources",
        json={"name": "orders-db", "connection": {"source_type": "sql", "uri": "postgres://db/orders"}},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "orders-db"

    listed = client.get("/pipelines/sources")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_invalid_connection_returns_422(client: TestClient):
    response = client.post(
        "/pipelines/sources",
        json={"name": "orders-api", "connection": {"source_type": "rest_api", "uri": "ftp://example.com"}},
    )

    assert response.status_code == 422


def test_api_register_duplicate_returns_409(client: TestClient):
    payload = {"name": "orders-db", "connection": {"source_type": "sql", "uri": "postgres://db/orders"}}
    client.post("/pipelines/sources", json=payload)
    response = client.post("/pipelines/sources", json=payload)

    assert response.status_code == 409


def test_api_get_unknown_source_returns_404(client: TestClient):
    response = client.get("/pipelines/sources/does-not-exist")

    assert response.status_code == 404


def test_api_delete_removes_source(client: TestClient):
    client.post(
        "/pipelines/sources",
        json={"name": "orders-db", "connection": {"source_type": "sql", "uri": "postgres://db/orders"}},
    )

    response = client.delete("/pipelines/sources/orders-db")
    assert response.status_code == 204

    assert client.get("/pipelines/sources/orders-db").status_code == 404
