from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rollback import (
    DeploymentRollbackEngine,
)
from backend.observability.deployment_governance_rollout_health import (
    HEALTH_STATES,
    DeploymentRolloutHealthEngine,
    HealthIndicator,
    RolloutHealthSnapshot,
    get_rollout_health_engine,
)
from backend.observability.deployment_governance_scheduler_metrics import (
    GovernanceSchedulerMetrics,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentRolloutHealthEngine:
    return DeploymentRolloutHealthEngine(clock=_clock, **kwargs)


def _bare_engine(**kwargs) -> DeploymentRolloutHealthEngine:
    """
    A health engine with every built-in indicator removed, for tests
    that want to register their own indicators from a clean slate
    without the built-ins (which all default to "healthy") diluting
    the weighted score.
    """

    engine = _engine(**kwargs)

    for name in list(engine._indicators):
        engine.remove_indicator(name)

    return engine


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The rollout health engine is a process-wide singleton; most tests
    below construct their own fresh engine instead (see _engine), and
    only the singleton and API tests touch the shared instance,
    matching test_deployment_rollback.py's own fixture.
    """

    def _reset():
        get_rollout_health_engine().clear_history()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestHealthIndicator:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            HealthIndicator(
                name="", value=1.0, threshold=1.0, healthy=True
            )

    def test_to_dict(self):
        indicator = HealthIndicator(
            name="success_rate", value=0.95, threshold=0.9,
            healthy=True,
        )

        assert indicator.to_dict() == {
            "name": "success_rate",
            "value": 0.95,
            "threshold": 0.9,
            "healthy": True,
        }


class TestRolloutHealthSnapshot:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            RolloutHealthSnapshot(
                deployment_id="", status="HEALTHY", score=100.0,
                evaluated_at=BASE_TIME,
            )

    def test_rejects_unknown_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            RolloutHealthSnapshot(
                deployment_id="dep-1", status="BOGUS", score=100.0,
                evaluated_at=BASE_TIME,
            )

    def test_rejects_score_out_of_range(self):
        with pytest.raises(
            ValueError, match="score must be between 0 and 100"
        ):
            RolloutHealthSnapshot(
                deployment_id="dep-1", status="HEALTHY", score=101.0,
                evaluated_at=BASE_TIME,
            )

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            RolloutHealthSnapshot(
                deployment_id="dep-1", status="HEALTHY", score=100.0,
                evaluated_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_to_dict(self):
        snapshot = RolloutHealthSnapshot(
            deployment_id="dep-1", status="HEALTHY", score=95.0,
            evaluated_at=BASE_TIME,
        )

        assert snapshot.to_dict() == {
            "deployment_id": "dep-1",
            "status": "HEALTHY",
            "score": 95.0,
            "evaluated_at": BASE_TIME.isoformat(),
        }


# --- Indicator registration -------------------------------------------


class TestIndicatorRegistration:

    def test_builtin_indicators_are_preregistered(self):
        engine = _engine()

        names = set(engine._indicators)

        assert names == {
            "success_rate", "error_rate", "request_latency",
            "instance_availability", "traffic_distribution",
            "restart_count", "rollback_count",
        }

    def test_register_indicator_adds_a_custom_one(self):
        engine = _engine()

        engine.register_indicator(
            "queue_depth", lambda deployment_id: 3.0, threshold=10.0,
            higher_is_better=False,
        )

        assert "queue_depth" in engine._indicators

    def test_register_indicator_rejects_empty_name(self):
        engine = _engine()

        with pytest.raises(ValueError, match="name must not be empty"):
            engine.register_indicator(
                "", lambda deployment_id: 1.0, threshold=1.0,
            )

    def test_register_indicator_rejects_non_positive_weight(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="weight must be greater than 0"
        ):
            engine.register_indicator(
                "custom", lambda deployment_id: 1.0, threshold=1.0,
                weight=0,
            )

    def test_register_indicator_can_override_a_builtin(self):
        engine = _engine()

        engine.register_indicator(
            "success_rate", lambda deployment_id: 0.5, threshold=0.9,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score < 100.0

    def test_remove_indicator(self):
        engine = _engine()

        engine.remove_indicator("traffic_distribution")

        assert "traffic_distribution" not in engine._indicators

    def test_remove_unknown_indicator_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.remove_indicator("does-not-exist")


# --- Weighted score calculation ----------------------------------------


class TestWeightedScoreCalculation:

    def test_all_healthy_scores_100(self):
        engine = _bare_engine()
        engine.register_indicator(
            "a", lambda deployment_id: 1.0, threshold=0.5, weight=1.0,
        )
        engine.register_indicator(
            "b", lambda deployment_id: 1.0, threshold=0.5, weight=1.0,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == 100.0

    def test_all_unhealthy_scores_0(self):
        engine = _bare_engine()
        engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5, weight=1.0,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == 0.0

    def test_score_is_weighted_by_indicator_weight(self):
        engine = _bare_engine()
        engine.register_indicator(
            "heavy", lambda deployment_id: 1.0, threshold=0.5,
            weight=3.0,
        )
        engine.register_indicator(
            "light", lambda deployment_id: 0.0, threshold=0.5,
            weight=1.0,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == pytest.approx(75.0)

    def test_no_indicators_scores_100(self):
        engine = _bare_engine()

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == 100.0

    def test_lower_is_better_indicator(self):
        engine = _bare_engine()
        engine.register_indicator(
            "error_rate", lambda deployment_id: 0.05, threshold=0.1,
            higher_is_better=False,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == 100.0

    def test_deterministic_evaluation_order(self):
        engine = _bare_engine()
        calls = []

        engine.register_indicator(
            "z-indicator",
            lambda deployment_id: calls.append("z-indicator") or 1.0,
            threshold=0.5, priority=1,
        )
        engine.register_indicator(
            "a-indicator",
            lambda deployment_id: calls.append("a-indicator") or 1.0,
            threshold=0.5, priority=1,
        )
        engine.register_indicator(
            "first",
            lambda deployment_id: calls.append("first") or 1.0,
            threshold=0.5, priority=0,
        )

        engine.evaluate("dep-1")

        assert calls == ["first", "a-indicator", "z-indicator"]


# --- Healthy / degraded / unhealthy evaluation --------------------------


class TestHealthyEvaluation:

    def test_no_metrics_or_rollback_engine_wired_is_healthy(self):
        engine = _engine()

        snapshot = engine.evaluate("dep-1")

        assert snapshot.status == "HEALTHY"
        assert snapshot.score == 100.0

    def test_evaluate_publishes_rollout_health_evaluated(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollout_health_evaluated", events.append)

        engine.evaluate("dep-1")

        assert len(events) == 1
        assert events[0].payload["status"] == "HEALTHY"


class TestDegradedEvaluation:

    def test_score_in_degraded_band(self):
        engine = _bare_engine()
        engine.register_indicator(
            "a", lambda deployment_id: 1.0, threshold=0.5,
        )
        engine.register_indicator(
            "b", lambda deployment_id: 0.0, threshold=0.5,
        )
        engine.register_indicator(
            "c", lambda deployment_id: 1.0, threshold=0.5,
        )
        engine.register_indicator(
            "d", lambda deployment_id: 1.0, threshold=0.5,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == 75.0
        assert snapshot.status == "DEGRADED"

    def test_publishes_rollout_health_degraded(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _bare_engine(event_bus=bus)
        engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5, weight=1.0,
        )
        engine.register_indicator(
            "b", lambda deployment_id: 1.0, threshold=0.5, weight=3.0,
        )

        events = []
        bus.subscribe("rollout_health_degraded", events.append)

        engine.evaluate("dep-1")

        assert len(events) == 1


class TestUnhealthyEvaluation:

    def test_score_in_unhealthy_band(self):
        engine = _bare_engine()
        engine.register_indicator(
            "a", lambda deployment_id: 1.0, threshold=0.5, weight=1.0,
        )
        engine.register_indicator(
            "b", lambda deployment_id: 0.0, threshold=0.5, weight=1.0,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == 50.0
        assert snapshot.status == "UNHEALTHY"

    def test_publishes_rollout_health_unhealthy(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _bare_engine(event_bus=bus)
        engine.register_indicator(
            "a", lambda deployment_id: 1.0, threshold=0.5, weight=1.0,
        )
        engine.register_indicator(
            "b", lambda deployment_id: 0.0, threshold=0.5, weight=1.0,
        )

        events = []
        bus.subscribe("rollout_health_unhealthy", events.append)

        engine.evaluate("dep-1")

        assert len(events) == 1

    def test_does_not_publish_unhealthy_when_score_is_critical(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _bare_engine(event_bus=bus)
        engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5,
        )

        events = []
        bus.subscribe("rollout_health_unhealthy", events.append)

        engine.evaluate("dep-1")  # score 0 lands in CRITICAL, not this

        assert len(events) == 0


class TestCriticalEvaluation:

    def test_score_in_critical_band(self):
        engine = _bare_engine()
        engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.status == "CRITICAL"

    def test_publishes_rollout_health_critical(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _bare_engine(event_bus=bus)
        engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5,
        )

        events = []
        bus.subscribe("rollout_health_critical", events.append)

        engine.evaluate("dep-1")

        assert len(events) == 1
        assert events[0].payload["deployment_id"] == "dep-1"


class TestHealthRestored:

    def test_restored_fires_only_on_recovery(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _bare_engine(event_bus=bus)
        healthy = {"value": False}
        engine.register_indicator(
            "a", lambda deployment_id: (
                1.0 if healthy["value"] else 0.0
            ),
            threshold=0.5,
        )

        events = []
        bus.subscribe("rollout_health_restored", events.append)

        engine.evaluate("dep-1")  # CRITICAL, no restored event
        assert len(events) == 0

        healthy["value"] = True
        engine.evaluate("dep-1")  # HEALTHY now, restored fires

        assert len(events) == 1

    def test_restored_does_not_fire_on_first_healthy_evaluation(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollout_health_restored", events.append)

        engine.evaluate("dep-1")

        assert len(events) == 0


# --- Custom indicator support -------------------------------------------


class TestCustomIndicatorSupport:

    def test_custom_indicator_receives_deployment_id(self):
        engine = _bare_engine()
        seen = []

        engine.register_indicator(
            "custom", lambda deployment_id: seen.append(deployment_id)
            or 1.0,
            threshold=0.5,
        )

        engine.evaluate("dep-custom")

        assert seen == ["dep-custom"]

    def test_custom_indicator_participates_in_score(self):
        engine = _bare_engine()
        engine.register_indicator(
            "custom", lambda deployment_id: 5.0, threshold=10.0,
            higher_is_better=False, weight=2.0,
        )

        snapshot = engine.evaluate("dep-1")

        assert snapshot.score == 100.0


# --- Rollout decision integration --------------------------------------


class TestRolloutDecisionIntegration:

    def test_decision_for_healthy_is_continue(self):
        engine = _engine()

        assert engine.decision_for("HEALTHY") == "CONTINUE"

    def test_decision_for_degraded_is_continue(self):
        engine = _engine()

        assert engine.decision_for("DEGRADED") == "CONTINUE"

    def test_decision_for_unhealthy_is_pause(self):
        engine = _engine()

        assert engine.decision_for("UNHEALTHY") == "PAUSE"

    def test_decision_for_critical_is_rollback(self):
        engine = _engine()

        assert engine.decision_for("CRITICAL") == "ROLLBACK"

    def test_decision_for_unknown_status_raises(self):
        engine = _engine()

        with pytest.raises(ValueError, match="status must be one of"):
            engine.decision_for("BOGUS")

    def test_canary_evaluate_consults_wired_health_engine(self):
        from backend.observability.deployment_governance_canary import (
            CanaryDeploymentEngine,
        )

        health_engine = _bare_engine()
        health_engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5,
        )

        canary = CanaryDeploymentEngine(
            clock=_clock, health_engine=health_engine,
        )
        canary.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        evaluation = canary.evaluate("dep-1")

        assert evaluation.healthy is False

    def test_rolling_validate_batch_consults_wired_health_engine(self):
        from backend.observability.deployment_governance_rolling import (
            RollingDeploymentEngine,
        )

        health_engine = _bare_engine()
        health_engine.register_indicator(
            "a", lambda deployment_id: 1.0, threshold=0.5,
        )

        rolling = RollingDeploymentEngine(
            clock=_clock, health_engine=health_engine,
        )
        rolling.deploy("dep-1", "1.0.0", 10, batch_size=3)
        rolling.next_batch("dep-1")

        result = rolling.validate_batch("dep-1")

        assert result.healthy is True

    def test_progressive_advance_consults_wired_health_engine(self):
        from backend.observability.deployment_governance_progressive_delivery import (  # noqa: E501
            ProgressiveDeliveryEngine,
        )

        health_engine = _bare_engine()
        health_engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5,
        )

        progressive = ProgressiveDeliveryEngine(
            clock=_clock, health_engine=health_engine,
        )
        progressive.deploy(
            "dep-1", [("stage-1", "HEALTH_VALIDATION", False)],
        )

        result = progressive.advance("dep-1")

        assert result.state == "ROLLED_BACK"

    def test_rollback_engine_reacts_to_rollout_health_critical(self):
        from backend.observability.deployment_governance_version_registry import (  # noqa: E501
            DeploymentVersionRegistry,
        )

        bus = GovernanceEventBus(clock=_clock)
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "a.tar.gz", "a" * 64)
        registry.update("dep-1", "1.1.0", "a2.tar.gz", "a" * 64)

        rollback_engine = DeploymentRollbackEngine(
            clock=_clock, event_bus=bus, version_registry=registry,
        )
        health_engine = _bare_engine(event_bus=bus)
        health_engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5,
        )

        health_engine.evaluate("dep-1")

        result = rollback_engine.latest("dep-1")

        assert result.success is True
        assert result.restored_version == "1.0.0"

    def test_rollout_manager_fails_matching_rollout_on_critical_health(
        self,
    ):
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            DeploymentRolloutManager,
        )

        bus = GovernanceEventBus(clock=_clock)
        manager = DeploymentRolloutManager(clock=_clock, event_bus=bus)
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        health_engine = _bare_engine(event_bus=bus)
        health_engine.register_indicator(
            "a", lambda deployment_id: 0.0, threshold=0.5,
        )

        health_engine.evaluate("dep-1")

        assert manager.status(rollout.rollout_id).state == "FAILED"


# --- History / summary --------------------------------------------------


class TestHistory:

    def test_history_empty_for_unknown_deployment(self):
        engine = _engine()

        assert engine.history("dep-1") == ()

    def test_history_accumulates(self):
        engine = _engine()
        engine.evaluate("dep-1")
        engine.evaluate("dep-1")

        assert len(engine.history("dep-1")) == 2

    def test_latest_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.latest("dep-1")

    def test_latest_returns_the_most_recent(self):
        engine = _engine()
        engine.evaluate("dep-1")
        second = engine.evaluate("dep-1")

        assert engine.latest("dep-1") == second

    def test_evaluate_all_reevaluates_every_known_deployment(self):
        engine = _engine()
        engine.evaluate("dep-a")
        engine.evaluate("dep-b")

        results = engine.evaluate_all()

        assert [r.deployment_id for r in results] == ["dep-a", "dep-b"]
        assert len(engine.history("dep-a")) == 2

    def test_summary_counts_by_status(self):
        engine = _bare_engine()
        engine.register_indicator(
            "always_healthy", lambda deployment_id: 1.0, threshold=0.5,
        )
        engine.evaluate("dep-1")

        summary = engine.summary()

        assert summary["total_evaluated"] == 1
        assert summary["healthy"] == 1
        assert summary["degraded"] == 0

    def test_list_orders_by_deployment_id(self):
        engine = _engine()
        engine.evaluate("dep-b")
        engine.evaluate("dep-a")

        listed = engine.list()

        assert [s.deployment_id for s in listed] == ["dep-a", "dep-b"]

    def test_clear_history_removes_evaluations_but_keeps_indicators(
        self,
    ):
        engine = _engine()
        engine.evaluate("dep-1")

        engine.clear_history()

        assert engine.history("dep-1") == ()
        assert "success_rate" in engine._indicators


# --- Metrics integration -----------------------------------------------


class TestMetricsIntegration:

    def test_success_rate_reflects_scheduler_metrics(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_completion(execution_ms=10.0)
        metrics.record_completion(execution_ms=10.0)
        metrics.record_failure(execution_ms=10.0)

        engine = _engine(metrics=metrics)

        snapshot = engine.evaluate("dep-1")

        # 2/3 completed is below the 0.9 success_rate threshold, and
        # 1/3 failed is above the 0.1 error_rate threshold — both
        # unhealthy, dragging the score down.
        assert snapshot.score < 100.0


# --- Scheduler integration ----------------------------------------------


class TestSchedulerIntegration:

    def test_construction_does_not_register_anything(self):
        """
        Unlike Canary/Rolling's per-deployment jobs, the sweep job is
        opt-in via register_sweep_job() — merely constructing the
        engine (including the process-wide singleton) must not
        register a permanent, not-tied-to-any-deployment job as a
        side effect.
        """

        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        scheduler = GovernanceScheduler(clock=_clock)

        _engine(scheduler=scheduler)

        assert scheduler.jobs() == ()

    def test_register_sweep_job(self):
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        scheduler = GovernanceScheduler(clock=_clock)
        engine = _engine(scheduler=scheduler)

        engine.register_sweep_job()

        names = {job.name for job in scheduler.jobs()}

        assert "rollout-health-evaluation-sweep" in names

    def test_register_sweep_job_is_idempotent(self):
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        scheduler = GovernanceScheduler(clock=_clock)
        engine = _engine(scheduler=scheduler)

        first = engine.register_sweep_job()
        second = engine.register_sweep_job()

        assert first == second
        assert len(scheduler.jobs()) == 1

    def test_register_sweep_job_without_scheduler_raises(self):
        engine = _engine(scheduler=None)

        with pytest.raises(ValueError, match="requires a scheduler"):
            engine.register_sweep_job()

    def test_no_scheduler_wired_is_safe(self):
        _engine(scheduler=None)


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_rollout_health_engine_returns_same_instance(self):
        assert (
            get_rollout_health_engine() is get_rollout_health_engine()
        )

    def test_singleton_is_wired_into_canary_rolling_progressive(self):
        from backend.observability.deployment_governance_canary import (
            get_canary_engine,
        )
        from backend.observability.deployment_governance_progressive_delivery import (  # noqa: E501
            get_progressive_delivery_engine,
        )
        from backend.observability.deployment_governance_rolling import (
            get_rolling_engine,
        )

        engine = get_rollout_health_engine()

        assert get_canary_engine()._health_engine is engine
        assert get_rolling_engine()._health_engine is engine
        assert (
            get_progressive_delivery_engine()._health_engine is engine
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRolloutHealthApi:

    def test_post_evaluate(self, client):
        response = client.post(
            "/governance/rollout-health/dep-api-1/evaluate"
        )

        assert response.status_code == 200
        assert response.json()["status"] in HEALTH_STATES

    def test_get_snapshot(self, client):
        client.post("/governance/rollout-health/dep-api-2/evaluate")

        response = client.get("/governance/rollout-health/dep-api-2")

        assert response.status_code == 200
        assert response.json()["deployment_id"] == "dep-api-2"

    def test_get_unknown_deployment_returns_404(self, client):
        response = client.get(
            "/governance/rollout-health/does-not-exist"
        )

        assert response.status_code == 404

    def test_list_snapshots(self, client):
        client.post("/governance/rollout-health/dep-api-3/evaluate")

        response = client.get("/governance/rollout-health")

        assert response.status_code == 200
        assert any(
            s["deployment_id"] == "dep-api-3" for s in response.json()
        )
