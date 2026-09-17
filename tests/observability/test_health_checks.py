import time

import pytest

from backend.observability.alert_engine import AlertRule, AlertRuleEngine
from backend.observability.health_checks import HealthCheck, HealthCheckFramework


@pytest.fixture
def framework():
    return HealthCheckFramework()


class TestCheckRegistration:
    def test_register_liveness_check(self, framework):
        check = framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )

        assert check.name == "api"

    def test_register_duplicate_raises(self, framework):
        framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )

        with pytest.raises(ValueError):
            framework.register(
                HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
            )

    def test_register_rejects_unknown_check_type(self, framework):
        with pytest.raises(ValueError):
            HealthCheck(name="api", check_type="unknown", check_fn=lambda: True)

    def test_register_with_unknown_dependency_raises(self, framework):
        with pytest.raises(KeyError):
            framework.register(
                HealthCheck(
                    name="gateway",
                    check_type="readiness",
                    check_fn=lambda: True,
                    depends_on=["missing"],
                )
            )


class TestStatusAggregation:
    def test_run_reports_healthy(self, framework):
        framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )

        report = framework.run("api")

        assert report.status == "healthy"

    def test_run_reports_unhealthy_on_falsy_result(self, framework):
        framework.register(
            HealthCheck(name="db", check_type="readiness", check_fn=lambda: False)
        )

        report = framework.run("db")

        assert report.status == "unhealthy"

    def test_run_reports_unhealthy_on_exception(self, framework):
        def failing_check():
            raise RuntimeError("connection refused")

        framework.register(
            HealthCheck(name="db", check_type="readiness", check_fn=failing_check)
        )

        report = framework.run("db")

        assert report.status == "unhealthy"
        assert "connection refused" in report.error

    def test_aggregate_returns_healthy_when_all_pass(self, framework):
        framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )
        framework.register(
            HealthCheck(name="db", check_type="readiness", check_fn=lambda: True)
        )

        report = framework.aggregate()

        assert report.status == "healthy"

    def test_aggregate_returns_unhealthy_when_any_fail(self, framework):
        framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )
        framework.register(
            HealthCheck(name="db", check_type="readiness", check_fn=lambda: False)
        )

        report = framework.aggregate()

        assert report.status == "unhealthy"

    def test_status_returns_last_run_report(self, framework):
        framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )
        framework.run("api")

        report = framework.status("api")

        assert report.status == "healthy"

    def test_status_without_run_raises(self, framework):
        framework.register(
            HealthCheck(name="api", check_type="liveness", check_fn=lambda: True)
        )

        with pytest.raises(KeyError):
            framework.status("api")


class TestTimeoutHandling:
    def test_run_times_out_slow_check(self, framework):
        def slow_check():
            time.sleep(1)
            return True

        framework.register(
            HealthCheck(
                name="slow", check_type="startup", check_fn=slow_check, timeout_seconds=0.05
            )
        )

        report = framework.run("slow")

        assert report.status == "unhealthy"
        assert "timed out" in report.error


class TestDependencyFailures:
    def test_run_marks_dependent_unhealthy_when_dependency_fails(self, framework):
        framework.register(
            HealthCheck(name="db", check_type="dependency", check_fn=lambda: False)
        )
        framework.register(
            HealthCheck(
                name="api", check_type="readiness", check_fn=lambda: True, depends_on=["db"]
            )
        )

        report = framework.run("api")

        assert report.status == "unhealthy"
        assert "db" in report.error

    def test_run_passes_when_dependency_healthy(self, framework):
        framework.register(
            HealthCheck(name="db", check_type="dependency", check_fn=lambda: True)
        )
        framework.register(
            HealthCheck(
                name="api", check_type="readiness", check_fn=lambda: True, depends_on=["db"]
            )
        )

        report = framework.run("api")

        assert report.status == "healthy"

    def test_evaluate_health_triggers_alert_on_unhealthy(self, framework):
        engine = AlertRuleEngine()
        engine.register_rule(
            AlertRule(
                name="db_down",
                metric_name="unused",
                rule_type="composite",
                severity="critical",
            )
        )
        framework.register(
            HealthCheck(name="db", check_type="readiness", check_fn=lambda: False)
        )
        report = framework.run("db")

        event = engine.evaluate_health("db_down", report)

        assert event is not None
        assert event.is_active
