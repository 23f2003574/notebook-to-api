from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_alerts import AlertRule, DeploymentAlertManager
from backend.governance.deployment_diagnostics import (
    DeploymentDiagnosticsService,
    UnknownDeploymentError,
    router as deployment_diagnostics_router,
)
from backend.governance.deployment_logging import DeploymentLoggingService
from backend.governance.deployment_metrics import (
    FAILURE_COUNT,
    DeploymentMetricsCollector,
)
from backend.governance.deployment_recovery import DeploymentRecoveryCoordinator
from backend.governance.deployment_tracing import DeploymentTracingService

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def service() -> DeploymentDiagnosticsService:
    return DeploymentDiagnosticsService()


def test_capture_pulls_logs_for_the_deployment(
    service: DeploymentDiagnosticsService,
):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "INFO", "starting rollout", timestamp=BASE_TIME)
    logging_service.log("svc-b", "INFO", "unrelated", timestamp=BASE_TIME)

    snapshot = service.capture(
        "svc-a", logging_service=logging_service, timestamp=BASE_TIME
    )

    assert len(snapshot.logs) == 1
    assert snapshot.logs[0]["message"] == "starting rollout"


def test_capture_correlates_traces_via_log_trace_ids(
    service: DeploymentDiagnosticsService,
):
    logging_service = DeploymentLoggingService()
    tracing_service = DeploymentTracingService()
    root = tracing_service.start_trace("deploy_rollout", timestamp=BASE_TIME)
    logging_service.log(
        "svc-a",
        "INFO",
        "span started",
        trace_id=root.trace_id,
        span_id=root.span_id,
        timestamp=BASE_TIME,
    )

    snapshot = service.capture(
        "svc-a",
        logging_service=logging_service,
        tracing_service=tracing_service,
        timestamp=BASE_TIME,
    )

    assert len(snapshot.traces) == 1
    assert snapshot.traces[0]["trace_id"] == root.trace_id


def test_capture_includes_metrics_alerts_and_recovery_events(
    service: DeploymentDiagnosticsService,
):
    metrics_collector = DeploymentMetricsCollector()
    metrics_collector.increment(FAILURE_COUNT, amount=1)

    alert_manager = DeploymentAlertManager(rules=[])
    alert_manager.register_rule(
        AlertRule(
            name="svc-a",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )
    alert_manager.evaluate(metrics_collector.snapshot(), timestamp=BASE_TIME)

    recovery_coordinator = DeploymentRecoveryCoordinator()
    recovery_coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    snapshot = service.capture(
        "svc-a",
        metrics_collector=metrics_collector,
        alert_manager=alert_manager,
        recovery_coordinator=recovery_coordinator,
        timestamp=BASE_TIME,
    )

    assert snapshot.metrics is not None
    assert len(snapshot.alerts) == 1
    assert len(snapshot.recovery_events) == 1


def test_capture_requires_deployment_identifier(
    service: DeploymentDiagnosticsService,
):
    with pytest.raises(ValueError):
        service.capture("")


def test_analyze_builds_chronological_timeline(
    service: DeploymentDiagnosticsService,
):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "INFO", "step one", timestamp=BASE_TIME)
    logging_service.log(
        "svc-a", "ERROR", "step two failed", timestamp=BASE_TIME + timedelta(seconds=5)
    )

    report = service.analyze(
        "svc-a", logging_service=logging_service, timestamp=BASE_TIME
    )

    assert [event["summary"] for event in report.timeline] == [
        "step one",
        "step two failed",
    ]


def test_analyze_root_cause_prefers_alerts_over_logs(
    service: DeploymentDiagnosticsService,
):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "ERROR", "minor issue", timestamp=BASE_TIME)

    metrics_collector = DeploymentMetricsCollector()
    metrics_collector.increment(FAILURE_COUNT, amount=1)
    alert_manager = DeploymentAlertManager(rules=[])
    alert_manager.register_rule(
        AlertRule(
            name="svc-a",
            level="CRITICAL",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )
    alert_manager.evaluate(metrics_collector.snapshot(), timestamp=BASE_TIME)

    report = service.analyze(
        "svc-a",
        logging_service=logging_service,
        alert_manager=alert_manager,
        timestamp=BASE_TIME,
    )

    assert "alert 'svc-a'" in report.root_cause
    assert "CRITICAL" in report.root_cause


def test_analyze_root_cause_falls_back_to_error_logs(
    service: DeploymentDiagnosticsService,
):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "INFO", "fine", timestamp=BASE_TIME)
    logging_service.log(
        "svc-a",
        "ERROR",
        "disk full",
        timestamp=BASE_TIME + timedelta(seconds=1),
    )

    report = service.analyze(
        "svc-a", logging_service=logging_service, timestamp=BASE_TIME
    )

    assert "disk full" in report.root_cause


def test_analyze_root_cause_falls_back_to_failed_recovery(
    service: DeploymentDiagnosticsService,
):
    recovery_coordinator = DeploymentRecoveryCoordinator(strategies=[])
    from backend.governance.deployment_recovery import RecoveryStrategy

    class _AlwaysFails(RecoveryStrategy):
        def execute(self, context):
            raise RuntimeError("could not roll back")

    recovery_coordinator.register_strategy(_AlwaysFails("only"))
    recovery_coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    report = service.analyze(
        "svc-a", recovery_coordinator=recovery_coordinator, timestamp=BASE_TIME
    )

    assert "could not roll back" in report.root_cause


def test_analyze_root_cause_defaults_when_nothing_wrong(
    service: DeploymentDiagnosticsService,
):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "INFO", "all good", timestamp=BASE_TIME)

    report = service.analyze(
        "svc-a", logging_service=logging_service, timestamp=BASE_TIME
    )

    assert report.root_cause == "no significant issues detected"


def test_analyze_reuses_previously_captured_snapshot(
    service: DeploymentDiagnosticsService,
):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "INFO", "captured", timestamp=BASE_TIME)
    snapshot = service.capture(
        "svc-a", logging_service=logging_service, timestamp=BASE_TIME
    )

    logging_service.log("svc-a", "INFO", "added after capture", timestamp=BASE_TIME)
    report = service.analyze("svc-a", timestamp=BASE_TIME)

    assert report.snapshot is snapshot
    assert len(report.timeline) == 1


def test_report_returns_latest_analysis(service: DeploymentDiagnosticsService):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "INFO", "hello", timestamp=BASE_TIME)
    service.analyze("svc-a", logging_service=logging_service, timestamp=BASE_TIME)

    report = service.report("svc-a")

    assert report.deployment == "svc-a"


def test_report_unknown_deployment_raises(service: DeploymentDiagnosticsService):
    with pytest.raises(UnknownDeploymentError):
        service.report("does-not-exist")


def test_history_filters_by_deployment(service: DeploymentDiagnosticsService):
    logging_service = DeploymentLoggingService()
    logging_service.log("svc-a", "INFO", "a", timestamp=BASE_TIME)
    logging_service.log("svc-b", "INFO", "b", timestamp=BASE_TIME)

    service.analyze("svc-a", logging_service=logging_service, timestamp=BASE_TIME)
    service.analyze("svc-b", logging_service=logging_service, timestamp=BASE_TIME)

    assert len(service.history()) == 2
    assert len(service.history(deployment="svc-a")) == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_diagnostics_router)
    return TestClient(app)


def test_api_analyze_and_get_report(client: TestClient):
    from backend.governance.deployment_logging import (
        get_deployment_logging_service,
    )

    get_deployment_logging_service().log(
        "svc-api-1", "INFO", "api driven", timestamp=BASE_TIME
    )

    analyze_response = client.post(
        "/governance/diagnostics/analyze", json={"deployment": "svc-api-1"}
    )
    get_response = client.get("/governance/diagnostics/svc-api-1")

    assert analyze_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["deployment"] == "svc-api-1"


def test_api_analyze_requires_deployment(client: TestClient):
    response = client.post("/governance/diagnostics/analyze", json={})

    assert response.status_code == 422


def test_api_get_unknown_deployment_returns_404(client: TestClient):
    response = client.get("/governance/diagnostics/does-not-exist")

    assert response.status_code == 404


def test_api_history(client: TestClient):
    client.post(
        "/governance/diagnostics/analyze", json={"deployment": "svc-api-2"}
    )

    response = client.get(
        "/governance/diagnostics/history", params={"deployment": "svc-api-2"}
    )

    assert response.status_code == 200
    assert any(r["deployment"] == "svc-api-2" for r in response.json())
