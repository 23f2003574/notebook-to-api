from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.checkpoint_manager import (
    Checkpoint,
    CheckpointRecoveryManager,
    CheckpointType,
    RecoveryState,
    UnknownCheckpointError,
    get_checkpoint_recovery_manager,
    router as checkpoint_manager_router,
)
from backend.pipeline.data_sources import ConnectionProfile, DataSourceManager, SourceType, get_data_source_manager
from backend.pipeline.etl_engine import ETLWorkflowEngine, get_etl_workflow_engine
from backend.pipeline.pipeline_executor import ExecutionState, PipelineExecutionEngine, UnknownRunError, get_pipeline_execution_engine
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
def executor() -> PipelineExecutionEngine:
    return PipelineExecutionEngine()


@pytest.fixture
def manager() -> CheckpointRecoveryManager:
    return CheckpointRecoveryManager()


@pytest.fixture
def client(manager, executor, workflows, sources, transformation_engine) -> TestClient:
    app = FastAPI()
    app.include_router(checkpoint_manager_router)
    app.dependency_overrides[get_checkpoint_recovery_manager] = lambda: manager
    app.dependency_overrides[get_pipeline_execution_engine] = lambda: executor
    app.dependency_overrides[get_etl_workflow_engine] = lambda: workflows
    app.dependency_overrides[get_data_source_manager] = lambda: sources
    app.dependency_overrides[get_data_transformation_engine] = lambda: transformation_engine
    return TestClient(app)


def test_create_checkpoint_stores_snapshot(manager: CheckpointRecoveryManager):
    checkpoint = manager.create_checkpoint("run-1", "orders-etl", ROWS, "transform", CheckpointType.AUTOMATIC)

    assert isinstance(checkpoint, Checkpoint)
    assert checkpoint.run_id == "run-1"
    assert len(checkpoint.rows) == 2


def test_create_checkpoint_raises_for_unknown_run_with_executor(manager: CheckpointRecoveryManager, executor: PipelineExecutionEngine):
    with pytest.raises(UnknownRunError):
        manager.create_checkpoint("does-not-exist", "orders-etl", ROWS, "extract", executor=executor)


def test_create_checkpoint_succeeds_for_known_run(manager: CheckpointRecoveryManager, executor: PipelineExecutionEngine):
    run = executor.submit("orders-etl", ROWS)

    checkpoint = manager.create_checkpoint(run.run_id, "orders-etl", ROWS, "extract", executor=executor)

    assert checkpoint.run_id == run.run_id


def test_create_checkpoint_rejects_missing_run_id(manager: CheckpointRecoveryManager):
    with pytest.raises(ValueError):
        manager.create_checkpoint("", "orders-etl", ROWS, "extract")


def test_get_unknown_checkpoint_raises(manager: CheckpointRecoveryManager):
    with pytest.raises(UnknownCheckpointError):
        manager.get("does-not-exist")


def test_list_checkpoints_filters_by_run(manager: CheckpointRecoveryManager):
    manager.create_checkpoint("run-1", "orders-etl", ROWS, "extract")
    manager.create_checkpoint("run-2", "orders-etl", ROWS, "extract")

    listed = manager.list_checkpoints(run_id="run-1")

    assert len(listed) == 1
    assert listed[0].run_id == "run-1"


def test_delete_checkpoint_removes_it(manager: CheckpointRecoveryManager):
    checkpoint = manager.create_checkpoint("run-1", "orders-etl", ROWS, "extract")

    manager.delete_checkpoint(checkpoint.checkpoint_id)

    with pytest.raises(UnknownCheckpointError):
        manager.get(checkpoint.checkpoint_id)


def test_delete_unknown_checkpoint_raises(manager: CheckpointRecoveryManager):
    with pytest.raises(UnknownCheckpointError):
        manager.delete_checkpoint("does-not-exist")


def test_restore_creates_recovery_state_without_new_run(manager: CheckpointRecoveryManager):
    checkpoint = manager.create_checkpoint("run-1", "orders-etl", ROWS, "transform")

    recovery = manager.restore(checkpoint.checkpoint_id)

    assert isinstance(recovery, RecoveryState)
    assert recovery.status == "restored"
    assert recovery.new_run_id is None


def test_restore_unknown_checkpoint_raises(manager: CheckpointRecoveryManager):
    with pytest.raises(UnknownCheckpointError):
        manager.restore("does-not-exist")


def test_resume_executes_new_run_from_snapshot(
    manager: CheckpointRecoveryManager, executor: PipelineExecutionEngine, workflows, sources, transformation_engine
):
    checkpoint = manager.create_checkpoint("run-1", "orders-etl", ROWS, "extract")

    run = manager.resume(checkpoint.checkpoint_id, executor, workflows, sources=sources, transformation_engine=transformation_engine)

    assert run.state == ExecutionState.SUCCEEDED
    assert run.resumed_from_checkpoint == checkpoint.checkpoint_id


def test_resume_records_recovery_state(
    manager: CheckpointRecoveryManager, executor: PipelineExecutionEngine, workflows, sources, transformation_engine
):
    checkpoint = manager.create_checkpoint("run-1", "orders-etl", ROWS, "extract")

    manager.resume(checkpoint.checkpoint_id, executor, workflows, sources=sources, transformation_engine=transformation_engine)

    recoveries = manager.list_recoveries(checkpoint_id=checkpoint.checkpoint_id)
    assert len(recoveries) == 1
    assert recoveries[0].status == "recovered"


def test_resume_unknown_checkpoint_raises(manager: CheckpointRecoveryManager, executor: PipelineExecutionEngine, workflows):
    with pytest.raises(UnknownCheckpointError):
        manager.resume("does-not-exist", executor, workflows)


def test_cleanup_removes_checkpoints_older_than_cutoff(manager: CheckpointRecoveryManager):
    checkpoint = manager.create_checkpoint("run-1", "orders-etl", ROWS, "extract")
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)

    removed = manager.cleanup(older_than=cutoff)

    assert removed == 1
    with pytest.raises(UnknownCheckpointError):
        manager.get(checkpoint.checkpoint_id)


def test_cleanup_retains_only_n_most_recent_per_run(manager: CheckpointRecoveryManager):
    first = manager.create_checkpoint("run-1", "orders-etl", ROWS, "extract")
    second = manager.create_checkpoint("run-1", "orders-etl", ROWS, "transform")
    third = manager.create_checkpoint("run-1", "orders-etl", ROWS, "load")

    removed = manager.cleanup(retention_per_run=2)

    assert removed == 1
    remaining_ids = {c.checkpoint_id for c in manager.list_checkpoints(run_id="run-1")}
    assert remaining_ids == {second.checkpoint_id, third.checkpoint_id}


def test_cleanup_with_no_filters_removes_nothing(manager: CheckpointRecoveryManager):
    manager.create_checkpoint("run-1", "orders-etl", ROWS, "extract")

    removed = manager.cleanup()

    assert removed == 0
    assert len(manager.list_checkpoints()) == 1


def test_api_create_checkpoint(client: TestClient, executor: PipelineExecutionEngine):
    run = executor.submit("orders-etl", ROWS)

    response = client.post(
        "/pipelines/checkpoints",
        json={"run_id": run.run_id, "workflow_name": "orders-etl", "rows": ROWS, "stage": "extract", "checkpoint_type": "manual"},
    )

    assert response.status_code == 201
    assert response.json()["checkpoint_type"] == "manual"


def test_api_create_checkpoint_unknown_run_returns_404(client: TestClient):
    response = client.post(
        "/pipelines/checkpoints",
        json={"run_id": "does-not-exist", "workflow_name": "orders-etl", "rows": ROWS, "stage": "extract"},
    )

    assert response.status_code == 404


def test_api_list_checkpoints(client: TestClient, executor: PipelineExecutionEngine):
    run = executor.submit("orders-etl", ROWS)
    client.post(
        "/pipelines/checkpoints",
        json={"run_id": run.run_id, "workflow_name": "orders-etl", "rows": ROWS, "stage": "extract"},
    )

    response = client.get("/pipelines/checkpoints")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_restore_endpoint_executes_resume(client: TestClient, executor: PipelineExecutionEngine):
    run = executor.submit("orders-etl", ROWS)
    created = client.post(
        "/pipelines/checkpoints",
        json={"run_id": run.run_id, "workflow_name": "orders-etl", "rows": ROWS, "stage": "extract"},
    )
    checkpoint_id = created.json()["checkpoint_id"]

    response = client.post(f"/pipelines/checkpoints/{checkpoint_id}/restore")

    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"
    assert response.json()["resumed_from_checkpoint"] == checkpoint_id


def test_api_restore_unknown_checkpoint_returns_404(client: TestClient):
    response = client.post("/pipelines/checkpoints/does-not-exist/restore")

    assert response.status_code == 404


def test_api_delete_checkpoint(client: TestClient, executor: PipelineExecutionEngine):
    run = executor.submit("orders-etl", ROWS)
    created = client.post(
        "/pipelines/checkpoints",
        json={"run_id": run.run_id, "workflow_name": "orders-etl", "rows": ROWS, "stage": "extract"},
    )
    checkpoint_id = created.json()["checkpoint_id"]

    response = client.delete(f"/pipelines/checkpoints/{checkpoint_id}")

    assert response.status_code == 204
    assert client.get("/pipelines/checkpoints").json() == []


def test_api_delete_unknown_checkpoint_returns_404(client: TestClient):
    response = client.delete("/pipelines/checkpoints/does-not-exist")

    assert response.status_code == 404
