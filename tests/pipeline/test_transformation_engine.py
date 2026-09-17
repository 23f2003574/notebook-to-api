import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.data_sources import (
    ConnectionProfile,
    DataSourceManager,
    SourceType,
    UnknownDataSourceError,
    get_data_source_manager,
)
from backend.pipeline.transformation_engine import (
    DataTransformationEngine,
    OperationType,
    TransformationResult,
    TransformationStep,
    UnknownColumnError,
    UnsupportedOperationError,
    get_data_transformation_engine,
    router as transformation_engine_router,
)

ROWS = [
    {"region": "east", "amount": 10},
    {"region": "east", "amount": 20},
    {"region": "west", "amount": 5},
]


@pytest.fixture
def engine() -> DataTransformationEngine:
    return DataTransformationEngine()


@pytest.fixture
def client(engine: DataTransformationEngine) -> TestClient:
    app = FastAPI()
    app.include_router(transformation_engine_router)
    app.dependency_overrides[get_data_transformation_engine] = lambda: engine
    app.dependency_overrides[get_data_source_manager] = lambda: DataSourceManager()
    return TestClient(app)


def test_map_columns_renames_fields(engine: DataTransformationEngine):
    mapped = engine.map_columns(ROWS, {"zone": "region", "total": "amount"})

    assert mapped[0] == {"zone": "east", "total": 10}


def test_map_columns_raises_for_unknown_source_column(engine: DataTransformationEngine):
    with pytest.raises(UnknownColumnError):
        engine.map_columns(ROWS, {"zone": "does-not-exist"})


def test_filter_rows_supports_eq(engine: DataTransformationEngine):
    filtered = engine.filter_rows(ROWS, "region", "eq", "east")

    assert len(filtered) == 2


def test_filter_rows_supports_gt(engine: DataTransformationEngine):
    filtered = engine.filter_rows(ROWS, "amount", "gt", 8)

    assert len(filtered) == 2


def test_filter_rows_rejects_unsupported_operator(engine: DataTransformationEngine):
    with pytest.raises(UnsupportedOperationError):
        engine.filter_rows(ROWS, "amount", "unsupported", 8)


def test_aggregate_sums_grouped_column(engine: DataTransformationEngine):
    result = engine.aggregate(ROWS, ["region"], "amount", "sum")

    lookup = {row["region"]: row["sum_amount"] for row in result}
    assert lookup == {"east": 30, "west": 5}


def test_aggregate_rejects_unsupported_func(engine: DataTransformationEngine):
    with pytest.raises(UnsupportedOperationError):
        engine.aggregate(ROWS, ["region"], "amount", "median")


def test_sort_rows_orders_ascending(engine: DataTransformationEngine):
    sorted_rows = engine.sort_rows(ROWS, "amount")

    assert [row["amount"] for row in sorted_rows] == [5, 10, 20]


def test_join_rows_merges_matching_keys(engine: DataTransformationEngine):
    left = [{"id": 1, "region": "east"}, {"id": 2, "region": "west"}]
    right = [{"ref_id": 1, "manager": "alice"}, {"ref_id": 2, "manager": "bob"}]

    joined = engine.join_rows(left, right, "id", "ref_id")

    assert {"id": 1, "region": "east", "manager": "alice"} in joined


def test_transform_chains_filter_then_aggregate(engine: DataTransformationEngine):
    steps = [
        TransformationStep(operation=OperationType.FILTER, config={"column": "region", "operator": "eq", "value": "east"}),
        TransformationStep(operation=OperationType.AGGREGATE, config={"group_by": ["region"], "column": "amount", "func": "sum"}),
    ]

    result = engine.transform(ROWS, steps)

    assert isinstance(result, TransformationResult)
    assert result.rows[0]["sum_amount"] == 30
    assert result.row_count == 1


def test_transform_records_history(engine: DataTransformationEngine):
    engine.transform(ROWS, [TransformationStep(operation=OperationType.SORT, config={"column": "amount"})])

    history = engine.history()
    assert len(history) == 1
    assert history[0].row_count == 3


def test_preview_does_not_record_history(engine: DataTransformationEngine):
    engine.preview(ROWS, [TransformationStep(operation=OperationType.SORT, config={"column": "amount"})])

    assert engine.history() == []


def test_preview_truncates_rows_but_keeps_full_row_count(engine: DataTransformationEngine):
    result = engine.preview(ROWS, [], limit=1)

    assert len(result.rows) == 1
    assert result.row_count == 3


def test_transform_marks_bound_source_as_read(engine: DataTransformationEngine):
    sources = DataSourceManager()
    sources.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))

    engine.transform(ROWS, [], sources=sources, source_name="orders-db")

    assert sources.get("orders-db").read_count == 1


def test_transform_raises_for_unknown_bound_source(engine: DataTransformationEngine):
    sources = DataSourceManager()

    with pytest.raises(UnknownDataSourceError):
        engine.transform(ROWS, [], sources=sources, source_name="does-not-exist")


def test_api_transform_returns_result(client: TestClient):
    response = client.post(
        "/pipelines/transform",
        json={
            "rows": ROWS,
            "steps": [{"operation": "filter", "config": {"column": "region", "operator": "eq", "value": "west"}}],
        },
    )

    assert response.status_code == 200
    assert response.json()["row_count"] == 1


def test_api_transform_invalid_operation_returns_422(client: TestClient):
    response = client.post(
        "/pipelines/transform",
        json={"rows": ROWS, "steps": [{"operation": "not-a-real-op", "config": {}}]},
    )

    assert response.status_code == 422


def test_api_preview_does_not_appear_in_history(client: TestClient):
    client.post("/pipelines/transform/preview", json={"rows": ROWS, "steps": []})

    history = client.get("/pipelines/transform/history")
    assert history.status_code == 200
    assert history.json() == []


def test_api_history_lists_recorded_transforms(client: TestClient):
    client.post("/pipelines/transform", json={"rows": ROWS, "steps": []})

    history = client.get("/pipelines/transform/history")

    assert history.status_code == 200
    assert len(history.json()) == 1
