import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.data_sources import (
    ConnectionProfile,
    DataSourceManager,
    SourceType,
    get_data_source_manager,
)
from backend.pipeline.transformation_engine import (
    DataTransformationEngine,
    OperationType,
    TransformationStep,
    get_data_transformation_engine,
)
from backend.pipeline.etl_engine import (
    ETLWorkflowEngine,
    ExecutionResult,
    LoadTargetError,
    UnknownWorkflowError,
    UnsupportedPostProcessingActionError,
    WorkflowAlreadyExistsError,
    get_etl_workflow_engine,
    router as etl_engine_router,
)

ROWS = [
    {"region": "east", "amount": 10},
    {"region": "east", "amount": 20},
    {"region": "west", "amount": 5},
]


@pytest.fixture
def sources() -> DataSourceManager:
    manager = DataSourceManager()
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    return manager


@pytest.fixture
def transformation_engine() -> DataTransformationEngine:
    return DataTransformationEngine()


@pytest.fixture
def engine() -> ETLWorkflowEngine:
    return ETLWorkflowEngine()


@pytest.fixture
def client(engine, transformation_engine, sources) -> TestClient:
    app = FastAPI()
    app.include_router(etl_engine_router)
    app.dependency_overrides[get_etl_workflow_engine] = lambda: engine
    app.dependency_overrides[get_data_transformation_engine] = lambda: transformation_engine
    app.dependency_overrides[get_data_source_manager] = lambda: sources
    return TestClient(app)


def test_register_workflow_creates_definition(engine: ETLWorkflowEngine):
    workflow = engine.register_workflow(
        "orders-etl", "orders-db", [TransformationStep(operation=OperationType.SORT, config={"column": "amount"})], "warehouse.orders"
    )

    assert workflow.name == "orders-etl"
    assert workflow.load_target == "warehouse.orders"


def test_register_workflow_rejects_duplicate(engine: ETLWorkflowEngine):
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")

    with pytest.raises(WorkflowAlreadyExistsError):
        engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")


def test_register_workflow_rejects_unsupported_post_processing_action(engine: ETLWorkflowEngine):
    with pytest.raises(UnsupportedPostProcessingActionError):
        engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders", post_processing=("teleport",))


def test_get_workflow_unknown_raises(engine: ETLWorkflowEngine):
    with pytest.raises(UnknownWorkflowError):
        engine.get_workflow("does-not-exist")


def test_execute_runs_all_stages_in_order(engine: ETLWorkflowEngine, transformation_engine, sources):
    engine.register_workflow(
        "orders-etl",
        "orders-db",
        [TransformationStep(operation=OperationType.AGGREGATE, config={"group_by": ["region"], "column": "amount", "func": "sum"})],
        "warehouse.orders",
        post_processing=("checksum",),
    )

    result = engine.execute("orders-etl", ROWS, sources=sources, transformation_engine=transformation_engine)

    assert isinstance(result, ExecutionResult)
    assert result.status == "success"
    assert result.stages_completed == ("extract", "transform", "load", "post_processing")
    assert result.row_count == 2
    assert result.transform_result_id is not None


def test_execute_marks_source_read(engine: ETLWorkflowEngine, transformation_engine, sources):
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")

    engine.execute("orders-etl", ROWS, sources=sources, transformation_engine=transformation_engine)

    assert sources.get("orders-db").read_count == 1


def test_execute_links_to_transformation_history(engine: ETLWorkflowEngine, transformation_engine, sources):
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")

    result = engine.execute("orders-etl", ROWS, sources=sources, transformation_engine=transformation_engine)

    linked = transformation_engine.get_result(result.transform_result_id)
    assert linked.result_id == result.transform_result_id


def test_execute_unknown_workflow_raises(engine: ETLWorkflowEngine):
    with pytest.raises(UnknownWorkflowError):
        engine.execute("does-not-exist", ROWS)


def test_execute_fails_at_extract_stage_for_unknown_source(engine: ETLWorkflowEngine, transformation_engine):
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")
    empty_sources = DataSourceManager()  # "orders-db" was never registered here

    result = engine.execute("orders-etl", ROWS, sources=empty_sources, transformation_engine=transformation_engine)

    assert result.status == "failed"
    assert result.failed_stage == "extract"
    assert result.stages_completed == ()


def test_post_process_rejects_unsupported_action(engine: ETLWorkflowEngine):
    with pytest.raises(UnsupportedPostProcessingActionError):
        engine.post_process(("unsupported-action",))


def test_execute_records_execution_in_history(engine: ETLWorkflowEngine, transformation_engine, sources):
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")

    engine.execute("orders-etl", ROWS, sources=sources, transformation_engine=transformation_engine)

    history = engine.list_executions("orders-etl")
    assert len(history) == 1
    assert history[0].status == "success"


def test_load_raises_for_empty_target(engine: ETLWorkflowEngine):
    with pytest.raises(LoadTargetError):
        engine.load(ROWS, "")


def test_api_register_and_get_workflow(client: TestClient):
    response = client.post(
        "/pipelines/etl",
        json={"name": "orders-etl", "source_name": "orders-db", "transform_steps": [], "load_target": "warehouse.orders"},
    )
    assert response.status_code == 201

    fetched = client.get("/pipelines/etl/orders-etl")
    assert fetched.status_code == 200
    assert fetched.json()["load_target"] == "warehouse.orders"


def test_api_get_unknown_workflow_returns_404(client: TestClient):
    response = client.get("/pipelines/etl/does-not-exist")

    assert response.status_code == 404


def test_api_execute_runs_registered_workflow(client: TestClient):
    client.post(
        "/pipelines/etl",
        json={"name": "orders-etl", "source_name": "orders-db", "transform_steps": [], "load_target": "warehouse.orders"},
    )

    response = client.post("/pipelines/etl/execute", json={"name": "orders-etl", "rows": ROWS})

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["row_count"] == 3


def test_api_execute_unknown_workflow_returns_404(client: TestClient):
    response = client.post("/pipelines/etl/execute", json={"name": "does-not-exist", "rows": ROWS})

    assert response.status_code == 404
