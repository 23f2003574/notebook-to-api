from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_pipeline import DeploymentPipelineEngine, PipelineStage
from backend.governance.deployment_workflow import DeploymentWorkflowEngine
from backend.governance.deployment_workflow_analytics import (
    DeploymentWorkflowAnalyticsService,
    WorkflowAnalytics,
    get_deployment_workflow_analytics_service,
    router as deployment_workflow_analytics_router,
)

BASE_TIME = datetime(2026, 7, 26, 9, 0, 0, tzinfo=timezone.utc)


def _execution_with_outcome(pipeline_name: str, status: str, *, start=BASE_TIME, seconds=60.0):
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register(pipeline_name, [PipelineStage(name="build", action="build")])
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    execution = wf_engine.start(pipeline_name, timestamp=start)
    end = start + timedelta(seconds=seconds)
    if status == "COMPLETED":
        wf_engine.complete(execution.execution_id, timestamp=end)
    elif status == "FAILED":
        wf_engine.fail(execution.execution_id, timestamp=end)
    elif status == "CANCELLED":
        wf_engine.cancel(execution.execution_id, timestamp=end)
    return wf_engine, execution.execution_id


@pytest.fixture
def service() -> DeploymentWorkflowAnalyticsService:
    return DeploymentWorkflowAnalyticsService()


def test_record_requires_workflow_engine(service: DeploymentWorkflowAnalyticsService):
    with pytest.raises(ValueError):
        service.record("exec-1")


def test_record_captures_execution_status_and_duration(service: DeploymentWorkflowAnalyticsService):
    wf_engine, execution_id = _execution_with_outcome("svc-a", "COMPLETED", seconds=90.0)

    service.record(execution_id, workflow_engine=wf_engine, timestamp=BASE_TIME)

    history = service.history("svc-a")
    assert len(history) == 1
    assert history[0]["status"] == "COMPLETED"
    assert history[0]["duration_seconds"] == 90.0


def test_record_returns_updated_summary(service: DeploymentWorkflowAnalyticsService):
    wf_engine, execution_id = _execution_with_outcome("svc-a", "COMPLETED")

    summary = service.record(execution_id, workflow_engine=wf_engine, timestamp=BASE_TIME)

    assert isinstance(summary, WorkflowAnalytics)
    assert summary.total_executions == 1
    assert summary.success_rate == 1.0


def test_summarize_unknown_pipeline_returns_zeroed_summary(
    service: DeploymentWorkflowAnalyticsService,
):
    summary = service.summarize("does-not-exist")

    assert summary.total_executions == 0
    assert summary.success_rate == 0.0
    assert summary.average_duration_seconds is None


def test_summarize_computes_success_and_failure_rates(service: DeploymentWorkflowAnalyticsService):
    wf_engine_1, exec_1 = _execution_with_outcome("svc-a", "COMPLETED")
    wf_engine_2, exec_2 = _execution_with_outcome("svc-a", "FAILED")
    service.record(exec_1, workflow_engine=wf_engine_1, timestamp=BASE_TIME)
    service.record(exec_2, workflow_engine=wf_engine_2, timestamp=BASE_TIME)

    summary = service.summarize("svc-a")

    assert summary.total_executions == 2
    assert summary.succeeded == 1
    assert summary.failed == 1
    assert summary.success_rate == 0.5
    assert summary.failure_rate == 0.5


def test_summarize_all_pipelines_without_filter(service: DeploymentWorkflowAnalyticsService):
    wf_engine_1, exec_1 = _execution_with_outcome("svc-a", "COMPLETED")
    wf_engine_2, exec_2 = _execution_with_outcome("svc-b", "COMPLETED")
    service.record(exec_1, workflow_engine=wf_engine_1, timestamp=BASE_TIME)
    service.record(exec_2, workflow_engine=wf_engine_2, timestamp=BASE_TIME)

    summaries = service.summarize()

    assert {summary.pipeline for summary in summaries} == {"svc-a", "svc-b"}


def test_summarize_computes_stage_durations_average(service: DeploymentWorkflowAnalyticsService):
    wf_engine_1, exec_1 = _execution_with_outcome("svc-a", "COMPLETED")
    wf_engine_2, exec_2 = _execution_with_outcome("svc-a", "COMPLETED")
    service.record(
        exec_1, workflow_engine=wf_engine_1, stage_durations={"build": 10.0}, timestamp=BASE_TIME
    )
    service.record(
        exec_2, workflow_engine=wf_engine_2, stage_durations={"build": 20.0}, timestamp=BASE_TIME
    )

    summary = service.summarize("svc-a")

    assert summary.stage_durations == {"build": 15.0}


def test_summarize_computes_average_queue_seconds(service: DeploymentWorkflowAnalyticsService):
    wf_engine, execution_id = _execution_with_outcome("svc-a", "COMPLETED")

    service.record(execution_id, workflow_engine=wf_engine, queue_seconds=5.0, timestamp=BASE_TIME)

    summary = service.summarize("svc-a")

    assert summary.average_queue_seconds == 5.0


def test_trends_buckets_by_time_window(service: DeploymentWorkflowAnalyticsService):
    wf_engine_1, exec_1 = _execution_with_outcome("svc-a", "COMPLETED")
    wf_engine_2, exec_2 = _execution_with_outcome("svc-a", "COMPLETED")
    service.record(exec_1, workflow_engine=wf_engine_1, timestamp=BASE_TIME)
    service.record(
        exec_2, workflow_engine=wf_engine_2, timestamp=BASE_TIME + timedelta(hours=2)
    )

    trends = service.trends("svc-a", bucket_seconds=3600.0)

    assert len(trends) == 2
    assert all(trend.total_executions == 1 for trend in trends)


def test_trends_rejects_non_positive_bucket_seconds(service: DeploymentWorkflowAnalyticsService):
    with pytest.raises(ValueError):
        service.trends("svc-a", bucket_seconds=0)


def test_trends_filters_by_pipeline(service: DeploymentWorkflowAnalyticsService):
    wf_engine_1, exec_1 = _execution_with_outcome("svc-a", "COMPLETED")
    wf_engine_2, exec_2 = _execution_with_outcome("svc-b", "COMPLETED")
    service.record(exec_1, workflow_engine=wf_engine_1, timestamp=BASE_TIME)
    service.record(exec_2, workflow_engine=wf_engine_2, timestamp=BASE_TIME)

    trends = service.trends("svc-a")

    assert all(trend.pipeline == "svc-a" for trend in trends)


def test_history_returns_recorded_entries_in_order(service: DeploymentWorkflowAnalyticsService):
    wf_engine_1, exec_1 = _execution_with_outcome("svc-a", "COMPLETED")
    wf_engine_2, exec_2 = _execution_with_outcome("svc-a", "FAILED")
    service.record(exec_1, workflow_engine=wf_engine_1, timestamp=BASE_TIME)
    service.record(exec_2, workflow_engine=wf_engine_2, timestamp=BASE_TIME + timedelta(minutes=1))

    history = service.history("svc-a")

    assert [entry["status"] for entry in history] == ["COMPLETED", "FAILED"]


def test_history_empty_for_unknown_pipeline(service: DeploymentWorkflowAnalyticsService):
    assert service.history("does-not-exist") == ()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_workflow_analytics_router)
    return TestClient(app)


def test_api_list_and_get_analytics(client: TestClient):
    wf_engine, execution_id = _execution_with_outcome("svc-analytics-api-1", "COMPLETED")
    get_deployment_workflow_analytics_service().record(
        execution_id, workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    list_response = client.get("/governance/workflow-analytics")
    get_response = client.get("/governance/workflow-analytics/svc-analytics-api-1")

    assert list_response.status_code == 200
    assert any(item["pipeline"] == "svc-analytics-api-1" for item in list_response.json())
    assert get_response.status_code == 200
    assert get_response.json()["total_executions"] == 1


def test_api_get_unknown_pipeline_returns_zeroed_summary(client: TestClient):
    response = client.get("/governance/workflow-analytics/does-not-exist-anywhere")

    assert response.status_code == 200
    assert response.json()["total_executions"] == 0


def test_api_trends(client: TestClient):
    wf_engine, execution_id = _execution_with_outcome("svc-analytics-api-2", "COMPLETED")
    get_deployment_workflow_analytics_service().record(
        execution_id, workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    response = client.get(
        "/governance/workflow-analytics/trends",
        params={"pipeline": "svc-analytics-api-2", "bucket_seconds": 3600},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
