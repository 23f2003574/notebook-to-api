import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.request_validation import (
    RequestValidationEngine,
    UnknownValidationRuleError,
    ValidationResult,
    ValidationRule,
    ValidationRuleAlreadyRegisteredError,
    get_validation_engine,
    router as validation_router,
)


@pytest.fixture
def engine() -> RequestValidationEngine:
    return RequestValidationEngine()


@pytest.fixture
def client(engine: RequestValidationEngine) -> TestClient:
    app = FastAPI()
    app.include_router(validation_router)
    app.dependency_overrides[get_validation_engine] = lambda: engine
    return TestClient(app)


BODY_SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
}


def test_register_rule_creates_rule(engine: RequestValidationEngine):
    rule = engine.register_rule(ValidationRule(route="/notebooks", required_headers=("x-api-key",)))

    assert isinstance(rule, ValidationRule)
    assert rule.route == "/notebooks"


def test_register_rule_rejects_duplicate_route(engine: RequestValidationEngine):
    engine.register_rule(ValidationRule(route="/notebooks"))

    with pytest.raises(ValidationRuleAlreadyRegisteredError):
        engine.register_rule(ValidationRule(route="/notebooks"))


def test_get_rule_unknown_route_raises(engine: RequestValidationEngine):
    with pytest.raises(UnknownValidationRuleError):
        engine.get_rule("/does-not-exist")


def test_remove_rule_deletes_rule(engine: RequestValidationEngine):
    engine.register_rule(ValidationRule(route="/notebooks"))

    engine.remove_rule("/notebooks")

    with pytest.raises(UnknownValidationRuleError):
        engine.get_rule("/notebooks")


def test_remove_rule_unknown_route_raises(engine: RequestValidationEngine):
    with pytest.raises(UnknownValidationRuleError):
        engine.remove_rule("/does-not-exist")


# --- validate_headers ---


def test_validate_headers_reports_missing_header(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks", required_headers=("x-api-key",))

    errors = engine.validate_headers(rule, {})

    assert any("x-api-key" in error for error in errors)


def test_validate_headers_passes_when_present_case_insensitive(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks", required_headers=("X-Api-Key",))

    errors = engine.validate_headers(rule, {"x-api-key": "secret"})

    assert errors == []


def test_validate_headers_checks_content_type(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks", content_type="application/json")

    errors = engine.validate_headers(rule, {"content-type": "text/plain"})

    assert any("content type" in error for error in errors)


def test_validate_headers_allows_content_type_with_charset(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks", content_type="application/json")

    errors = engine.validate_headers(rule, {"content-type": "application/json; charset=utf-8"})

    assert errors == []


# --- validate_params ---


def test_validate_params_reports_missing_query_param(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks", required_params=("page",))

    errors = engine.validate_params(rule, {})

    assert any("page" in error for error in errors)


def test_validate_params_reports_missing_path_param(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks/{id}", required_path_params=("id",))

    errors = engine.validate_params(rule, {}, {})

    assert any("id" in error for error in errors)


def test_validate_params_passes_when_present(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks/{id}", required_params=("page",), required_path_params=("id",))

    errors = engine.validate_params(rule, {"page": "1"}, {"id": "abc"})

    assert errors == []


# --- validate_schema / validate_body ---


def test_validate_schema_reports_missing_required_field(engine: RequestValidationEngine):
    errors = engine.validate_schema(BODY_SCHEMA, {"age": 5})

    assert any("name" in error for error in errors)


def test_validate_schema_reports_type_mismatch(engine: RequestValidationEngine):
    errors = engine.validate_schema(BODY_SCHEMA, {"name": "alice", "age": "not-a-number"})

    assert any("age" in error for error in errors)


def test_validate_schema_rejects_boolean_as_integer(engine: RequestValidationEngine):
    errors = engine.validate_schema(BODY_SCHEMA, {"name": "alice", "age": True})

    assert any("age" in error for error in errors)


def test_validate_schema_passes_valid_body(engine: RequestValidationEngine):
    errors = engine.validate_schema(BODY_SCHEMA, {"name": "alice", "age": 30})

    assert errors == []


def test_validate_schema_top_level_type_mismatch(engine: RequestValidationEngine):
    errors = engine.validate_schema(BODY_SCHEMA, ["not", "an", "object"])

    assert len(errors) == 1
    assert "expected type object" in errors[0]


def test_validate_body_requires_body_when_schema_set(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks", schema=BODY_SCHEMA)

    errors = engine.validate_body(rule, None)

    assert errors == ["body is required"]


def test_validate_body_skips_when_no_schema(engine: RequestValidationEngine):
    rule = ValidationRule(route="/notebooks")

    errors = engine.validate_body(rule, None)

    assert errors == []


# --- validate_request aggregation ---


def test_validate_request_aggregates_errors_across_checks(engine: RequestValidationEngine):
    rule = ValidationRule(
        route="/notebooks",
        required_headers=("x-api-key",),
        required_params=("page",),
        schema=BODY_SCHEMA,
    )

    result = engine.validate_request(rule, headers={}, params={}, body=None)

    assert isinstance(result, ValidationResult)
    assert result.valid is False
    assert len(result.errors) == 3


def test_validate_request_valid_when_all_checks_pass(engine: RequestValidationEngine):
    rule = ValidationRule(
        route="/notebooks",
        required_headers=("x-api-key",),
        required_params=("page",),
        schema=BODY_SCHEMA,
    )

    result = engine.validate_request(
        rule,
        headers={"x-api-key": "secret"},
        params={"page": "1"},
        body={"name": "alice"},
    )

    assert result.valid is True
    assert result.errors == ()


# --- API ---


def test_api_validate_unknown_route_returns_404(client: TestClient):
    response = client.post("/gateway/validate", json={"route": "/does-not-exist"})

    assert response.status_code == 404


def test_api_validate_reports_aggregated_errors(client: TestClient, engine: RequestValidationEngine):
    engine.register_rule(
        ValidationRule(route="/notebooks", required_headers=("x-api-key",), schema=BODY_SCHEMA)
    )

    response = client.post("/gateway/validate", json={"route": "/notebooks", "headers": {}, "body": None})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert len(body["errors"]) == 2


def test_api_validate_returns_valid_true(client: TestClient, engine: RequestValidationEngine):
    engine.register_rule(ValidationRule(route="/notebooks", required_headers=("x-api-key",)))

    response = client.post(
        "/gateway/validate", json={"route": "/notebooks", "headers": {"x-api-key": "secret"}}
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_api_validate_schema_endpoint(client: TestClient):
    response = client.post(
        "/gateway/validate/schema", json={"schema": BODY_SCHEMA, "body": {"age": 5}}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any("name" in error for error in body["errors"])


def test_api_list_validation_rules(client: TestClient, engine: RequestValidationEngine):
    engine.register_rule(ValidationRule(route="/notebooks"))
    engine.register_rule(ValidationRule(route="/exports"))

    response = client.get("/gateway/validation/rules")

    assert response.status_code == 200
    routes = {rule["route"] for rule in response.json()}
    assert routes == {"/notebooks", "/exports"}
