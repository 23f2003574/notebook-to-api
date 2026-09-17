from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_logging import DeploymentLoggingService
from backend.governance.deployment_tracing import (
    DeploymentTracingService,
    UnknownSpanError,
    UnknownTraceError,
    router as deployment_tracing_router,
)

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def service() -> DeploymentTracingService:
    return DeploymentTracingService()


def test_start_trace_creates_root_span(service: DeploymentTracingService):
    root = service.start_trace("deploy_rollout", timestamp=BASE_TIME)

    assert root.parent_span_id is None
    assert root.trace_id
    assert root.span_id
    assert root.operation == "deploy_rollout"


def test_nested_spans_track_parent_child_relationship(
    service: DeploymentTracingService,
):
    root = service.start_trace("deploy_rollout", timestamp=BASE_TIME)
    child = service.create_span(
        root.trace_id,
        "run_health_check",
        parent_span_id=root.span_id,
        timestamp=BASE_TIME + timedelta(seconds=1),
    )
    grandchild = service.create_span(
        child.trace_id,
        "ping_endpoint",
        parent_span_id=child.span_id,
        timestamp=BASE_TIME + timedelta(seconds=2),
    )

    assert child.parent_span_id == root.span_id
    assert grandchild.parent_span_id == child.span_id
    assert grandchild.trace_id == root.trace_id


def test_create_span_rejects_unknown_parent(service: DeploymentTracingService):
    root = service.start_trace("deploy_rollout", timestamp=BASE_TIME)

    with pytest.raises(UnknownSpanError):
        service.create_span(
            root.trace_id, "orphan", parent_span_id="does-not-exist"
        )


def test_finish_span_calculates_duration(service: DeploymentTracingService):
    root = service.start_trace("deploy_rollout", timestamp=BASE_TIME)

    finished = service.finish_span(
        root.trace_id,
        root.span_id,
        status="OK",
        timestamp=BASE_TIME + timedelta(seconds=5),
    )

    assert finished.duration_ms == 5000.0
    assert finished.status == "OK"


def test_finish_span_produces_new_immutable_span(
    service: DeploymentTracingService,
):
    root = service.start_trace("deploy_rollout", timestamp=BASE_TIME)

    finished = service.finish_span(root.trace_id, root.span_id)

    assert root.end_time is None
    assert finished.end_time is not None
    assert finished is not root


def test_finish_span_unknown_trace_raises(service: DeploymentTracingService):
    with pytest.raises(UnknownTraceError):
        service.finish_span("missing-trace", "missing-span")


def test_get_trace_returns_spans_ordered_by_start_time(
    service: DeploymentTracingService,
):
    root = service.start_trace("deploy_rollout", timestamp=BASE_TIME)
    service.create_span(
        root.trace_id,
        "step_two",
        parent_span_id=root.span_id,
        timestamp=BASE_TIME + timedelta(seconds=2),
    )
    service.create_span(
        root.trace_id,
        "step_one",
        parent_span_id=root.span_id,
        timestamp=BASE_TIME + timedelta(seconds=1),
    )

    spans = service.get_trace(root.trace_id)

    assert [span.operation for span in spans] == [
        "deploy_rollout",
        "step_one",
        "step_two",
    ]


def test_get_trace_unknown_trace_raises(service: DeploymentTracingService):
    with pytest.raises(UnknownTraceError):
        service.get_trace("missing-trace")


def test_logging_service_propagates_trace_correlation():
    logging_service = DeploymentLoggingService()
    tracing_service = DeploymentTracingService()
    root = tracing_service.start_trace("deploy_rollout", timestamp=BASE_TIME)

    entry = logging_service.log(
        "deploy-1",
        "INFO",
        "span started",
        trace_id=root.trace_id,
        span_id=root.span_id,
        timestamp=BASE_TIME,
    )

    assert entry.context["trace_id"] == root.trace_id
    assert entry.context["span_id"] == root.span_id


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_tracing_router)
    return TestClient(app)


def test_api_get_trace_returns_spans(client: TestClient):
    from backend.governance.deployment_tracing import (
        get_deployment_tracing_service,
    )

    tracing_service = get_deployment_tracing_service()
    root = tracing_service.start_trace("deploy_rollout", timestamp=BASE_TIME)
    tracing_service.finish_span(root.trace_id, root.span_id)

    response = client.get(f"/governance/traces/{root.trace_id}")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["span_id"] == root.span_id


def test_api_get_trace_missing_returns_404(client: TestClient):
    response = client.get("/governance/traces/does-not-exist")

    assert response.status_code == 404


def test_api_list_traces(client: TestClient):
    from backend.governance.deployment_tracing import (
        get_deployment_tracing_service,
    )

    tracing_service = get_deployment_tracing_service()
    root = tracing_service.start_trace("deploy_rollout", timestamp=BASE_TIME)

    response = client.get("/governance/traces")

    assert response.status_code == 200
    assert root.trace_id in response.json()
