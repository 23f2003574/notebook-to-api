import pytest

from backend.observability.alert_engine import AlertRule, AlertRuleEngine
from backend.observability.dashboard import ObservabilityDashboardAPI
from backend.observability.health_checks import HealthCheck, HealthCheckFramework
from backend.observability.observability_analytics import ObservabilityAnalyticsService


@pytest.fixture
def analytics_service():
    return ObservabilityAnalyticsService()


@pytest.fixture
def alert_engine():
    return AlertRuleEngine()


@pytest.fixture
def health_framework():
    return HealthCheckFramework()


@pytest.fixture
def dashboard(analytics_service, alert_engine, health_framework):
    return ObservabilityDashboardAPI(analytics_service, alert_engine, health_framework)


class TestMetricsEndpoint:
    def test_metrics_returns_average_and_latest(self, dashboard, analytics_service):
        analytics_service.record("request_rate", 100)
        analytics_service.record("request_rate", 200)

        metrics = dashboard.metrics()

        assert metrics["request_rate"] == {"average": 150, "latest": 200}

    def test_metrics_empty_when_nothing_recorded(self, dashboard):
        assert dashboard.metrics() == {}


class TestAlertEndpoint:
    def test_alerts_returns_active_alerts_by_default(self, dashboard, alert_engine):
        alert_engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
                threshold=90,
            )
        )
        alert_engine.trigger("high_cpu", "cpu over threshold")

        alerts = dashboard.alerts()

        assert len(alerts) == 1
        assert alerts[0]["rule_name"] == "high_cpu"
        assert alerts[0]["is_active"] is True

    def test_alerts_excludes_resolved_when_active_only(self, dashboard, alert_engine):
        alert_engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
                threshold=90,
            )
        )
        event = alert_engine.trigger("high_cpu", "cpu over threshold")
        alert_engine.resolve(event.alert_id)

        assert dashboard.alerts(active_only=True) == []
        assert len(dashboard.alerts(active_only=False)) == 1


class TestHealthEndpoint:
    def test_health_reports_healthy_status(self, dashboard, health_framework):
        health_framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )

        report = dashboard.health()

        assert report["status"] == "healthy"

    def test_health_reports_unhealthy_status(self, dashboard, health_framework):
        health_framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: False)
        )

        report = dashboard.health()

        assert report["status"] == "unhealthy"


class TestOverviewEndpoint:
    def test_overview_bundles_all_sections(
        self, dashboard, analytics_service, alert_engine, health_framework
    ):
        analytics_service.record("request_rate", 100)
        alert_engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="warning",
                threshold=90,
            )
        )
        alert_engine.trigger("high_cpu", "cpu over threshold")
        health_framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )

        overview = dashboard.overview()

        assert overview["generated_at"]
        assert overview["metrics"]["request_rate"]["latest"] == 100
        assert len(overview["alerts"]) == 1
        assert overview["health"]["status"] == "healthy"
