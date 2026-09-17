from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_automation import DeploymentAutomationEngine
from backend.governance.deployment_orchestration_dashboard import (
    DeploymentOrchestrationDashboard,
    router as deployment_orchestration_dashboard_router,
)
from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    UnknownPipelineError,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_pipeline_recovery import DeploymentPipelineRecoveryManager
from backend.governance.deployment_scheduler import DeploymentScheduler
from backend.governance.deployment_workflow import (
    DeploymentWorkflowEngine,
    UnknownExecutionError,
    router as deployment_workflow_router,
)
from backend.governance.deployment_workflow_analytics import DeploymentWorkflowAnalyticsService

BASE_TIME = datetime(2026, 7, 26, 9, 0, 0, tzinfo=timezone.utc)


def _wired_services():
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register("svc-a", [PipelineStage(name="build", action="build")])
    workflow_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    scheduler = DeploymentScheduler(workflow_engine=workflow_engine)
    automation_engine = DeploymentAutomationEngine(scheduler=scheduler)
    recovery_manager = DeploymentPipelineRecoveryManager(workflow_engine=workflow_engine)
    analytics_service = DeploymentWorkflowAnalyticsService(workflow_engine=workflow_engine)
    return pipeline_engine, workflow_engine, scheduler, automation_engine, recovery_manager, analytics_service


@pytest.fixture
def dashboard() -> DeploymentOrchestrationDashboard:
    return DeploymentOrchestrationDashboard()


def test_refresh_computes_and_caches_overview(dashboard: DeploymentOrchestrationDashboard):
    pipeline_engine, workflow_engine, scheduler, automation_engine, _, analytics = _wired_services()

    snapshot = dashboard.refresh(
        pipeline_engine=pipeline_engine,
        workflow_engine=workflow_engine,
        scheduler=scheduler,
        automation_engine=automation_engine,
        analytics_service=analytics,
        timestamp=BASE_TIME,
    )

    assert snapshot["pipelines"]["total"] == 1
    assert snapshot["generated_at"] == BASE_TIME.isoformat()


def test_overview_returns_cached_snapshot_without_recompute(
    dashboard: DeploymentOrchestrationDashboard,
):
    pipeline_engine, workflow_engine, scheduler, automation_engine, _, analytics = _wired_services()
    dashboard.refresh(
        pipeline_engine=pipeline_engine,
        workflow_engine=workflow_engine,
        scheduler=scheduler,
        automation_engine=automation_engine,
        analytics_service=analytics,
        timestamp=BASE_TIME,
    )

    pipeline_engine.register("svc-b", [PipelineStage(name="build", action="build")])
    cached = dashboard.overview()

    assert cached["pipelines"]["total"] == 1


def test_overview_computes_fresh_when_refresh_true(dashboard: DeploymentOrchestrationDashboard):
    pipeline_engine, workflow_engine, scheduler, automation_engine, _, analytics = _wired_services()
    dashboard.refresh(
        pipeline_engine=pipeline_engine,
        workflow_engine=workflow_engine,
        scheduler=scheduler,
        automation_engine=automation_engine,
        analytics_service=analytics,
        timestamp=BASE_TIME,
    )
    pipeline_engine.register("svc-b", [PipelineStage(name="build", action="build")])

    refreshed = dashboard.overview(
        refresh=True,
        pipeline_engine=pipeline_engine,
        workflow_engine=workflow_engine,
        scheduler=scheduler,
        automation_engine=automation_engine,
        analytics_service=analytics,
    )

    assert refreshed["pipelines"]["total"] == 2


def test_refresh_aggregates_workflows_schedules_and_automation(
    dashboard: DeploymentOrchestrationDashboard,
):
    pipeline_engine, workflow_engine, scheduler, automation_engine, _, analytics = _wired_services()
    workflow_engine.start("svc-a", timestamp=BASE_TIME)
    scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)
    automation_engine.register_rule("rule-1", "svc-a", "manual", timestamp=BASE_TIME)

    snapshot = dashboard.refresh(
        pipeline_engine=pipeline_engine,
        workflow_engine=workflow_engine,
        scheduler=scheduler,
        automation_engine=automation_engine,
        analytics_service=analytics,
        timestamp=BASE_TIME,
    )

    assert snapshot["workflows"]["total"] == 1
    assert snapshot["workflows"]["by_status"] == {"RUNNING": 1}
    assert snapshot["schedules"]["pending"] == 1
    assert snapshot["automation"]["total_rules"] == 1
    assert snapshot["automation"]["enabled_rules"] == 1


def test_pipeline_returns_summary_for_named_pipeline(dashboard: DeploymentOrchestrationDashboard):
    pipeline_engine, workflow_engine, _, _, _, analytics = _wired_services()
    workflow_engine.start("svc-a", timestamp=BASE_TIME)

    summary = dashboard.pipeline(
        "svc-a", pipeline_engine=pipeline_engine, workflow_engine=workflow_engine, analytics_service=analytics
    )

    assert summary["pipeline"]["name"] == "svc-a"
    assert summary["active_executions"] == 1
    assert summary["total_executions"] == 1


def test_pipeline_unknown_name_raises(dashboard: DeploymentOrchestrationDashboard):
    pipeline_engine, workflow_engine, _, _, _, analytics = _wired_services()

    with pytest.raises(UnknownPipelineError):
        dashboard.pipeline("does-not-exist", pipeline_engine=pipeline_engine)


def test_pipeline_without_name_returns_all_summaries(dashboard: DeploymentOrchestrationDashboard):
    pipeline_engine, workflow_engine, _, _, _, analytics = _wired_services()
    pipeline_engine.register("svc-b", [PipelineStage(name="build", action="build")])

    summaries = dashboard.pipeline(pipeline_engine=pipeline_engine, workflow_engine=workflow_engine)

    assert {item["pipeline"]["name"] for item in summaries} == {"svc-a", "svc-b"}


def test_pipeline_requires_pipeline_engine(dashboard: DeploymentOrchestrationDashboard):
    with pytest.raises(ValueError):
        dashboard.pipeline("svc-a")


def test_workflow_returns_execution_with_recovery_and_analytics(
    dashboard: DeploymentOrchestrationDashboard,
):
    pipeline_engine, workflow_engine, _, _, recovery_manager, analytics = _wired_services()
    execution = workflow_engine.start("svc-a", timestamp=BASE_TIME)
    workflow_engine.fail(execution.execution_id, timestamp=BASE_TIME)
    recovery_manager.resume(execution.execution_id, workflow_engine=workflow_engine, timestamp=BASE_TIME)
    analytics.record(execution.execution_id, workflow_engine=workflow_engine, timestamp=BASE_TIME)

    result = dashboard.workflow(
        execution.execution_id,
        workflow_engine=workflow_engine,
        pipeline_recovery_manager=recovery_manager,
        analytics_service=analytics,
    )

    assert result["execution"]["execution_id"] == execution.execution_id
    assert len(result["recovery_history"]) == 1
    assert result["analytics"]["total_executions"] == 1


def test_workflow_unknown_execution_raises(dashboard: DeploymentOrchestrationDashboard):
    _, workflow_engine, _, _, _, _ = _wired_services()

    with pytest.raises(UnknownExecutionError):
        dashboard.workflow("does-not-exist", workflow_engine=workflow_engine)


def test_workflow_requires_workflow_engine(dashboard: DeploymentOrchestrationDashboard):
    with pytest.raises(ValueError):
        dashboard.workflow("exec-1")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_workflow_router)
    app.include_router(deployment_orchestration_dashboard_router)
    return TestClient(app)


def test_api_overview(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={"name": "svc-orch-api-1", "stages": [{"name": "build", "action": "build"}]},
    )

    response = client.get("/governance/orchestration")

    assert response.status_code == 200
    assert response.json()["pipelines"]["total"] >= 1


def test_api_pipelines_list(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={"name": "svc-orch-api-2", "stages": [{"name": "build", "action": "build"}]},
    )

    response = client.get("/governance/orchestration/pipelines")

    assert response.status_code == 200
    assert any(item["pipeline"]["name"] == "svc-orch-api-2" for item in response.json())


def test_api_workflow_lookup(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={"name": "svc-orch-api-3", "stages": [{"name": "build", "action": "build"}]},
    )
    start_response = client.post(
        "/governance/workflows/start", json={"pipeline": "svc-orch-api-3"}
    )
    execution_id = start_response.json()["execution_id"]

    response = client.get(f"/governance/orchestration/workflows/{execution_id}")

    assert response.status_code == 200
    assert response.json()["execution"]["execution_id"] == execution_id


def test_api_workflow_unknown_returns_404(client: TestClient):
    response = client.get("/governance/orchestration/workflows/does-not-exist")

    assert response.status_code == 404
