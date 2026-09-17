import pytest

from backend.observability.alert_engine import AlertRule, AlertRuleEngine
from backend.observability.metrics_registry import MetricDefinition, MetricsRegistry


@pytest.fixture
def engine():
    return AlertRuleEngine()


@pytest.fixture
def registry():
    registry = MetricsRegistry()
    registry.register_metric(MetricDefinition(name="cpu_usage", metric_type="gauge"))
    return registry


class TestRuleRegistration:
    def test_register_threshold_rule(self, engine):
        rule = engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
                threshold=90,
                comparator="gt",
            )
        )

        assert rule.name == "high_cpu"

    def test_register_duplicate_raises(self, engine):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
                threshold=90,
            )
        )

        with pytest.raises(ValueError):
            engine.register_rule(
                AlertRule(
                    name="high_cpu",
                    metric_name="cpu_usage",
                    rule_type="threshold",
                    severity="critical",
                    threshold=90,
                )
            )

    def test_threshold_rule_requires_threshold(self, engine):
        with pytest.raises(ValueError):
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
            )

    def test_register_rejects_unknown_rule_type(self, engine):
        with pytest.raises(ValueError):
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="unknown",
                severity="critical",
                threshold=90,
            )


class TestThresholdEvaluation:
    def test_evaluate_returns_none_when_no_samples(self, engine, registry):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
                threshold=90,
            )
        )

        assert engine.evaluate("high_cpu", registry) is None

    def test_evaluate_returns_none_when_below_threshold(self, engine, registry):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
                threshold=90,
            )
        )
        registry.record("cpu_usage", 50)

        assert engine.evaluate("high_cpu", registry) is None

    def test_evaluate_triggers_when_threshold_breached(self, engine, registry):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="critical",
                threshold=90,
            )
        )
        registry.record("cpu_usage", 95)

        event = engine.evaluate("high_cpu", registry)

        assert event is not None
        assert event.severity == "critical"

    def test_evaluate_unregistered_rule_raises(self, engine, registry):
        with pytest.raises(KeyError):
            engine.evaluate("missing_rule", registry)


class TestAlertTriggering:
    def test_trigger_creates_active_alert(self, engine):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="warning",
                threshold=80,
            )
        )

        event = engine.trigger("high_cpu", "cpu over threshold")

        assert event.is_active
        assert event in engine.list_alerts(active_only=True)

    def test_trigger_deduplicates_while_active(self, engine):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="warning",
                threshold=80,
            )
        )

        first = engine.trigger("high_cpu", "cpu over threshold")
        second = engine.trigger("high_cpu", "cpu over threshold again")

        assert first.alert_id == second.alert_id
        assert len(engine.list_alerts()) == 1

    def test_trigger_unregistered_rule_raises(self, engine):
        with pytest.raises(KeyError):
            engine.trigger("missing_rule", "message")


class TestAlertResolution:
    def test_resolve_marks_alert_inactive(self, engine):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="warning",
                threshold=80,
            )
        )
        event = engine.trigger("high_cpu", "cpu over threshold")

        resolved = engine.resolve(event.alert_id)

        assert not resolved.is_active
        assert resolved not in engine.list_alerts(active_only=True)

    def test_resolve_twice_raises(self, engine):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="warning",
                threshold=80,
            )
        )
        event = engine.trigger("high_cpu", "cpu over threshold")
        engine.resolve(event.alert_id)

        with pytest.raises(ValueError):
            engine.resolve(event.alert_id)

    def test_evaluate_auto_resolves_when_no_longer_breached(self, engine, registry):
        engine.register_rule(
            AlertRule(
                name="high_cpu",
                metric_name="cpu_usage",
                rule_type="threshold",
                severity="warning",
                threshold=80,
            )
        )
        registry.record("cpu_usage", 95)
        triggered = engine.evaluate("high_cpu", registry)
        assert triggered.is_active

        registry.record("cpu_usage", 10)
        engine.evaluate("high_cpu", registry)

        assert engine.list_alerts(active_only=True) == []

    def test_resolve_unknown_alert_raises(self, engine):
        with pytest.raises(KeyError):
            engine.resolve("missing-alert-id")
