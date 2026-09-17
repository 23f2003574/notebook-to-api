import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.data_validation import DataValidationEngine, get_data_validation_engine
from backend.pipeline.schema_registry import (
    IncompatibleSchemaError,
    SchemaAlreadyExistsError,
    SchemaDefinition,
    SchemaRegistry,
    SchemaVersion,
    UnknownSchemaError,
    UnknownSchemaVersionError,
    get_schema_registry,
    router as schema_registry_router,
)

ORDER_FIELDS = [
    {"name": "region", "type": "str", "nullable": False},
    {"name": "amount", "type": "int", "nullable": False},
]


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry()


@pytest.fixture
def validation_engine() -> DataValidationEngine:
    return DataValidationEngine()


@pytest.fixture
def client(registry: SchemaRegistry, validation_engine: DataValidationEngine) -> TestClient:
    app = FastAPI()
    app.include_router(schema_registry_router)
    app.dependency_overrides[get_schema_registry] = lambda: registry
    app.dependency_overrides[get_data_validation_engine] = lambda: validation_engine
    return TestClient(app)


def test_register_creates_schema_with_version_one(registry: SchemaRegistry):
    schema = registry.register("orders", ORDER_FIELDS)

    assert isinstance(schema, SchemaDefinition)
    assert schema.name == "orders"
    assert len(schema.versions) == 1
    assert schema.latest.version == 1


def test_register_rejects_duplicate_name(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)

    with pytest.raises(SchemaAlreadyExistsError):
        registry.register("orders", ORDER_FIELDS)


def test_update_appends_new_version(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)
    new_fields = ORDER_FIELDS + [{"name": "currency", "type": "str", "nullable": True}]

    version = registry.update("orders", new_fields)

    assert isinstance(version, SchemaVersion)
    assert version.version == 2
    assert len(registry.get("orders").versions) == 2


def test_update_unknown_schema_raises(registry: SchemaRegistry):
    with pytest.raises(UnknownSchemaError):
        registry.update("does-not-exist", ORDER_FIELDS)


def test_update_rejects_removed_field(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)

    with pytest.raises(IncompatibleSchemaError):
        registry.update("orders", [{"name": "region", "type": "str", "nullable": False}])


def test_update_rejects_type_change(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)
    changed = [
        {"name": "region", "type": "str", "nullable": False},
        {"name": "amount", "type": "float", "nullable": False},
    ]

    with pytest.raises(IncompatibleSchemaError):
        registry.update("orders", changed)


def test_update_rejects_new_required_field(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)
    new_fields = ORDER_FIELDS + [{"name": "currency", "type": "str", "nullable": False}]

    with pytest.raises(IncompatibleSchemaError):
        registry.update("orders", new_fields)


def test_update_allows_new_nullable_field(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)
    new_fields = ORDER_FIELDS + [{"name": "currency", "type": "str", "nullable": True}]

    version = registry.update("orders", new_fields)

    assert len(version.fields) == 3


def test_get_unknown_schema_raises(registry: SchemaRegistry):
    with pytest.raises(UnknownSchemaError):
        registry.get("does-not-exist")


def test_list_schemas_sorted_by_name(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)
    registry.register("customers", [{"name": "id", "type": "int", "nullable": False}])

    listed = [schema.name for schema in registry.list_schemas()]

    assert listed == ["customers", "orders"]


def test_history_returns_all_versions(registry: SchemaRegistry):
    registry.register("orders", ORDER_FIELDS)
    registry.update("orders", ORDER_FIELDS + [{"name": "currency", "type": "str", "nullable": True}])

    history = registry.history("orders")

    assert [v.version for v in history] == [1, 2]


def test_validate_passes_for_conforming_rows(registry: SchemaRegistry, validation_engine: DataValidationEngine):
    registry.register("orders", ORDER_FIELDS)
    rows = [{"region": "east", "amount": 10}]

    report = registry.validate("orders", rows, validation_engine)

    assert report.passed is True
    assert report.schema_name == "orders"
    assert report.schema_version == 1


def test_validate_flags_null_in_required_field(registry: SchemaRegistry, validation_engine: DataValidationEngine):
    registry.register("orders", ORDER_FIELDS)
    rows = [{"region": None, "amount": 10}]

    report = registry.validate("orders", rows, validation_engine)

    assert report.passed is False


def test_validate_flags_wrong_type(registry: SchemaRegistry, validation_engine: DataValidationEngine):
    registry.register("orders", ORDER_FIELDS)
    rows = [{"region": "east", "amount": "ten"}]

    report = registry.validate("orders", rows, validation_engine)

    assert report.passed is False


def test_validate_against_specific_version(registry: SchemaRegistry, validation_engine: DataValidationEngine):
    registry.register("orders", ORDER_FIELDS)
    registry.update("orders", ORDER_FIELDS + [{"name": "currency", "type": "str", "nullable": True}])
    rows = [{"region": "east", "amount": 10}]

    report = registry.validate("orders", rows, validation_engine, version=1)

    assert report.schema_version == 1
    assert report.passed is True


def test_validate_unknown_version_raises(registry: SchemaRegistry, validation_engine: DataValidationEngine):
    registry.register("orders", ORDER_FIELDS)

    with pytest.raises(UnknownSchemaVersionError):
        registry.validate("orders", [], validation_engine, version=99)


def test_validate_unknown_schema_raises(registry: SchemaRegistry, validation_engine: DataValidationEngine):
    with pytest.raises(UnknownSchemaError):
        registry.validate("does-not-exist", [], validation_engine)


def test_api_register_creates_schema(client: TestClient):
    response = client.post("/pipelines/schemas", json={"name": "orders", "fields": ORDER_FIELDS})

    assert response.status_code == 201
    assert len(response.json()["versions"]) == 1


def test_api_post_again_upserts_new_version(client: TestClient):
    client.post("/pipelines/schemas", json={"name": "orders", "fields": ORDER_FIELDS})
    new_fields = ORDER_FIELDS + [{"name": "currency", "type": "str", "nullable": True}]

    response = client.post("/pipelines/schemas", json={"name": "orders", "fields": new_fields})

    assert response.status_code == 201
    assert len(response.json()["versions"]) == 2


def test_api_list_schemas(client: TestClient):
    client.post("/pipelines/schemas", json={"name": "orders", "fields": ORDER_FIELDS})

    response = client.get("/pipelines/schemas")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_schema_returns_history(client: TestClient):
    client.post("/pipelines/schemas", json={"name": "orders", "fields": ORDER_FIELDS})
    client.post(
        "/pipelines/schemas",
        json={"name": "orders", "fields": ORDER_FIELDS + [{"name": "currency", "type": "str", "nullable": True}]},
    )

    response = client.get("/pipelines/schemas/orders")

    assert response.status_code == 200
    assert len(response.json()["versions"]) == 2


def test_api_get_unknown_schema_returns_404(client: TestClient):
    response = client.get("/pipelines/schemas/does-not-exist")

    assert response.status_code == 404


def test_api_validate_endpoint(client: TestClient):
    client.post("/pipelines/schemas", json={"name": "orders", "fields": ORDER_FIELDS})

    response = client.post(
        "/pipelines/schemas/orders/validate", json={"rows": [{"region": "east", "amount": 10}]}
    )

    assert response.status_code == 200
    assert response.json()["passed"] is True


def test_api_validate_unknown_schema_returns_404(client: TestClient):
    response = client.post("/pipelines/schemas/does-not-exist/validate", json={"rows": []})

    assert response.status_code == 404
