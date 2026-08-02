from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType, get_data_source_manager
from backend.pipeline.etl_engine import ETLWorkflowEngine, get_etl_workflow_engine
from backend.pipeline.pipeline_executor import (
    ExecutionState,
    InvalidStateTransitionError,
    PipelineExecutionEngine,
    PipelineRun,
    UnknownRunError,
    get_pipeline_execution_engine,
    router as pipeline_executor_router,
)
from backend.pipeline.pipeline_scheduler import PipelineScheduler, ScheduleTrigger, TriggerType
from backend.pipeline.transformation_engine import DataTransformationEngine, get_data_transformation_engine

ROWS = [{"region": "east", "amount": 10}, {"region": "west", "amount": 5}]


@pytest.fixture
def sources() -> DataSourceManager:
    manager = DataSourceManager()
    manager.register("orders-db", ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/orders"))
    return manager


@pytest.fixture
def workflows(sources: DataSourceManager) -> ETLWorkflowEngine:
    engine = ETLWorkflowEngine()
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")
    return engine


@pytest.fixture
def transformation_engine() -> DataTransformationEngine:
    return DataTransformationEngine()


@pytest.fixture
def engine() -> PipelineExecutionEngine:
    return PipelineExecutionEngine()


@pytest.fixture
def client(engine, workflows, sources, transformation_engine) -> TestClient:
    app = FastAPI()
    app.include_router(pipeline_executor_router)
    app.dependency_overrides[get_pipeline_execution_engine] = lambda: engine
    app.dependency_overrides[get_etl_workflow_engine] = lambda: workflows
    app.dependency_overrides[get_data_source_manager] = lambda: sources
    app.dependency_overrides[get_data_transformation_engine] = lambda: transformation_engine
    return TestClient(app)


def test_submit_creates_queued_run(engine: PipelineExecutionEngine):
    run = engine.submit("orders-etl", ROWS)

    assert isinstance(run, PipelineRun)
    assert run.state == ExecutionState.QUEUED
    assert run.progress == 0.0


def test_submit_rejects_empty_workflow_name(engine: PipelineExecutionEngine):
    with pytest.raises(ValueError):
        engine.submit("", ROWS)


def test_execute_transitions_queued_to_succeeded(engine: PipelineExecutionEngine, workflows, sources, transformation_engine):
    run = engine.submit("orders-etl", ROWS)

    finished = engine.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=transformation_engine)

    assert finished.state == ExecutionState.SUCCEEDED
    assert finished.progress == 1.0
    assert finished.execution_id is not None
    assert finished.started_at is not None
    assert finished.finished_at is not None


def test_execute_unknown_workflow_marks_run_failed(engine: PipelineExecutionEngine, workflows, sources, transformation_engine):
    run = engine.submit("does-not-exist", ROWS)

    finished = engine.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=transformation_engine)

    assert finished.state == ExecutionState.FAILED
    assert finished.error is not None


def test_execute_unknown_run_raises(engine: PipelineExecutionEngine, workflows):
    with pytest.raises(UnknownRunError):
        engine.execute("does-not-exist", workflows=workflows)


def test_execute_twice_raises_invalid_transition(engine: PipelineExecutionEngine, workflows, sources, transformation_engine):
    run = engine.submit("orders-etl", ROWS)
    engine.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=transformation_engine)

    with pytest.raises(InvalidStateTransitionError):
        engine.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=transformation_engine)


def test_execute_reports_partial_progress_on_failure(engine: PipelineExecutionEngine, workflows, transformation_engine):
    run = engine.submit("orders-etl", ROWS)
    empty_sources = DataSourceManager()  # "orders-db" was never registered here

    finished = engine.execute(run.run_id, workflows=workflows, sources=empty_sources, transformation_engine=transformation_engine)

    assert finished.state == ExecutionState.FAILED
    assert finished.progress == 0.0


def test_cancel_transitions_queued_to_cancelled(engine: PipelineExecutionEngine):
    run = engine.submit("orders-etl", ROWS)

    cancelled = engine.cancel(run.run_id)

    assert cancelled.state == ExecutionState.CANCELLED
    assert cancelled.finished_at is not None


def test_cancel_unknown_run_raises(engine: PipelineExecutionEngine):
    with pytest.raises(UnknownRunError):
        engine.cancel("does-not-exist")


def test_cancel_completed_run_raises(engine: PipelineExecutionEngine, workflows, sources, transformation_engine):
    run = engine.submit("orders-etl", ROWS)
    engine.execute(run.run_id, workflows=workflows, sources=sources, transformation_engine=transformation_engine)

    with pytest.raises(InvalidStateTransitionError):
        engine.cancel(run.run_id)


def test_status_unknown_run_raises(engine: PipelineExecutionEngine):
    with pytest.raises(UnknownRunError):
        engine.status("does-not-exist")


def test_list_runs_sorted_most_recent_first(engine: PipelineExecutionEngine):
    first = engine.submit("orders-etl", ROWS)
    second = engine.submit("orders-etl", ROWS)

    runs = engine.list_runs()

    assert [run.run_id for run in runs] == [second.run_id, first.run_id]


def test_list_runs_filters_by_workflow_name(engine: PipelineExecutionEngine):
    engine.submit("orders-etl", ROWS)
    engine.submit("other-etl", ROWS)

    runs = engine.list_runs(workflow_name="other-etl")

    assert len(runs) == 1
    assert runs[0].workflow_name == "other-etl"


def test_dispatch_due_runs_scheduled_workflow_and_advances_schedule(
    engine: PipelineExecutionEngine, workflows, sources, transformation_engine
):
    scheduler = PipelineScheduler()
    schedule = scheduler.schedule(
        "orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60), workflows=workflows
    )
    due_at = schedule.next_run_at

    results = engine.dispatch_due(
        scheduler,
        workflows,
        now=due_at,
        sources=sources,
        transformation_engine=transformation_engine,
    )

    assert len(results) == 1
    assert results[0].state == ExecutionState.SUCCEEDED
    assert results[0].schedule_id == schedule.schedule_id

    advanced = scheduler.get(schedule.schedule_id)
    assert advanced.next_run_at > due_at


def test_dispatch_due_ignores_schedules_not_yet_due(engine: PipelineExecutionEngine, workflows, sources, transformation_engine):
    scheduler = PipelineScheduler()
    scheduler.schedule(
        "orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=3600), workflows=workflows
    )

    results = engine.dispatch_due(
        scheduler, workflows, now=datetime.now(timezone.utc), sources=sources, transformation_engine=transformation_engine
    )

    assert results == []


def test_api_execute_returns_terminal_run(client: TestClient):
    response = client.post("/pipelines/execute", json={"workflow_name": "orders-etl", "rows": ROWS})

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "succeeded"
    assert body["progress"] == 1.0


def test_api_execute_missing_workflow_name_returns_422(client: TestClient):
    response = client.post("/pipelines/execute", json={"rows": ROWS})

    assert response.status_code == 422


def test_api_list_runs(client: TestClient):
    client.post("/pipelines/execute", json={"workflow_name": "orders-etl", "rows": ROWS})

    response = client.get("/pipelines/runs")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_run_by_id(client: TestClient):
    created = client.post("/pipelines/execute", json={"workflow_name": "orders-etl", "rows": ROWS})
    run_id = created.json()["run_id"]

    response = client.get(f"/pipelines/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id


def test_api_get_unknown_run_returns_404(client: TestClient):
    response = client.get("/pipelines/runs/does-not-exist")

    assert response.status_code == 404


def test_api_delete_completed_run_returns_409(client: TestClient):
    created = client.post("/pipelines/execute", json={"workflow_name": "orders-etl", "rows": ROWS})
    run_id = created.json()["run_id"]

    response = client.delete(f"/pipelines/runs/{run_id}")

    assert response.status_code == 409


def test_api_delete_unknown_run_returns_404(client: TestClient):
    response = client.delete("/pipelines/runs/does-not-exist")

    assert response.status_code == 404
