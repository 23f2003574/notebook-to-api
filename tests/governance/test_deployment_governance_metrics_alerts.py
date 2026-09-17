from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
)
from backend.observability.deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricAlert,
    GovernanceIntegrityMetricsAlertService,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _metrics(
    total=0, successful=0, failed=0, retries=0, average_duration_ms=0.0
) -> GovernanceIntegrityMetrics:
    return GovernanceIntegrityMetrics(
        total_dispatches=total,
        successful_dispatches=successful,
        failed_dispatches=failed,
        retry_dispatches=retries,
        average_duration_ms=average_duration_ms,
    )


class TestGovernanceIntegrityMetricAlert:

    def test_triggered_requires_triggered_at(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricAlert(
                name="x",
                triggered=True,
                value=1.0,
                threshold=0.5,
                triggered_at=None,
            )

    def test_untriggered_must_not_have_triggered_at(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricAlert(
                name="x",
                triggered=False,
                value=0.0,
                threshold=0.5,
                triggered_at=BASE_TIME,
            )

    def test_naive_triggered_at_rejected(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricAlert(
                name="x",
                triggered=True,
                value=1.0,
                threshold=0.5,
                triggered_at=datetime(2026, 1, 1),
            )

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricAlert(
                name="  ",
                triggered=False,
                value=0.0,
                threshold=0.5,
                triggered_at=None,
            )


class TestGovernanceIntegrityMetricsAlertServiceRegistration:

    def test_default_alerts_are_registered(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(_metrics())

        names = {alert.name for alert in results}

        assert names == {
            "failure_rate",
            "retry_rate",
            "average_latency",
        }

    def test_register_defaults_false_registers_nothing(self):
        service = GovernanceIntegrityMetricsAlertService(
            register_defaults=False
        )

        assert service.evaluate(_metrics()) == ()

    def test_register_custom_alert(self):
        service = GovernanceIntegrityMetricsAlertService(
            register_defaults=False
        )

        service.register(
            "high_volume", lambda m: float(m.total_dispatches), 100.0
        )

        results = service.evaluate(
            _metrics(total=150, successful=150)
        )

        assert len(results) == 1
        assert results[0].name == "high_volume"
        assert results[0].triggered is True

    def test_duplicate_registration_raises_error(self):
        service = GovernanceIntegrityMetricsAlertService(
            register_defaults=False
        )

        service.register("x", lambda m: 0.0, 1.0)

        with pytest.raises(ValueError, match="already registered"):
            service.register("x", lambda m: 0.0, 1.0)

    def test_remove_unregisters_alert(self):
        service = GovernanceIntegrityMetricsAlertService(
            register_defaults=False
        )

        service.register("x", lambda m: 0.0, 1.0)
        service.remove("x")

        assert service.evaluate(_metrics()) == ()

    def test_remove_missing_alert_raises_error(self):
        service = GovernanceIntegrityMetricsAlertService(
            register_defaults=False
        )

        with pytest.raises(KeyError):
            service.remove("does-not-exist")

    def test_remove_drops_active_state(self):
        service = GovernanceIntegrityMetricsAlertService(
            register_defaults=False
        )

        service.register("x", lambda m: 10.0, 1.0)
        service.evaluate(_metrics())

        assert len(service.active()) == 1

        service.remove("x")

        assert service.active() == ()


class TestGovernanceIntegrityMetricsAlertServiceFailureRate:

    def test_failure_rate_not_triggered_below_threshold(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(
            _metrics(total=10, successful=8, failed=2)
        )

        failure_alert = next(
            a for a in results if a.name == "failure_rate"
        )

        assert failure_alert.triggered is False
        assert failure_alert.value == pytest.approx(0.2)

    def test_failure_rate_alert_triggers_above_threshold(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(
            _metrics(total=10, successful=3, failed=7)
        )

        failure_alert = next(
            a for a in results if a.name == "failure_rate"
        )

        assert failure_alert.triggered is True
        assert failure_alert.value == pytest.approx(0.7)

    def test_failure_rate_with_zero_dispatches_is_not_triggered(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(_metrics())

        failure_alert = next(
            a for a in results if a.name == "failure_rate"
        )

        assert failure_alert.triggered is False
        assert failure_alert.value == 0.0


class TestGovernanceIntegrityMetricsAlertServiceRetryRate:

    def test_retry_rate_alert_triggers_above_threshold(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(
            _metrics(total=10, successful=10, retries=8)
        )

        retry_alert = next(
            a for a in results if a.name == "retry_rate"
        )

        assert retry_alert.triggered is True
        assert retry_alert.value == pytest.approx(0.8)

    def test_retry_rate_alert_not_triggered_below_threshold(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(
            _metrics(total=10, successful=10, retries=1)
        )

        retry_alert = next(
            a for a in results if a.name == "retry_rate"
        )

        assert retry_alert.triggered is False


class TestGovernanceIntegrityMetricsAlertServiceLatency:

    def test_latency_alert_triggers_above_threshold(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(
            _metrics(
                total=1,
                successful=1,
                average_duration_ms=10000.0,
            )
        )

        latency_alert = next(
            a for a in results if a.name == "average_latency"
        )

        assert latency_alert.triggered is True
        assert latency_alert.value == 10000.0

    def test_latency_alert_not_triggered_below_threshold(self):
        service = GovernanceIntegrityMetricsAlertService()

        results = service.evaluate(
            _metrics(
                total=1, successful=1, average_duration_ms=100.0
            )
        )

        latency_alert = next(
            a for a in results if a.name == "average_latency"
        )

        assert latency_alert.triggered is False


class TestGovernanceIntegrityMetricsAlertServiceActiveState:

    def test_active_omits_untriggered_alerts(self):
        service = GovernanceIntegrityMetricsAlertService()

        service.evaluate(_metrics())

        assert service.active() == ()

    def test_active_reflects_triggered_alerts(self):
        service = GovernanceIntegrityMetricsAlertService()

        service.evaluate(
            _metrics(total=10, successful=1, failed=9)
        )

        active_names = {alert.name for alert in service.active()}

        assert "failure_rate" in active_names

    def test_no_duplicate_active_alerts_across_evaluations(self):
        service = GovernanceIntegrityMetricsAlertService()

        service.evaluate(_metrics(total=10, successful=1, failed=9))
        service.evaluate(_metrics(total=20, successful=2, failed=18))

        active = service.active()

        names = [alert.name for alert in active]

        assert len(names) == len(set(names))

    def test_triggered_at_persists_while_still_active(self):
        timestamps = iter(
            [
                BASE_TIME,
                BASE_TIME + timedelta(minutes=5),
            ]
        )

        service = GovernanceIntegrityMetricsAlertService(
            clock=lambda: next(timestamps)
        )

        first = service.evaluate(
            _metrics(total=10, successful=1, failed=9)
        )
        second = service.evaluate(
            _metrics(total=20, successful=2, failed=18)
        )

        first_failure = next(
            a for a in first if a.name == "failure_rate"
        )
        second_failure = next(
            a for a in second if a.name == "failure_rate"
        )

        assert first_failure.triggered_at == BASE_TIME
        assert second_failure.triggered_at == BASE_TIME

    def test_alert_resolves_when_no_longer_triggered(self):
        service = GovernanceIntegrityMetricsAlertService()

        service.evaluate(_metrics(total=10, successful=1, failed=9))

        assert any(
            a.name == "failure_rate" for a in service.active()
        )

        service.evaluate(_metrics(total=10, successful=10, failed=0))

        assert not any(
            a.name == "failure_rate" for a in service.active()
        )

    def test_clear_dismisses_active_alerts(self):
        service = GovernanceIntegrityMetricsAlertService()

        service.evaluate(_metrics(total=10, successful=1, failed=9))

        assert len(service.active()) > 0

        service.clear()

        assert service.active() == ()

    def test_clear_does_not_unregister_definitions(self):
        service = GovernanceIntegrityMetricsAlertService()

        service.evaluate(_metrics(total=10, successful=1, failed=9))
        service.clear()

        results = service.evaluate(
            _metrics(total=10, successful=1, failed=9)
        )

        assert any(
            a.name == "failure_rate" and a.triggered
            for a in results
        )

    def test_clear_resets_triggered_at_for_still_triggered_alert(
        self,
    ):
        timestamps = iter(
            [
                BASE_TIME,
                BASE_TIME + timedelta(minutes=10),
            ]
        )

        service = GovernanceIntegrityMetricsAlertService(
            clock=lambda: next(timestamps)
        )

        service.evaluate(_metrics(total=10, successful=1, failed=9))
        service.clear()

        results = service.evaluate(
            _metrics(total=10, successful=1, failed=9)
        )

        failure_alert = next(
            a for a in results if a.name == "failure_rate"
        )

        assert failure_alert.triggered_at == (
            BASE_TIME + timedelta(minutes=10)
        )
