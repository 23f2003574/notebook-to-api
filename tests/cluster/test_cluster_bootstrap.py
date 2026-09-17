import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.bootstrap import (
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    ClusterBootstrapError,
    ClusterNotInitializedError,
    DistributedExecutionBootstrap,
    UnknownServiceError,
    bootstrap_cluster_subsystem,
    get_cluster_bootstrap,
)
from backend.cluster.worker_registry import WorkerMetadata


def make_metadata(hostname: str = "node-a.local") -> WorkerMetadata:
    return WorkerMetadata(hostname=hostname, region="us-east-1", version="1.0.0")


def test_register_services_wires_every_required_service():
    bootstrap = DistributedExecutionBootstrap()

    services = bootstrap.register_services()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = DistributedExecutionBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register_services()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = DistributedExecutionBootstrap()
    bootstrap.register_services()

    scheduler = bootstrap.discover("scheduler")

    assert scheduler is bootstrap.registered_services()["scheduler"]


def test_discover_unknown_service_raises():
    bootstrap = DistributedExecutionBootstrap()
    bootstrap.register_services()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_wire_components_probes_registered_workers():
    bootstrap = DistributedExecutionBootstrap()
    services = bootstrap.register_services()
    worker_id = f"e2e-worker-{uuid.uuid4().hex}"
    services["worker_registry"].register(worker_id, ["parse"], make_metadata())

    restored = bootstrap.wire_components()

    assert worker_id in restored
    assert services["worker_health"].get_report(worker_id) is not None


def test_wire_components_is_idempotent():
    bootstrap = DistributedExecutionBootstrap()
    bootstrap.register_services()

    first = bootstrap.wire_components()
    second = bootstrap.wire_components()

    assert first == second


def test_initialize_returns_valid_result():
    bootstrap = DistributedExecutionBootstrap()

    result = bootstrap.initialize()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()
    assert bootstrap.is_initialized is True


def test_initialize_raises_when_a_required_service_is_missing(monkeypatch):
    bootstrap = DistributedExecutionBootstrap()
    incomplete = {name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"}
    monkeypatch.setattr(bootstrap, "register_services", lambda: incomplete)
    monkeypatch.setattr(bootstrap, "wire_components", lambda: ())

    with pytest.raises(ClusterBootstrapError) as exc_info:
        bootstrap.initialize()

    assert exc_info.value.result.missing_services == ("dashboard_api",)
    assert exc_info.value.result.valid is False
    assert bootstrap.is_initialized is False


def test_health_check_before_initialize_raises():
    bootstrap = DistributedExecutionBootstrap()

    with pytest.raises(ClusterNotInitializedError):
        bootstrap.health_check()


def test_health_check_delegates_to_the_dashboard():
    bootstrap = DistributedExecutionBootstrap()
    bootstrap.initialize()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "workers" in report
    assert "executions" in report


def test_shutdown_before_initialize_raises():
    bootstrap = DistributedExecutionBootstrap()

    with pytest.raises(ClusterNotInitializedError):
        bootstrap.shutdown()


def test_shutdown_resets_state():
    bootstrap = DistributedExecutionBootstrap()
    bootstrap.initialize()

    bootstrap.shutdown()

    assert bootstrap.is_initialized is False


def test_bootstrap_cluster_subsystem_is_valid():
    result = bootstrap_cluster_subsystem()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)


def test_bootstrap_cluster_subsystem_is_idempotent():
    first = bootstrap_cluster_subsystem()
    second = bootstrap_cluster_subsystem()

    assert first.valid is True
    assert second.valid is True


def test_get_cluster_bootstrap_returns_singleton():
    assert get_cluster_bootstrap() is get_cluster_bootstrap()


def test_subsystem_name_is_stable():
    assert SUBSYSTEM_NAME == "distributed_execution_and_compute_orchestration"


def test_end_to_end_distributed_execution_workflow():
    result = bootstrap_cluster_subsystem()
    assert result.valid is True

    services = get_cluster_bootstrap().registered_services()
    registry = services["worker_registry"]
    discovery = services["worker_discovery"]
    health = services["worker_health"]
    coordinator = services["execution_coordinator"]
    scheduler = services["scheduler"]
    fault_tolerance = services["fault_tolerance"]
    analytics = services["analytics_service"]
    dashboard_api = services["dashboard_api"]
    export_service = services["export_service"]

    suffix = uuid.uuid4().hex
    worker_id = f"e2e-worker-{suffix}"
    capability = f"e2e-capability-{suffix}"
    job_id = f"e2e-job-{suffix}"

    registry.register(worker_id, [capability], make_metadata(hostname=f"node-{suffix}"))
    health.check(worker_id, metrics={"cpu_percent": 5.0})
    assert discovery.get_health(worker_id) == "healthy"

    plan = scheduler.schedule(job_id, capability)
    assert plan.decision.worker_id == worker_id

    session = coordinator.submit(job_id, capability)
    assert session.worker_id == worker_id
    assert session.state == "assigned"

    monitored = coordinator.monitor(job_id)
    assert monitored.state == "running"

    completed = coordinator.complete(job_id, result={"rows": 3})
    assert completed.state == "completed"

    records = analytics.list_records(capability)
    assert len(records) == 1
    assert records[0].completed_count == 1

    overview = dashboard_api.overview()
    assert overview["workers"]["total"] >= 1

    fail_job_id = f"e2e-job-fail-{suffix}"
    coordinator.submit(fail_job_id, capability)
    coordinator.complete(fail_job_id, success=False, error="simulated failure")
    recovery_plan = fault_tolerance.retry(fail_job_id)
    assert recovery_plan.strategy == "retry"

    export = export_service.export_workers(format="json")
    assert export.manifest.export_type == "workers"


@pytest.fixture
def client() -> TestClient:
    from backend.cluster.worker_registry import router as worker_registry_router
    from backend.cluster.worker_discovery import router as worker_discovery_router
    from backend.cluster.job_dispatcher import router as job_dispatcher_router
    from backend.cluster.task_serializer import router as task_serializer_router
    from backend.cluster.execution_coordinator import router as execution_coordinator_router
    from backend.cluster.worker_health import router as worker_health_router
    from backend.cluster.distributed_scheduler import router as distributed_scheduler_router
    from backend.cluster.auto_scaling import router as auto_scaling_router
    from backend.cluster.fault_tolerance import router as fault_tolerance_router
    from backend.cluster.cluster_analytics import router as cluster_analytics_router
    from backend.cluster.dashboard import router as dashboard_router
    from backend.cluster.export_service import router as export_service_router

    app = FastAPI()
    for router in (
        worker_registry_router,
        worker_discovery_router,
        job_dispatcher_router,
        task_serializer_router,
        execution_coordinator_router,
        worker_health_router,
        distributed_scheduler_router,
        auto_scaling_router,
        fault_tolerance_router,
        cluster_analytics_router,
        dashboard_router,
        export_service_router,
    ):
        app.include_router(router)
    return TestClient(app)


def test_route_registration_covers_every_service_area(client: TestClient):
    bootstrap_cluster_subsystem()

    assert client.get("/cluster/workers").status_code == 200
    assert client.get("/cluster/discovery").status_code == 200
    assert client.get("/cluster/dispatch/queue").status_code == 200
    assert client.get("/cluster/tasks/formats").status_code == 200
    assert client.get("/cluster/executions").status_code == 200
    assert client.get("/cluster/health").status_code == 200
    assert client.get("/cluster/scheduler/stats").status_code == 200
    assert client.get("/cluster/scaling/history").status_code == 200
    assert client.get("/cluster/recovery/events").status_code == 200
    assert client.get("/cluster/analytics").status_code == 200
    assert client.get("/cluster/dashboard").status_code == 200
    assert client.get("/cluster/export/all").status_code == 200
