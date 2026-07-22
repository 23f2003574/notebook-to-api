from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_execution_manager import (
    GovernanceExecutionManager,
)
from backend.observability.deployment_governance_job_registry import (
    GovernanceJobRegistry,
)
from backend.observability.deployment_governance_retry import (
    GovernanceRetryEngine,
)
from backend.observability.deployment_governance_scheduler import (
    GovernanceScheduler,
    SchedulerStatus,
)
from backend.observability.deployment_governance_scheduler_dashboard import (
    GovernanceSchedulerDashboard,
    SchedulerDashboard,
    SchedulerDashboardSummary,
)
from backend.observability.deployment_governance_scheduler_locks import (
    GovernanceSchedulerLockManager,
)
from backend.observability.deployment_governance_scheduler_metrics import (
    GovernanceSchedulerMetrics,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The dashboard aggregates a whole family of process-wide singletons
    (scheduler, job registry, execution manager, retry engine, lock
    manager, metrics), so tests that touch the shared dashboard
    (directly or via the API) must reset all of them.
    """

    from backend.observability.deployment_governance_execution_manager import (
        get_execution_manager,
    )
    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_retry import (
        get_retry_engine,
    )
    from backend.observability.deployment_governance_scheduler_locks import (
        get_scheduler_lock_manager,
    )
    from backend.observability.deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )
    from backend.observability.deployment_governance_trigger_engine import (
        get_trigger_engine,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_execution_manager().cleanup()
        get_scheduler_metrics().reset()

        retry_engine = get_retry_engine()
        for attempt in retry_engine.pending():
            retry_engine.cancel_retry(attempt.execution_id)

        lock_manager = get_scheduler_lock_manager()
        for lock in lock_manager.list():
            lock_manager.release(lock.job_id, lock.owner_id)

        get_trigger_engine().clear()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


def _build_components(clock=_clock):
    job_registry = GovernanceJobRegistry(clock=clock)
    execution_manager = GovernanceExecutionManager(clock=clock)
    retry_engine = GovernanceRetryEngine(clock=clock)
    lock_manager = GovernanceSchedulerLockManager(clock=clock)
    metrics = GovernanceSchedulerMetrics(clock=clock)
    scheduler = GovernanceScheduler(clock=clock, job_registry=job_registry)

    return job_registry, execution_manager, retry_engine, lock_manager, metrics, scheduler


# --- Models --------------------------------------------------------------


class TestSchedulerDashboard:

    def test_rejects_naive_generated_at(self):
        with pytest.raises(
            ValueError, match="generated_at must be timezone-aware"
        ):
            SchedulerDashboard(
                generated_at=datetime(2026, 7, 21, 12, 0, 0),
                scheduler=SchedulerStatus(
                    running=True, active_jobs=0, next_execution=None,
                ),
                metrics=GovernanceSchedulerMetrics(clock=_clock).snapshot(),
                active_jobs=0, pending_jobs=0, running_jobs=0,
                failed_jobs=0,
            )

    def test_rejects_negative_counts(self):
        with pytest.raises(ValueError, match="active_jobs must be >= 0"):
            SchedulerDashboard(
                generated_at=BASE_TIME,
                scheduler=SchedulerStatus(
                    running=True, active_jobs=0, next_execution=None,
                ),
                metrics=GovernanceSchedulerMetrics(clock=_clock).snapshot(),
                active_jobs=-1, pending_jobs=0, running_jobs=0,
                failed_jobs=0,
            )

    def test_to_dict(self):
        status = SchedulerStatus(
            running=True, active_jobs=1, next_execution=None,
        )
        metrics_snapshot = GovernanceSchedulerMetrics(clock=_clock).snapshot()

        dashboard = SchedulerDashboard(
            generated_at=BASE_TIME, scheduler=status,
            metrics=metrics_snapshot, active_jobs=1, pending_jobs=2,
            running_jobs=1, failed_jobs=0,
        )

        payload = dashboard.to_dict()
        assert payload["generated_at"] == BASE_TIME.isoformat()
        assert payload["scheduler"] == status.to_dict()
        assert payload["active_jobs"] == 1
        assert payload["pending_jobs"] == 2


class TestSchedulerDashboardSummary:

    def test_rejects_out_of_range_utilization(self):
        with pytest.raises(
            ValueError, match="utilization must be between 0 and 1"
        ):
            SchedulerDashboardSummary(
                healthy=True, total_jobs=0, utilization=1.5,
                success_rate=0, next_execution=None,
            )

    def test_to_dict(self):
        summary = SchedulerDashboardSummary(
            healthy=True, total_jobs=5, utilization=0.5,
            success_rate=1.0, next_execution=BASE_TIME,
        )

        assert summary.to_dict() == {
            "healthy": True,
            "total_jobs": 5,
            "utilization": 0.5,
            "success_rate": 1.0,
            "next_execution": BASE_TIME.isoformat(),
        }


# --- Dashboard generation -------------------------------------------


class TestDashboardGeneration:

    def test_dashboard_with_nothing_wired(self):
        dashboard_service = GovernanceSchedulerDashboard(clock=_clock)

        dashboard = dashboard_service.dashboard()

        assert dashboard.scheduler.running is False
        assert dashboard.active_jobs == 0
        assert dashboard.generated_at == BASE_TIME

    def test_dashboard_reflects_scheduler_status(self):
        _, _, _, _, _, scheduler = _build_components()
        scheduler.start()

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, scheduler=scheduler,
        )

        assert dashboard_service.dashboard().scheduler.running is True

    def test_dashboard_active_jobs_reflects_execution_manager(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        seen = {}

        def _check():
            seen["active"] = (
                GovernanceSchedulerDashboard(
                    clock=_clock, execution_manager=execution_manager,
                ).dashboard().active_jobs
            )

        execution_manager.execute("job-1", _check)

        assert seen["active"] == 1

    def test_dashboard_running_jobs_subset_of_active(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        seen = {}

        def _check():
            dash = GovernanceSchedulerDashboard(
                clock=_clock, execution_manager=execution_manager,
            ).dashboard()
            seen["active"] = dash.active_jobs
            seen["running"] = dash.running_jobs

        execution_manager.execute("job-1", _check)

        assert seen["running"] == 1
        assert seen["active"] == 1

    def test_dashboard_failed_jobs_from_metrics(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_failure(execution_ms=1)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, metrics=metrics,
        )

        assert dashboard_service.dashboard().failed_jobs == 1

    def test_refresh_produces_equivalent_dashboard(self):
        dashboard_service = GovernanceSchedulerDashboard(clock=_clock)

        first = dashboard_service.dashboard()
        second = dashboard_service.refresh()

        assert first == second


# --- Summary generation -------------------------------------------------


class TestSummaryGeneration:

    def test_summary_with_nothing_wired(self):
        dashboard_service = GovernanceSchedulerDashboard(clock=_clock)

        summary = dashboard_service.summary()

        assert summary.total_jobs == 0
        assert summary.success_rate == 0.0
        assert summary.healthy is False

    def test_summary_total_jobs_from_registry(self):
        job_registry = GovernanceJobRegistry(clock=_clock)
        job_registry.register("job-1", "a")
        job_registry.register("job-2", "b")

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, job_registry=job_registry,
        )

        assert dashboard_service.summary().total_jobs == 2

    def test_summary_success_rate_computed(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_completion(execution_ms=1)
        metrics.record_completion(execution_ms=1)
        metrics.record_failure(execution_ms=1)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, metrics=metrics,
        )

        assert dashboard_service.summary().success_rate == (2 / 3)

    def test_summary_healthy_when_running_and_no_more_failures_than_successes(
        self,
    ):
        _, _, _, _, metrics, scheduler = _build_components()
        scheduler.start()
        metrics.record_completion(execution_ms=1)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, scheduler=scheduler, metrics=metrics,
        )

        assert dashboard_service.summary().healthy is True

    def test_summary_unhealthy_when_more_failures_than_successes(self):
        _, _, _, _, metrics, scheduler = _build_components()
        scheduler.start()
        metrics.record_failure(execution_ms=1)
        metrics.record_failure(execution_ms=1)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, scheduler=scheduler, metrics=metrics,
        )

        assert dashboard_service.summary().healthy is False

    def test_summary_unhealthy_when_scheduler_not_running(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, metrics=metrics,
        )

        assert dashboard_service.summary().healthy is False

    def test_summary_utilization_from_metrics(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_schedule(registered_jobs=4, pending_jobs=4)
        metrics.record_dispatch(count=2, active_jobs=2)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, metrics=metrics,
        )

        assert dashboard_service.summary().utilization == 0.5

    def test_summary_next_execution_from_scheduler(self):
        job_registry = GovernanceJobRegistry(clock=_clock)
        scheduler = GovernanceScheduler(clock=_clock, job_registry=job_registry)
        scheduler.register("a", interval_seconds=60)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, scheduler=scheduler,
        )

        assert dashboard_service.summary().next_execution == (
            BASE_TIME + timedelta(seconds=60)
        )


# --- Execution aggregation -------------------------------------------


class TestExecutionAggregation:

    def test_executions_with_no_execution_manager(self):
        dashboard_service = GovernanceSchedulerDashboard(clock=_clock)

        assert dashboard_service.executions() == ()

    def test_executions_reflects_history(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        execution_manager.execute("job-1")
        execution_manager.execute("job-2")

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, execution_manager=execution_manager,
        )

        assert len(dashboard_service.executions()) == 2

    def test_executions_are_newest_first(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        first = execution_manager.execute("job-1")
        second = execution_manager.execute("job-1")

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, execution_manager=execution_manager,
        )

        executions = dashboard_service.executions()
        assert executions[0].execution_id == second.execution_id
        assert executions[1].execution_id == first.execution_id


# --- Retry aggregation -----------------------------------------------


class TestRetryAggregation:

    def test_retries_with_no_retry_engine(self):
        dashboard_service = GovernanceSchedulerDashboard(clock=_clock)

        assert dashboard_service.retries() == ()

    def test_retries_reflects_pending(self):
        retry_engine = GovernanceRetryEngine(clock=_clock)
        retry_engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        retry_engine.schedule_retry("exec-1", "p", job_id="job-1")

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, retry_engine=retry_engine,
        )

        assert len(dashboard_service.retries()) == 1


# --- Lock aggregation ----------------------------------------------------


class TestLockAggregation:

    def test_locks_with_no_lock_manager(self):
        dashboard_service = GovernanceSchedulerDashboard(clock=_clock)

        assert dashboard_service.locks() == ()

    def test_locks_reflects_stored_locks(self):
        lock_manager = GovernanceSchedulerLockManager(clock=_clock)
        lock_manager.acquire("job-1", "node-1")

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, lock_manager=lock_manager,
        )

        assert len(dashboard_service.locks()) == 1


# --- Metrics aggregation -------------------------------------------------


class TestMetricsAggregation:

    def test_metrics_with_no_metrics_wired(self):
        dashboard_service = GovernanceSchedulerDashboard(clock=_clock)

        snapshot = dashboard_service.metrics()

        assert snapshot.jobs_registered == 0

    def test_metrics_reflects_recorded_data(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        metrics.record_completion(execution_ms=1)

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, metrics=metrics,
        )

        assert dashboard_service.metrics().jobs_completed == 1


# --- Deterministic ordering -----------------------------------------------


class TestDeterministicOrdering:

    def test_jobs_ordering_matches_registry(self):
        job_registry = GovernanceJobRegistry(clock=_clock)
        job_registry.register("job-z", "b", namespace="ns")
        job_registry.register("job-a", "a", namespace="ns")

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, job_registry=job_registry,
        )

        expected = [job.job_id for job in job_registry.list()]
        actual = [job.job_id for job in dashboard_service.jobs()]

        assert actual == expected == ["job-a", "job-z"]

    def test_repeated_calls_are_consistent_with_no_intervening_changes(self):
        job_registry = GovernanceJobRegistry(clock=_clock)
        job_registry.register("job-1", "a")

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, job_registry=job_registry,
        )

        first = dashboard_service.jobs()
        second = dashboard_service.jobs()

        assert first == second


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_dashboard_publishes_generated_event(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, event_bus=bus,
        )
        dashboard_service.dashboard()

        assert received == ["scheduler_dashboard_generated"]

    def test_refresh_publishes_refreshed_event(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        dashboard_service = GovernanceSchedulerDashboard(
            clock=_clock, event_bus=bus,
        )
        dashboard_service.refresh()

        assert received == ["scheduler_dashboard_refreshed"]


# --- Singleton -------------------------------------------------------------


class TestSchedulerDashboardSingleton:

    def test_get_scheduler_dashboard_returns_same_instance(self):
        from backend.observability.deployment_governance_scheduler_dashboard import (
            get_scheduler_dashboard,
        )

        assert (
            get_scheduler_dashboard() is get_scheduler_dashboard()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSchedulerDashboardApi:

    def test_get_dashboard(self, client) -> None:
        response = client.get("/governance/scheduler/dashboard")

        assert response.status_code == 200

        payload = response.json()
        assert "scheduler" in payload
        assert "metrics" in payload

    def test_get_dashboard_summary(self, client) -> None:
        response = client.get("/governance/scheduler/dashboard/summary")

        assert response.status_code == 200

        payload = response.json()
        assert "healthy" in payload
        assert "total_jobs" in payload

    def test_get_dashboard_jobs(self, client) -> None:
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )

        get_job_registry().register("job-1", "a")

        response = client.get("/governance/scheduler/dashboard/jobs")

        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_get_dashboard_executions(self, client) -> None:
        client.post("/governance/executions/job-1")

        response = client.get("/governance/scheduler/dashboard/executions")

        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_get_dashboard_retries(self, client) -> None:
        response = client.get("/governance/scheduler/dashboard/retries")

        assert response.status_code == 200
        assert response.json() == []

    def test_get_dashboard_locks(self, client) -> None:
        client.post(
            "/governance/locks/job-1/acquire",
            params={"owner_id": "node-1"},
        )

        response = client.get("/governance/scheduler/dashboard/locks")

        assert response.status_code == 200
        assert len(response.json()) == 1
