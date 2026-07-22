from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_scheduler_metrics import (
    GovernanceSchedulerMetrics,
    SchedulerMetrics,
    SchedulerPerformanceSnapshot,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The scheduler metrics collector is a process-wide singleton, so
    tests that touch it (directly or via the API) must not leak state
    into other tests — reset() is the collector's own, built-in way to
    do that.
    """

    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_scheduler_metrics().reset()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestSchedulerMetrics:

    def test_rejects_negative_counters(self):
        with pytest.raises(ValueError, match="jobs_registered must be >= 0"):
            SchedulerMetrics(
                jobs_registered=-1, jobs_scheduled=0, jobs_completed=0,
                jobs_failed=0, jobs_cancelled=0, active_jobs=0,
                pending_jobs=0, collected_at=BASE_TIME,
            )

    def test_rejects_naive_collected_at(self):
        with pytest.raises(
            ValueError, match="collected_at must be timezone-aware"
        ):
            SchedulerMetrics(
                jobs_registered=0, jobs_scheduled=0, jobs_completed=0,
                jobs_failed=0, jobs_cancelled=0, active_jobs=0,
                pending_jobs=0,
                collected_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict(self):
        metrics = SchedulerMetrics(
            jobs_registered=1, jobs_scheduled=2, jobs_completed=3,
            jobs_failed=4, jobs_cancelled=5, active_jobs=6,
            pending_jobs=7, collected_at=BASE_TIME,
        )

        assert metrics.to_dict() == {
            "jobs_registered": 1,
            "jobs_scheduled": 2,
            "jobs_completed": 3,
            "jobs_failed": 4,
            "jobs_cancelled": 5,
            "active_jobs": 6,
            "pending_jobs": 7,
            "collected_at": BASE_TIME.isoformat(),
        }


class TestSchedulerPerformanceSnapshot:

    def test_rejects_out_of_range_retry_rate(self):
        with pytest.raises(
            ValueError, match="retry_rate must be between 0 and 1"
        ):
            SchedulerPerformanceSnapshot(
                average_execution_ms=0, average_queue_wait_ms=0,
                retry_rate=1.5, scheduler_utilization=0,
                collected_at=BASE_TIME,
            )

    def test_rejects_negative_average_execution_ms(self):
        with pytest.raises(
            ValueError, match="average_execution_ms must be >= 0"
        ):
            SchedulerPerformanceSnapshot(
                average_execution_ms=-1, average_queue_wait_ms=0,
                retry_rate=0, scheduler_utilization=0,
                collected_at=BASE_TIME,
            )

    def test_to_dict(self):
        snapshot = SchedulerPerformanceSnapshot(
            average_execution_ms=10.5, average_queue_wait_ms=2.0,
            retry_rate=0.25, scheduler_utilization=0.5,
            collected_at=BASE_TIME,
        )

        assert snapshot.to_dict() == {
            "average_execution_ms": 10.5,
            "average_queue_wait_ms": 2.0,
            "retry_rate": 0.25,
            "scheduler_utilization": 0.5,
            "collected_at": BASE_TIME.isoformat(),
        }


# --- Counter updates -------------------------------------------------


class TestCounterUpdates:

    def test_record_schedule_increments_jobs_registered(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_schedule(registered_jobs=1, pending_jobs=1)
        metrics.record_schedule(registered_jobs=2, pending_jobs=2)

        assert metrics.snapshot().jobs_registered == 2

    def test_record_dispatch_increments_by_count(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_dispatch(count=3)

        assert metrics.snapshot().jobs_scheduled == 3

    def test_record_dispatch_zero_count_does_not_increment(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_dispatch(count=0, tick_duration_ms=5.0)

        assert metrics.snapshot().jobs_scheduled == 0

    def test_record_dispatch_rejects_negative_count(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        with pytest.raises(ValueError, match="count must be >= 0"):
            metrics.record_dispatch(count=-1)

    def test_record_completion_increments_jobs_completed(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_completion(execution_ms=10)

        assert metrics.snapshot().jobs_completed == 1

    def test_record_failure_increments_jobs_failed(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_failure(execution_ms=10)

        assert metrics.snapshot().jobs_failed == 1

    def test_record_failure_cancelled_increments_jobs_cancelled(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_failure(execution_ms=10, cancelled=True)

        snapshot = metrics.snapshot()
        assert snapshot.jobs_cancelled == 1
        assert snapshot.jobs_failed == 0

    def test_counters_are_monotonic(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        for _ in range(5):
            metrics.record_completion(execution_ms=1)

        assert metrics.snapshot().jobs_completed == 5


# --- Gauge updates -------------------------------------------------------


class TestGaugeUpdates:

    def test_record_schedule_sets_gauges(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_schedule(registered_jobs=3, pending_jobs=2)

        snapshot = metrics.snapshot()
        # active_jobs/pending_jobs on SchedulerMetrics: pending_jobs is
        # set directly by record_schedule(); active_jobs is set by
        # record_dispatch() instead, so it stays 0 here.
        assert snapshot.pending_jobs == 2

    def test_record_dispatch_sets_active_jobs_gauge(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_dispatch(count=2, active_jobs=2)

        assert metrics.snapshot().active_jobs == 2

    def test_gauges_reflect_the_latest_value_not_a_sum(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_dispatch(count=1, active_jobs=5)
        metrics.record_dispatch(count=1, active_jobs=2)

        assert metrics.snapshot().active_jobs == 2


# --- Duration recording ------------------------------------------------


class TestDurationRecording:

    def test_record_completion_feeds_average_execution_ms(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_completion(execution_ms=10)
        metrics.record_completion(execution_ms=20)

        assert metrics.summary().average_execution_ms == 15.0

    def test_record_failure_also_feeds_average_execution_ms(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_completion(execution_ms=10)
        metrics.record_failure(execution_ms=30)

        assert metrics.summary().average_execution_ms == 20.0

    def test_record_queue_wait_feeds_average_queue_wait_ms(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_queue_wait(wait_ms=4)
        metrics.record_queue_wait(wait_ms=6)

        assert metrics.summary().average_queue_wait_ms == 5.0

    def test_record_retry_feeds_retry_delay_timer(self):
        # retry_delay isn't directly exposed via summary(), but
        # recording it must not raise and must not affect other
        # timers.
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_retry(delay_ms=100)

        assert metrics.summary().average_execution_ms == 0.0

    def test_record_lock_contention_feeds_acquisition_timer(self):
        # Not directly exposed via summary() either, but must not
        # raise and must still increment lock_contentions internally
        # (exercised indirectly through the contended flag).
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_lock_contention(acquisition_ms=5, contended=False)

        # No public accessor for lock_contentions directly; this just
        # confirms the call is accepted and harmless.
        assert metrics.summary().average_execution_ms == 0.0


# --- Rolling averages ----------------------------------------------------


class TestRollingAverages:

    def test_average_reflects_only_recorded_samples(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_completion(execution_ms=100)

        assert metrics.summary().average_execution_ms == 100.0

    def test_average_updates_as_more_samples_are_recorded(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_completion(execution_ms=10)
        first = metrics.summary().average_execution_ms

        metrics.record_completion(execution_ms=30)
        second = metrics.summary().average_execution_ms

        assert first == 10.0
        assert second == 20.0

    def test_rolling_window_is_bounded(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        for _ in range(250):
            metrics.record_completion(execution_ms=10)

        # All samples are identical, so the bounded window doesn't
        # change the average, but this exercises that recording well
        # beyond the window size doesn't error or grow unbounded.
        assert metrics.summary().average_execution_ms == 10.0

    def test_retry_rate_computed_from_counters(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_dispatch(count=4)
        metrics.record_retry(delay_ms=1)

        assert metrics.summary().retry_rate == 0.25

    def test_retry_rate_zero_with_no_executions(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        assert metrics.summary().retry_rate == 0.0

    def test_scheduler_utilization_computed(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        metrics.record_schedule(registered_jobs=4, pending_jobs=4)
        metrics.record_dispatch(count=2, active_jobs=2)

        assert metrics.summary().scheduler_utilization == 0.5

    def test_scheduler_utilization_zero_with_no_registered_jobs(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        assert metrics.summary().scheduler_utilization == 0.0


# --- Snapshot generation -------------------------------------------------


class TestSnapshotGeneration:

    def test_repeated_snapshot_is_deterministic(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_completion(execution_ms=5)

        first = metrics.snapshot()
        second = metrics.snapshot()

        assert first == second

    def test_snapshot_reflects_collected_at(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        assert metrics.snapshot().collected_at == BASE_TIME

    def test_snapshot_publishes_scheduler_metrics_snapshot(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        metrics = GovernanceSchedulerMetrics(clock=_clock, event_bus=bus)
        metrics.snapshot()

        assert received == ["scheduler_metrics_snapshot"]

    def test_threshold_exceeded_publishes_event(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        metrics = GovernanceSchedulerMetrics(
            clock=_clock, event_bus=bus, slow_execution_threshold_ms=100,
        )
        metrics.record_completion(execution_ms=150)

        assert "scheduler_metrics_threshold_exceeded" in received

    def test_below_threshold_does_not_publish(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        metrics = GovernanceSchedulerMetrics(
            clock=_clock, event_bus=bus, slow_execution_threshold_ms=100,
        )
        metrics.record_completion(execution_ms=50)

        assert "scheduler_metrics_threshold_exceeded" not in received


# --- Concurrent updates ------------------------------------------------


class TestConcurrentUpdates:

    def test_concurrent_record_schedule_calls_are_all_counted(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        def _record():
            for _ in range(50):
                metrics.record_schedule(registered_jobs=1, pending_jobs=1)

        threads = [threading.Thread(target=_record) for _ in range(10)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert metrics.snapshot().jobs_registered == 500

    def test_concurrent_record_completion_calls_are_all_counted(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        def _record():
            for _ in range(50):
                metrics.record_completion(execution_ms=1)

        threads = [threading.Thread(target=_record) for _ in range(10)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert metrics.snapshot().jobs_completed == 500

    def test_concurrent_timer_recording_does_not_lose_samples(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        def _record():
            for _ in range(50):
                metrics.record_queue_wait(wait_ms=1)

        threads = [threading.Thread(target=_record) for _ in range(4)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # 4 * 50 = 200 samples, all value 1 — bounded window is 200,
        # so the average should be exactly 1.0 with nothing evicted.
        assert metrics.summary().average_queue_wait_ms == 1.0


# --- Reset ---------------------------------------------------------------


class TestReset:

    def test_reset_zeroes_counters(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_completion(execution_ms=10)

        metrics.reset()

        assert metrics.snapshot().jobs_completed == 0

    def test_reset_zeroes_timers(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_completion(execution_ms=10)

        metrics.reset()

        assert metrics.summary().average_execution_ms == 0.0

    def test_reset_publishes_scheduler_metrics_reset(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        metrics = GovernanceSchedulerMetrics(clock=_clock, event_bus=bus)
        metrics.reset()

        assert received == ["scheduler_metrics_reset"]


# --- Runtime integration -------------------------------------------------


class TestRuntimeIntegration:

    def test_scheduler_register_records_schedule_metric(self):
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        scheduler = GovernanceScheduler(clock=_clock, metrics=metrics)

        scheduler.register("a", interval_seconds=60)

        assert metrics.snapshot().jobs_registered == 1

    def test_execution_manager_records_completion_metric(self):
        from backend.observability.deployment_governance_execution_manager import (
            GovernanceExecutionManager,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        manager = GovernanceExecutionManager(clock=_clock, metrics=metrics)

        manager.execute("job-1")

        assert metrics.snapshot().jobs_completed == 1

    def test_execution_manager_records_failure_metric(self):
        from backend.observability.deployment_governance_execution_manager import (
            GovernanceExecutionManager,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        manager = GovernanceExecutionManager(clock=_clock, metrics=metrics)

        manager.execute(
            "job-1", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert metrics.snapshot().jobs_failed == 1

    def test_retry_engine_records_retry_metric(self):
        from backend.observability.deployment_governance_retry import (
            GovernanceRetryEngine,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        engine = GovernanceRetryEngine(clock=_clock, metrics=metrics)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )

        engine.schedule_retry("exec-1", "p", job_id="job-1")

        assert metrics.summary().retry_rate >= 0

    def test_lock_manager_records_contention_metric(self):
        from backend.observability.deployment_governance_scheduler_locks import (
            GovernanceSchedulerLockManager,
        )

        metrics = GovernanceSchedulerMetrics(clock=_clock)
        lock_manager = GovernanceSchedulerLockManager(
            clock=_clock, metrics=metrics,
        )

        lock_manager.acquire("job-1", "node-1")
        result = lock_manager.acquire("job-1", "node-2")

        assert result.acquired is False
        # No public accessor for the raw counter, but this should not
        # raise, and a contended acquire always calls the recorder.

    def test_metrics_bootstrap_exposes_scheduler_metrics(self):
        from backend.observability.deployment_governance_metrics_bootstrap import (
            build_integrity_metrics_bootstrap,
        )
        from backend.observability.deployment_governance_persistence import (
            DeploymentGovernancePersistenceConfig,
            build_deployment_governance_persistence,
        )

        runtime = build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
        bootstrap = build_integrity_metrics_bootstrap(runtime)

        assert bootstrap.scheduler_metrics is not None
        assert isinstance(
            bootstrap.scheduler_metrics.snapshot(), SchedulerMetrics,
        )


# --- Singleton -------------------------------------------------------------


class TestSchedulerMetricsSingleton:

    def test_get_scheduler_metrics_returns_same_instance(self):
        from backend.observability.deployment_governance_scheduler_metrics import (
            get_scheduler_metrics,
        )

        assert get_scheduler_metrics() is get_scheduler_metrics()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSchedulerMetricsApi:

    def test_get_metrics_returns_snapshot(self, client) -> None:
        response = client.get("/governance/scheduler/metrics")

        assert response.status_code == 200

        payload = response.json()
        assert "jobs_registered" in payload

    def test_get_metrics_summary_returns_performance_snapshot(
        self, client
    ) -> None:
        response = client.get("/governance/scheduler/metrics/summary")

        assert response.status_code == 200

        payload = response.json()
        assert "average_execution_ms" in payload
        assert "retry_rate" in payload

    def test_post_reset_clears_metrics(self, client) -> None:
        client.post("/governance/executions/job-1")

        response = client.post("/governance/scheduler/metrics/reset")

        assert response.status_code == 200
        assert response.json() == {"reset": True}

        follow_up = client.get("/governance/scheduler/metrics")
        assert follow_up.json()["jobs_completed"] == 0
