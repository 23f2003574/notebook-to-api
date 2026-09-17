import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.data_validation import (
    DataValidationEngine,
    UnknownValidationReportError,
    UnsupportedValidationTypeError,
    ValidationReport,
    ValidationRule,
    ValidationType,
    get_data_validation_engine,
    router as data_validation_router,
)

ROWS = [
    {"region": "east", "amount": 10},
    {"region": "east", "amount": 20},
    {"region": None, "amount": 5},
]


@pytest.fixture
def engine() -> DataValidationEngine:
    return DataValidationEngine()


@pytest.fixture
def client(engine: DataValidationEngine) -> TestClient:
    app = FastAPI()
    app.include_router(data_validation_router)
    app.dependency_overrides[get_data_validation_engine] = lambda: engine
    return TestClient(app)


def test_check_schema_flags_missing_column(engine: DataValidationEngine):
    issues = engine.check_schema(ROWS, ["region", "amount", "currency"])

    assert len(issues) == 1
    assert issues[0]["column"] == "currency"


def test_check_schema_passes_when_columns_present(engine: DataValidationEngine):
    issues = engine.check_schema(ROWS, ["region", "amount"])

    assert issues == []


def test_check_schema_on_empty_rows_flags_all_expected(engine: DataValidationEngine):
    issues = engine.check_schema([], ["region"])

    assert len(issues) == 1


def test_null_values_rule_detects_null(engine: DataValidationEngine):
    issues = engine.check_quality(ROWS, [ValidationRule(rule_type=ValidationType.NULL_VALUES, column="region")])

    assert len(issues) == 1
    assert issues[0]["row_index"] == 2


def test_data_types_rule_flags_wrong_type(engine: DataValidationEngine):
    rows = [{"amount": 10}, {"amount": "not-a-number"}]

    issues = engine.check_quality(
        rows, [ValidationRule(rule_type=ValidationType.DATA_TYPES, column="amount", config={"expected_type": "int"})]
    )

    assert len(issues) == 1
    assert issues[0]["row_index"] == 1


def test_range_rule_flags_out_of_bounds_values(engine: DataValidationEngine):
    issues = engine.check_quality(
        ROWS, [ValidationRule(rule_type=ValidationType.RANGE, column="amount", config={"minimum": 8, "maximum": 15})]
    )

    values_flagged = {issue["row_index"] for issue in issues}
    assert values_flagged == {1, 2}


def test_uniqueness_rule_flags_duplicates(engine: DataValidationEngine):
    rows = [{"id": 1}, {"id": 2}, {"id": 1}]

    issues = engine.check_quality(rows, [ValidationRule(rule_type=ValidationType.UNIQUENESS, column="id")])

    assert len(issues) == 1
    assert issues[0]["row_index"] == 2


def test_check_quality_rejects_unsupported_rule_type(engine: DataValidationEngine):
    class FakeRule:
        rule_type = "not-a-real-type"
        column = "amount"
        config = {}

    with pytest.raises(UnsupportedValidationTypeError):
        engine.check_quality(ROWS, [FakeRule()])


def test_validate_aggregates_multiple_rules_into_report(engine: DataValidationEngine):
    rules = [
        ValidationRule(rule_type=ValidationType.NULL_VALUES, column="region"),
        ValidationRule(rule_type=ValidationType.RANGE, column="amount", config={"minimum": 8}),
    ]

    report = engine.validate(ROWS, rules)

    assert isinstance(report, ValidationReport)
    assert report.passed is False
    assert report.rule_count == 2
    assert report.row_count == 3
    assert len(report.issues) == 2


def test_validate_passes_when_no_issues(engine: DataValidationEngine):
    rows = [{"amount": 10}, {"amount": 20}]

    report = engine.validate(rows, [ValidationRule(rule_type=ValidationType.RANGE, column="amount", config={"minimum": 0})])

    assert report.passed is True
    assert report.issues == ()


def test_report_retrieves_stored_result(engine: DataValidationEngine):
    report = engine.validate(ROWS, [])

    fetched = engine.report(report.report_id)

    assert fetched.report_id == report.report_id


def test_report_unknown_id_raises(engine: DataValidationEngine):
    with pytest.raises(UnknownValidationReportError):
        engine.report("does-not-exist")


def test_api_validate_returns_report(client: TestClient):
    response = client.post(
        "/pipelines/validate",
        json={"rows": ROWS, "rules": [{"rule_type": "null_values", "column": "region"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert len(body["issues"]) == 1


def test_api_validate_schema_endpoint(client: TestClient):
    response = client.post(
        "/pipelines/validate/schema",
        json={"rows": ROWS, "expected_columns": ["region", "amount", "currency"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["issues"][0]["column"] == "currency"


def test_api_get_report_by_id(client: TestClient):
    created = client.post("/pipelines/validate", json={"rows": ROWS, "rules": []})
    report_id = created.json()["report_id"]

    response = client.get(f"/pipelines/validation/{report_id}")

    assert response.status_code == 200
    assert response.json()["report_id"] == report_id


def test_api_get_unknown_report_returns_404(client: TestClient):
    response = client.get("/pipelines/validation/does-not-exist")

    assert response.status_code == 404


def test_etl_execute_fails_extract_stage_on_pre_transform_validation(engine: DataValidationEngine):
    from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType
    from backend.pipeline.etl_engine import ETLWorkflowEngine
    from backend.pipeline.transformation_engine import DataTransformationEngine

    sources = DataSourceManager()
    sources.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    etl = ETLWorkflowEngine()
    etl.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")

    result = etl.execute(
        "orders-etl",
        ROWS,
        sources=sources,
        transformation_engine=DataTransformationEngine(),
        validation=engine,
        pre_transform_rules=[ValidationRule(rule_type=ValidationType.NULL_VALUES, column="region")],
    )

    assert result.status == "failed"
    assert result.failed_stage == "extract"


def test_etl_execute_fails_transform_stage_on_post_transform_validation(engine: DataValidationEngine):
    from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType
    from backend.pipeline.etl_engine import ETLWorkflowEngine
    from backend.pipeline.transformation_engine import DataTransformationEngine

    sources = DataSourceManager()
    sources.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    etl = ETLWorkflowEngine()
    etl.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")

    result = etl.execute(
        "orders-etl",
        ROWS,
        sources=sources,
        transformation_engine=DataTransformationEngine(),
        validation=engine,
        post_transform_rules=[ValidationRule(rule_type=ValidationType.NULL_VALUES, column="region")],
    )

    assert result.status == "failed"
    assert result.failed_stage == "transform"
    assert result.stages_completed == ("extract",)


def test_etl_execute_succeeds_when_validation_passes(engine: DataValidationEngine):
    from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType
    from backend.pipeline.etl_engine import ETLWorkflowEngine
    from backend.pipeline.transformation_engine import DataTransformationEngine

    sources = DataSourceManager()
    sources.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    etl = ETLWorkflowEngine()
    etl.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")

    result = etl.execute(
        "orders-etl",
        ROWS,
        sources=sources,
        transformation_engine=DataTransformationEngine(),
        validation=engine,
        pre_transform_rules=[ValidationRule(rule_type=ValidationType.UNIQUENESS, column="amount")],
    )

    assert result.status == "success"
    assert result.stages_completed == ("extract", "transform", "load", "post_processing")
