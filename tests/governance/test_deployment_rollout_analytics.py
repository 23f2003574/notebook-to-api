from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rollout_analytics import (
    DeploymentRolloutAnalytics,
    RolloutAnalyticsSnapshot,
    RolloutTrend,
    get_rollout_analytics,
)
from backend.observability.deployment_governance_rollout_manager import (
    DeploymentRolloutManager,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


class _ManualClock:
    """
    A clock that only moves when explicitly told to via advance() —
    unlike an auto-ticking clock, this stays stable across however
    many times a single record() call happens to read it internally,
    so tests can assert exact gaps between record() calls (used by
    MTTR, MTBF, deployment frequency, and the rolling window) without
    depending on how many times the implementation happens to read
    the clock per call.
    """

    def __init__(self, start: datetime = BASE_TIME) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def _clock():
    return BASE_TIME


def _engine(clock=_clock, **kwargs) -> DeploymentRolloutAnalytics:
    return DeploymentRolloutAnalytics(clock=clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The rollout analytics engine is a process-wide singleton; most
    tests below construct their own fresh engine instead (see
    _engine), and only the singleton and API tests touch the shared
    instance, matching test_deployment_rollback.py's own fixture.
    """

    def _reset():
        get_rollout_analytics().reset()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestRolloutAnalyticsSnapshot:

    def test_rejects_negative_successful_rollouts(self):
        with pytest.raises(
            ValueError, match="successful_rollouts must be >= 0"
        ):
            RolloutAnalyticsSnapshot(
                generated_at=BASE_TIME, successful_rollouts=-1,
                failed_rollouts=0, average_duration_seconds=0.0,
                rollback_rate=0.0,
            )

    def test_rejects_negative_failed_rollouts(self):
        with pytest.raises(
            ValueError, match="failed_rollouts must be >= 0"
        ):
            RolloutAnalyticsSnapshot(
                generated_at=BASE_TIME, successful_rollouts=0,
                failed_rollouts=-1, average_duration_seconds=0.0,
                rollback_rate=0.0,
            )

    def test_rejects_naive_generated_at(self):
        with pytest.raises(
            ValueError, match="generated_at must be timezone-aware"
        ):
            RolloutAnalyticsSnapshot(
                generated_at=datetime(2026, 7, 23, 12, 0, 0),
                successful_rollouts=0, failed_rollouts=0,
                average_duration_seconds=0.0, rollback_rate=0.0,
            )

    def test_to_dict(self):
        snapshot = RolloutAnalyticsSnapshot(
            generated_at=BASE_TIME, successful_rollouts=3,
            failed_rollouts=1, average_duration_seconds=12.5,
            rollback_rate=0.25,
        )

        assert snapshot.to_dict() == {
            "generated_at": BASE_TIME.isoformat(),
            "successful_rollouts": 3,
            "failed_rollouts": 1,
            "average_duration_seconds": 12.5,
            "rollback_rate": 0.25,
        }


class TestRolloutTrend:

    def test_rejects_empty_metric(self):
        with pytest.raises(ValueError, match="metric must not be empty"):
            RolloutTrend(
                metric="", current=1.0, previous=1.0, change_percent=0.0,
            )

    def test_to_dict(self):
        trend = RolloutTrend(
            metric="success_rate", current=0.9, previous=0.8,
            change_percent=12.5,
        )

        assert trend.to_dict() == {
            "metric": "success_rate",
            "current": 0.9,
            "previous": 0.8,
            "change_percent": 12.5,
        }


# --- Record / validation --------------------------------------------


class TestRecord:

    def test_record_rejects_unknown_outcome(self):
        engine = _engine()

        with pytest.raises(ValueError, match="outcome must be one of"):
            engine.record("dep-1", "BOGUS", 10.0)

    def test_record_rejects_negative_duration(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="duration_seconds must not be negative"
        ):
            engine.record("dep-1", "SUCCESS", -1.0)

    def test_record_returns_a_snapshot(self):
        engine = _engine()

        snapshot = engine.record("dep-1", "SUCCESS", 10.0)

        assert snapshot.successful_rollouts == 1
        assert snapshot.failed_rollouts == 0


# --- KPI calculation -----------------------------------------------------


class TestKpiCalculation:

    def test_success_and_failure_rate(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 10.0)
        engine.record("dep-2", "SUCCESS", 10.0)
        engine.record("dep-3", "FAILURE", 10.0)

        summary = engine.summary()

        assert summary["success_rate"] == pytest.approx(2 / 3)
        assert summary["failure_rate"] == pytest.approx(1 / 3)

    def test_average_duration(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 10.0)
        engine.record("dep-2", "SUCCESS", 20.0)

        assert engine.summary()["average_duration_seconds"] == 15.0

    def test_average_duration_falls_back_to_metrics_when_empty(self):
        from backend.observability.deployment_governance_scheduler_metrics import (  # noqa: E501
            GovernanceSchedulerMetrics,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_completion(execution_ms=2000.0)

        engine = _engine(metrics=metrics)

        assert engine.summary()["average_duration_seconds"] == 2.0

    def test_average_duration_zero_with_no_data_and_no_metrics(self):
        engine = _engine()

        assert engine.summary()["average_duration_seconds"] == 0.0

    def test_rollback_rate(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.record("dep-1", "SUCCESS", 10.0)
        engine.record("dep-2", "SUCCESS", 10.0)

        bus.publish("rollback_completed", "dep-1", {})

        assert engine.summary()["rollback_rate"] == pytest.approx(0.5)

    def test_mttr_averages_failure_to_next_success_gaps(self):
        clock = _ManualClock()
        engine = _engine(clock=clock)
        engine.record("dep-1", "FAILURE", 1.0)
        clock.advance(5)
        engine.record("dep-1", "FAILURE", 1.0)  # still pending recovery
        clock.advance(3)
        engine.record("dep-1", "SUCCESS", 1.0)

        # 3 seconds between the (most recent, per-deployment
        # sequential) failure and the success that follows it.
        assert engine.summary()["mttr_seconds"] == pytest.approx(3.0)

    def test_mttr_zero_with_no_recovered_failures(self):
        engine = _engine()
        engine.record("dep-1", "FAILURE", 1.0)

        assert engine.summary()["mttr_seconds"] == 0.0

    def test_mtbf_averages_gaps_between_failures(self):
        clock = _ManualClock()
        engine = _engine(clock=clock)
        engine.record("dep-1", "FAILURE", 1.0)
        clock.advance(4)
        engine.record("dep-2", "FAILURE", 1.0)
        clock.advance(6)
        engine.record("dep-3", "FAILURE", 1.0)

        assert engine.summary()["mtbf_seconds"] == pytest.approx(5.0)

    def test_mtbf_zero_with_fewer_than_two_failures(self):
        engine = _engine()
        engine.record("dep-1", "FAILURE", 1.0)

        assert engine.summary()["mtbf_seconds"] == 0.0

    def test_health_score_trend_averages_recorded_scores(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        bus.publish(
            "rollout_health_evaluated", "dep-1",
            {"deployment_id": "dep-1", "status": "HEALTHY", "score": 90.0},
        )
        bus.publish(
            "rollout_health_evaluated", "dep-1",
            {"deployment_id": "dep-1", "status": "HEALTHY", "score": 70.0},
        )

        assert engine.summary()["health_score_trend"] == 80.0

    def test_health_score_trend_zero_with_no_scores(self):
        engine = _engine()

        assert engine.summary()["health_score_trend"] == 0.0

    def test_deployment_frequency_uses_record_span(self):
        clock = _ManualClock()
        engine = _engine(clock=clock)
        engine.record("dep-1", "SUCCESS", 1.0)
        # 1 day total span across 2 records -> 2 records / 1 day.
        clock.advance(86400)
        engine.record("dep-2", "SUCCESS", 1.0)

        frequency = engine.summary()["deployment_frequency"]

        assert frequency == pytest.approx(2.0)

    def test_deployment_frequency_falls_back_to_count_with_zero_span(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 1.0)
        engine.record("dep-2", "SUCCESS", 1.0)

        assert engine.summary()["deployment_frequency"] == 2.0

    def test_deployment_frequency_zero_with_no_records(self):
        engine = _engine()

        assert engine.summary()["deployment_frequency"] == 0.0

    def test_deployment_frequency_uses_configured_window(self):
        engine = _engine(window_seconds=3600)
        engine.record("dep-1", "SUCCESS", 1.0)

        # 1 record / (3600s / 86400s-per-day) = 24 per day
        assert engine.summary()["deployment_frequency"] == pytest.approx(
            24.0
        )


# --- Custom KPI support -------------------------------------------------


class TestCustomKpi:

    def test_register_kpi_adds_a_custom_metric(self):
        engine = _engine()

        engine.register_kpi("queue_depth", lambda: 3.0)

        assert engine.summary()["queue_depth"] == 3.0

    def test_register_kpi_rejects_empty_name(self):
        engine = _engine()

        with pytest.raises(ValueError, match="name must not be empty"):
            engine.register_kpi("", lambda: 1.0)

    def test_custom_kpi_participates_in_trend(self):
        engine = _engine()
        engine.register_kpi("queue_depth", lambda: 3.0)
        engine.record("dep-1", "SUCCESS", 1.0)

        trend = engine.trend("queue_depth")

        assert trend.current == 3.0


# --- Trend computation ----------------------------------------------


class TestTrendComputation:

    def test_trend_unknown_metric_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.trend("does-not-exist")

    def test_trend_with_no_prior_history_has_zero_change(self):
        engine = _engine()

        trend = engine.trend("success_rate")

        assert trend.current == trend.previous
        assert trend.change_percent == 0.0

    def test_trend_reflects_change_between_records(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 1.0)

        first_trend = engine.trend("success_rate")
        assert first_trend.current == 1.0

        engine.record("dep-2", "FAILURE", 1.0)

        second_trend = engine.trend("success_rate")

        assert second_trend.previous == 1.0
        assert second_trend.current == 0.5
        assert second_trend.change_percent == pytest.approx(-50.0)

    def test_trend_change_percent_zero_when_previous_is_zero(self):
        engine = _engine()
        engine.record("dep-1", "FAILURE", 1.0)  # success_rate 0.0

        engine.record("dep-2", "SUCCESS", 1.0)  # success_rate now 0.5

        trend = engine.trend("success_rate")

        assert trend.previous == 0.0
        assert trend.change_percent == 0.0

    def test_publishes_rollout_trend_changed_after_first_record(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.record("dep-1", "SUCCESS", 1.0)

        events = []
        bus.subscribe("rollout_trend_changed", events.append)

        engine.record("dep-2", "FAILURE", 1.0)

        assert len(events) == 1

    def test_no_trend_event_on_the_very_first_record(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollout_trend_changed", events.append)

        engine.record("dep-1", "SUCCESS", 1.0)

        assert len(events) == 0


# --- Rolling-window aggregation ------------------------------------------


class TestRollingWindowAggregation:

    def test_records_outside_the_window_are_excluded(self):
        clock = _ManualClock()
        engine = _engine(clock=clock, window_seconds=1)
        engine.record("dep-1", "SUCCESS", 1.0)

        clock.advance(10)

        engine.record("dep-2", "FAILURE", 1.0)

        summary = engine.summary()

        # Only the most recent record (dep-2, FAILURE) is still
        # inside a 1-second window by the time of this summary() call.
        assert summary["failure_rate"] == 1.0

    def test_no_window_includes_everything(self):
        clock = _ManualClock()
        engine = _engine(clock=clock, window_seconds=None)
        engine.record("dep-1", "SUCCESS", 1.0)

        clock.advance(1000)

        engine.record("dep-2", "FAILURE", 1.0)

        assert engine.summary()["success_rate"] == pytest.approx(0.5)


# --- Analytics history / snapshot generation -----------------------------


class TestHistory:

    def test_history_empty_before_any_record(self):
        engine = _engine()

        assert engine.history() == ()

    def test_history_accumulates_one_snapshot_per_record(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 1.0)
        engine.record("dep-2", "FAILURE", 1.0)

        assert len(engine.history()) == 2

    def test_snapshot_reflects_current_state_without_recording(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 1.0)

        before = engine.snapshot()
        after = engine.snapshot()

        assert before == after
        assert len(engine.history()) == 1

    def test_export_includes_snapshot_kpis_and_history(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 1.0)

        exported = engine.export()

        assert "snapshot" in exported
        assert "kpis" in exported
        assert "history" in exported
        assert len(exported["history"]) == 1

    def test_export_includes_routing_count_when_router_wired(self):
        from backend.observability.deployment_governance_traffic_router import (  # noqa: E501
            DeploymentTrafficRouter,
        )

        router = DeploymentTrafficRouter(clock=_clock)
        router.configure("dep-1", [("1.0.0", 100.0)])

        engine = _engine(traffic_router=router)

        exported = engine.export()

        assert exported["active_routing_configurations"] == 1

    def test_publishes_rollout_snapshot_created(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollout_snapshot_created", events.append)

        engine.record("dep-1", "SUCCESS", 1.0)

        assert len(events) == 1

    def test_publishes_rollout_analytics_updated(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollout_analytics_updated", events.append)

        engine.record("dep-1", "SUCCESS", 1.0)

        assert len(events) == 1


# --- Threshold breaches -----------------------------------------------


class TestThresholds:

    def test_set_threshold_rejects_invalid_direction(self):
        engine = _engine()

        with pytest.raises(ValueError, match="direction must be"):
            engine.set_threshold("failure_rate", 0.5, direction="sideways")

    def test_above_threshold_breach_publishes_event(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.set_threshold("failure_rate", 0.4, direction="above")

        events = []
        bus.subscribe("rollout_kpi_threshold_exceeded", events.append)

        engine.record("dep-1", "FAILURE", 1.0)

        assert len(events) == 1
        assert events[0].payload["metric"] == "failure_rate"

    def test_below_threshold_breach_publishes_event(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.set_threshold("success_rate", 0.9, direction="below")

        events = []
        bus.subscribe("rollout_kpi_threshold_exceeded", events.append)

        engine.record("dep-1", "FAILURE", 1.0)

        assert len(events) == 1

    def test_no_breach_when_within_threshold(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.set_threshold("failure_rate", 0.9, direction="above")

        events = []
        bus.subscribe("rollout_kpi_threshold_exceeded", events.append)

        engine.record("dep-1", "SUCCESS", 1.0)

        assert len(events) == 0


# --- Concurrent aggregation ---------------------------------------------


class TestConcurrentAggregation:

    def test_concurrent_record_calls_are_all_captured(self):
        engine = _engine()

        thread_count = 25
        barrier = threading.Barrier(thread_count)

        def _worker(i: int) -> None:
            barrier.wait()
            outcome = "SUCCESS" if i % 2 == 0 else "FAILURE"
            engine.record(f"dep-{i}", outcome, float(i))

        threads = [
            threading.Thread(target=_worker, args=(i,))
            for i in range(thread_count)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert len(engine.history()) == thread_count

        summary = engine.summary()

        assert summary["success_rate"] + summary["failure_rate"] == (
            pytest.approx(1.0)
        )


# --- Reset behavior ------------------------------------------------------


class TestReset:

    def test_reset_clears_records_and_history(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 1.0)

        engine.reset()

        assert engine.history() == ()
        assert engine.summary()["success_rate"] == 0.0

    def test_reset_keeps_registered_kpis_and_thresholds(self):
        engine = _engine()
        engine.register_kpi("queue_depth", lambda: 5.0)
        engine.set_threshold("queue_depth", 10.0, direction="above")

        engine.reset()

        assert engine.summary()["queue_depth"] == 5.0

    def test_reset_allows_trend_to_start_fresh(self):
        engine = _engine()
        engine.record("dep-1", "SUCCESS", 1.0)

        engine.reset()

        trend = engine.trend("success_rate")

        assert trend.current == trend.previous


# --- Runtime integration (event-driven ingestion) ------------------------


class TestRuntimeIntegration:

    def test_rollout_completed_is_recorded_via_rollout_manager(self):
        clock = _ManualClock()
        bus = GovernanceEventBus(clock=clock)
        rollout_manager = DeploymentRolloutManager(
            clock=clock, event_bus=bus
        )
        engine = _engine(
            clock=clock, event_bus=bus, rollout_manager=rollout_manager,
        )

        rollout = rollout_manager.create("dep-1", "CANARY")
        rollout_manager.start(rollout.rollout_id)
        rollout_manager.complete(rollout.rollout_id)

        assert engine.summary()["success_rate"] == 1.0

    def test_rollout_failed_is_recorded_via_rollout_manager(self):
        clock = _ManualClock()
        bus = GovernanceEventBus(clock=clock)
        rollout_manager = DeploymentRolloutManager(
            clock=clock, event_bus=bus
        )
        engine = _engine(
            clock=clock, event_bus=bus, rollout_manager=rollout_manager,
        )

        rollout = rollout_manager.create("dep-1", "CANARY")
        rollout_manager.start(rollout.rollout_id)
        rollout_manager.fail(rollout.rollout_id)

        assert engine.summary()["failure_rate"] == 1.0

    def test_no_rollout_manager_wired_skips_ingestion_silently(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        bus.publish("rollout_completed", "rollout-1", {
            "deployment_id": "dep-1",
        })

        assert engine.history() == ()


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_rollout_analytics_returns_same_instance(self):
        assert get_rollout_analytics() is get_rollout_analytics()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRolloutAnalyticsApi:

    def test_get_analytics_snapshot(self, client):
        response = client.get("/governance/rollout/analytics")

        assert response.status_code == 200
        assert "successful_rollouts" in response.json()

    def test_get_summary(self, client):
        response = client.get("/governance/rollout/analytics/summary")

        assert response.status_code == 200
        assert "success_rate" in response.json()

    def test_get_trends(self, client):
        response = client.get("/governance/rollout/analytics/trends")

        assert response.status_code == 200

        metrics = {entry["metric"] for entry in response.json()}

        assert "success_rate" in metrics

    def test_post_reset(self, client):
        response = client.post("/governance/rollout/analytics/reset")

        assert response.status_code == 200
        assert response.json() == {"reset": True}
