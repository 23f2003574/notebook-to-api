import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetricsService,
)
from backend.observability.deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricsAlertService,
)
from backend.observability.deployment_governance_metrics_api import (
    GovernanceIntegrityMetricsApi,
)
from backend.observability.deployment_governance_metrics_history import (
    InMemoryGovernanceIntegrityMetricsHistoryRepository,
)


class TestGovernanceIntegrityMetricsApiSummary:

    def test_summary_empty_state(self):
        metrics_service = GovernanceIntegrityMetricsService()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        summary = api.summary()

        assert summary.total_dispatches == 0

    def test_summary_reflects_recorded_activity(self):
        metrics_service = GovernanceIntegrityMetricsService()

        metrics_service.record_success(10.0)
        metrics_service.record_failure(20.0)

        api = GovernanceIntegrityMetricsApi(metrics_service)

        summary = api.summary()

        assert summary.total_dispatches == 2
        assert summary.successful_dispatches == 1
        assert summary.failed_dispatches == 1


class TestGovernanceIntegrityMetricsApiHistory:

    def _service_with_history(self):
        history_repository = (
            InMemoryGovernanceIntegrityMetricsHistoryRepository()
        )

        return GovernanceIntegrityMetricsService(
            auto_flush_enabled=False,
            history_repository=history_repository,
        )

    def test_history_empty_state(self):
        metrics_service = self._service_with_history()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        assert api.history() == ()

    def test_history_is_newest_first(self):
        metrics_service = self._service_with_history()

        metrics_service.record_success(10.0)
        metrics_service.capture_snapshot()

        metrics_service.record_success(20.0)
        metrics_service.capture_snapshot()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        history = api.history()

        assert len(history) == 2
        assert (
            history[0].metrics.successful_dispatches
            == 2
        )
        assert (
            history[1].metrics.successful_dispatches
            == 1
        )

    def test_history_pagination_limit(self):
        metrics_service = self._service_with_history()

        for _ in range(5):
            metrics_service.capture_snapshot()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        page = api.history(limit=2)

        assert len(page) == 2

    def test_history_pagination_offset(self):
        metrics_service = self._service_with_history()

        for i in range(5):
            metrics_service.record_success(float(i))
            metrics_service.capture_snapshot()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        full = api.history()
        offset_page = api.history(offset=2)

        assert offset_page == full[2:]

    def test_history_pagination_limit_and_offset(self):
        metrics_service = self._service_with_history()

        for _ in range(5):
            metrics_service.capture_snapshot()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        full = api.history()
        page = api.history(limit=2, offset=1)

        assert page == full[1:3]

    def test_history_negative_offset_rejected(self):
        metrics_service = self._service_with_history()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        with pytest.raises(ValueError):
            api.history(offset=-1)

    def test_history_negative_limit_rejected(self):
        metrics_service = self._service_with_history()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        with pytest.raises(ValueError):
            api.history(limit=-1)


class TestGovernanceIntegrityMetricsApiAlerts:

    def test_alerts_without_alert_service_is_empty(self):
        metrics_service = GovernanceIntegrityMetricsService()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        assert api.alerts() == ()

    def test_alerts_reflects_active_state(self):
        metrics_service = GovernanceIntegrityMetricsService()
        alert_service = GovernanceIntegrityMetricsAlertService()

        for _ in range(9):
            metrics_service.record_failure(10.0)

        metrics_service.record_success(10.0)

        alert_service.evaluate(metrics_service.snapshot())

        api = GovernanceIntegrityMetricsApi(
            metrics_service, alert_service=alert_service
        )

        names = {alert.name for alert in api.alerts()}

        assert "failure_rate" in names

    def test_alerts_empty_when_nothing_triggered(self):
        metrics_service = GovernanceIntegrityMetricsService()
        alert_service = GovernanceIntegrityMetricsAlertService()

        metrics_service.record_success(10.0)

        alert_service.evaluate(metrics_service.snapshot())

        api = GovernanceIntegrityMetricsApi(
            metrics_service, alert_service=alert_service
        )

        assert api.alerts() == ()


class TestGovernanceIntegrityMetricsApiDashboard:

    def test_dashboard_empty_state(self):
        metrics_service = GovernanceIntegrityMetricsService()

        api = GovernanceIntegrityMetricsApi(metrics_service)

        dashboard = api.dashboard()

        assert dashboard.summary.total_dispatches == 0
        assert dashboard.success_rate == 0.0
        assert dashboard.active_alerts == 0

    def test_dashboard_reflects_metrics_and_alerts(self):
        metrics_service = GovernanceIntegrityMetricsService()
        alert_service = GovernanceIntegrityMetricsAlertService()

        for _ in range(7):
            metrics_service.record_success(10.0)

        for _ in range(3):
            metrics_service.record_failure(10.0)

        alert_service.evaluate(metrics_service.snapshot())

        api = GovernanceIntegrityMetricsApi(
            metrics_service, alert_service=alert_service
        )

        dashboard = api.dashboard()

        assert dashboard.summary.total_dispatches == 10
        assert dashboard.success_rate == 70.0
        assert dashboard.failure_rate == 30.0


def _setup_sqlite_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceMetricsApiIntegration:

    def test_summary_endpoint_empty_state(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-summary-empty.db")

        response = client.get("/governance/metrics")

        assert response.status_code == 200

        payload = response.json()

        assert payload["total_dispatches"] == 0

    def test_summary_endpoint_reflects_persisted_metrics(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-summary.db")

        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
            deployment_governance_persistence_config_from_env,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        runtime.build_integrity_metrics_service().record_success(
            100.0
        )

        response = client.get("/governance/metrics")

        assert response.status_code == 200
        assert response.json()["total_dispatches"] == 1

    def test_dashboard_endpoint(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-dashboard.db")

        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
            deployment_governance_persistence_config_from_env,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_metrics_service()

        for _ in range(7):
            service.record_success(10.0)

        for _ in range(3):
            service.record_failure(10.0)

        response = client.get("/governance/metrics/dashboard")

        assert response.status_code == 200

        payload = response.json()

        assert payload["success_rate"] == 70.0
        assert payload["failure_rate"] == 30.0

    def test_history_endpoint_empty_state(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-history-empty.db")

        response = client.get("/governance/metrics/history")

        assert response.status_code == 200
        assert response.json() == []

    def test_history_endpoint_pagination(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-history.db")

        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
            deployment_governance_persistence_config_from_env,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_metrics_service()

        for i in range(5):
            service.record_success(float(i))

        response = client.get(
            "/governance/metrics/history?limit=2&offset=1"
        )

        assert response.status_code == 200

        payload = response.json()

        assert len(payload) == 2

    def test_history_endpoint_rejects_negative_offset(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(
            monkeypatch, tmp_path, "api-history-invalid.db"
        )

        response = client.get("/governance/metrics/history?offset=-1")

        assert response.status_code == 422

    def test_alerts_endpoint_empty_state(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-alerts-empty.db")

        response = client.get("/governance/metrics/alerts")

        assert response.status_code == 200
        assert response.json() == []

    def test_alerts_endpoint_reflects_active_alerts(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-alerts.db")

        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
            deployment_governance_persistence_config_from_env,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        service = runtime.build_integrity_metrics_service()

        for _ in range(9):
            service.record_failure(10.0)

        service.record_success(10.0)

        response = client.get("/governance/metrics/alerts")

        assert response.status_code == 200

        names = {alert["name"] for alert in response.json()}

        assert "failure_rate" in names
