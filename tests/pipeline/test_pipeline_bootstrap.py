import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.bootstrap import (
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    PipelineBootstrap,
    PipelineBootstrapError,
    PipelineNotInitializedError,
    UnknownServiceError,
    bootstrap_pipeline_subsystem,
    get_pipeline_bootstrap,
)
from backend.pipeline.data_sources import ConnectionProfile, SourceType
from backend.pipeline.pipeline_executor import ExecutionState
from backend.pipeline.pipeline_scheduler import ScheduleTrigger, TriggerType
from backend.pipeline.transformation_engine import OperationType, TransformationStep


def test_register_services_wires_every_required_service():
    bootstrap = PipelineBootstrap()

    services = bootstrap.register_services()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = PipelineBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register_services()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = PipelineBootstrap()
    bootstrap.register_services()

    scheduler = bootstrap.discover("scheduler")

    assert scheduler is bootstrap.registered_services()["scheduler"]


def test_discover_unknown_service_raises():
    bootstrap = PipelineBootstrap()
    bootstrap.register_services()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_wire_components_restores_schedules_bound_to_existing_workflows():
    bootstrap = PipelineBootstrap()
    services = bootstrap.register_services()
    workflow_name = f"e2e-workflow-{uuid.uuid4().hex}"
    services["etl_engine"].register_workflow(workflow_name, "e2e-source", [], "warehouse.target")
    schedule = services["scheduler"].schedule(
        workflow_name, ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60), workflows=services["etl_engine"]
    )

    restored = bootstrap.wire_components()

    assert schedule.schedule_id in restored


def test_wire_components_cancels_schedules_for_missing_workflows():
    bootstrap = PipelineBootstrap()
    services = bootstrap.register_services()
    # Schedule without passing `workflows=`, bypassing the scheduler's own existence check.
    orphan = services["scheduler"].schedule(
        f"ghost-workflow-{uuid.uuid4().hex}", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60)
    )

    restored = bootstrap.wire_components()

    assert orphan.schedule_id not in restored
    assert services["scheduler"].get(orphan.schedule_id).status == "cancelled"


def test_wire_components_is_idempotent():
    bootstrap = PipelineBootstrap()
    bootstrap.register_services()

    first = bootstrap.wire_components()
    second = bootstrap.wire_components()

    assert first == second


def test_initialize_returns_valid_result():
    bootstrap = PipelineBootstrap()

    result = bootstrap.initialize()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()
    assert bootstrap.is_initialized is True


def test_initialize_raises_when_a_required_service_is_missing(monkeypatch):
    bootstrap = PipelineBootstrap()
    incomplete = {name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"}
    monkeypatch.setattr(bootstrap, "register_services", lambda: incomplete)
    monkeypatch.setattr(bootstrap, "wire_components", lambda: ())
    monkeypatch.setattr(bootstrap, "_load_schemas", lambda: 0)

    with pytest.raises(PipelineBootstrapError) as exc_info:
        bootstrap.initialize()

    assert exc_info.value.result.missing_services == ("dashboard_api",)
    assert exc_info.value.result.valid is False
    assert bootstrap.is_initialized is False


def test_health_check_before_initialize_raises():
    bootstrap = PipelineBootstrap()

    with pytest.raises(PipelineNotInitializedError):
        bootstrap.health_check()


def test_health_check_delegates_to_the_dashboard():
    bootstrap = PipelineBootstrap()
    bootstrap.initialize()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "executions" in report
    assert "schedules" in report


def test_shutdown_before_initialize_raises():
    bootstrap = PipelineBootstrap()

    with pytest.raises(PipelineNotInitializedError):
        bootstrap.shutdown()


def test_shutdown_cancels_queued_runs_and_resets_state():
    bootstrap = PipelineBootstrap()
    services = bootstrap.initialize()
    executor = bootstrap.registered_services()["execution_engine"]
    workflow_name = f"shutdown-workflow-{uuid.uuid4().hex}"
    bootstrap.registered_services()["etl_engine"].register_workflow(workflow_name, "shutdown-source", [], "warehouse.target")
    queued_run = executor.submit(workflow_name, [])

    bootstrap.shutdown()

    assert bootstrap.is_initialized is False
    assert executor.status(queued_run.run_id).state == ExecutionState.CANCELLED


def test_bootstrap_pipeline_subsystem_is_valid():
    result = bootstrap_pipeline_subsystem()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)


def test_bootstrap_pipeline_subsystem_is_idempotent():
    first = bootstrap_pipeline_subsystem()
    second = bootstrap_pipeline_subsystem()

    assert first.valid is True
    assert second.valid is True


def test_get_pipeline_bootstrap_returns_singleton():
    assert get_pipeline_bootstrap() is get_pipeline_bootstrap()


def test_subsystem_name_is_stable():
    assert SUBSYSTEM_NAME == "data_pipeline_and_etl_framework"


def test_end_to_end_etl_execution():
    result = bootstrap_pipeline_subsystem()
    assert result.valid is True

    services = get_pipeline_bootstrap().registered_services()
    sources = services["data_source_manager"]
    workflows = services["etl_engine"]
    executor = services["execution_engine"]
    transformation_engine = services["transformation_engine"]
    validation_engine = services["validation_engine"]
    schema_registry = services["schema_registry"]
    scheduler = services["scheduler"]
    checkpoints = services["checkpoint_manager"]
    analytics = services["analytics_service"]
    dashboard_api = services["dashboard_api"]
    export_service = services["export_service"]

    suffix = uuid.uuid4().hex
    source_name = f"e2e-source-{suffix}"
    workflow_name = f"e2e-workflow-{suffix}"

    sources.register(source_name, ConnectionProfile(source_type=SourceType.SQL, uri="postgres://db/e2e"))
    workflows.register_workflow(
        workflow_name,
        source_name,
        [TransformationStep(operation=OperationType.SORT, config={"column": "amount"})],
        "warehouse.e2e",
    )
    schema_registry.register(f"e2e-schema-{suffix}", [{"name": "amount", "type": "int", "nullable": False}])
    schedule = scheduler.schedule(
        workflow_name, ScheduleTrigger(trigger_type=TriggerType.MANUAL), workflows=workflows
    )

    rows = [{"amount": 20}, {"amount": 10}]
    run = executor.submit(workflow_name, rows)
    finished = executor.execute(
        run.run_id,
        workflows=workflows,
        sources=sources,
        transformation_engine=transformation_engine,
        analytics=analytics,
    )
    assert finished.state == ExecutionState.SUCCEEDED

    report = validation_engine.validate(rows, [])
    assert report.passed is True

    checkpoint = checkpoints.create_checkpoint(run.run_id, workflow_name, rows, "extract", executor=executor)
    resumed = checkpoints.resume(checkpoint.checkpoint_id, executor, workflows, sources=sources, transformation_engine=transformation_engine)
    assert resumed.state == ExecutionState.SUCCEEDED

    overview = dashboard_api.overview()
    assert overview["executions"]["total_runs"] >= 2

    manifest = export_service.export_all()
    assert manifest.sections == ("definitions", "runs", "schemas", "analytics")

    scheduler.cancel(schedule.schedule_id)


@pytest.fixture
def client() -> TestClient:
    from backend.pipeline.data_sources import router as data_sources_router
    from backend.pipeline.pipeline_scheduler import router as scheduler_router
    from backend.pipeline.pipeline_executor import router as executor_router
    from backend.pipeline.checkpoint_manager import router as checkpoint_router
    from backend.pipeline.pipeline_analytics import router as analytics_router
    from backend.pipeline.dashboard import router as dashboard_router, export_router
    from backend.pipeline.schema_registry import router as schema_router
    from backend.pipeline.pipeline_registry import router as registry_router

    app = FastAPI()
    for router in (
        data_sources_router,
        scheduler_router,
        executor_router,
        checkpoint_router,
        analytics_router,
        dashboard_router,
        export_router,
        schema_router,
        registry_router,
    ):
        app.include_router(router)
    return TestClient(app)


def test_route_registration_covers_every_service_area(client: TestClient):
    bootstrap_pipeline_subsystem()

    assert client.get("/pipelines/dashboard").status_code == 200
    assert client.get("/pipelines/sources").status_code == 200
    assert client.get("/pipelines/schedules").status_code == 200
    assert client.get("/pipelines/runs").status_code == 200
    assert client.get("/pipelines/checkpoints").status_code == 200
    assert client.get("/pipelines/analytics").status_code == 200
    assert client.get("/pipelines/schemas").status_code == 200
    assert client.get("/pipelines/export/all").status_code == 200
