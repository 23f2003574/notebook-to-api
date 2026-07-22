from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_scheduler import (
    GovernanceScheduler,
    ScheduledJob,
    SchedulerStatus,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The lifecycle manager and governance scheduler are both
    process-wide singletons, so tests that touch them (directly or
    via the API) must not leak state into other tests. The scheduler
    has no bulk "clear" method (mirroring how the recovery manager's
    built-in plans are left alone) so every registered job is
    unregistered individually through the public API instead.
    """

    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_scheduler import (
        get_scheduler,
    )

    def _reset():
        get_lifecycle_manager().shutdown()

        scheduler = get_scheduler()

        for job in scheduler.jobs():
            scheduler.unregister(job.job_id)

        scheduler.stop()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestScheduledJob:

    def test_rejects_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must not be empty"):
            ScheduledJob(
                job_id="", name="a", interval_seconds=1, enabled=True,
                created_at=BASE_TIME,
            )

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            ScheduledJob(
                job_id="1", name="", interval_seconds=1, enabled=True,
                created_at=BASE_TIME,
            )

    def test_rejects_non_positive_interval(self):
        with pytest.raises(
            ValueError, match="interval_seconds must be greater than zero"
        ):
            ScheduledJob(
                job_id="1", name="a", interval_seconds=0, enabled=True,
                created_at=BASE_TIME,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            ScheduledJob(
                job_id="1", name="a", interval_seconds=1, enabled=True,
                created_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict(self):
        job = ScheduledJob(
            job_id="1", name="a", interval_seconds=30, enabled=False,
            created_at=BASE_TIME,
        )

        assert job.to_dict() == {
            "job_id": "1",
            "name": "a",
            "interval_seconds": 30,
            "enabled": False,
            "created_at": BASE_TIME.isoformat(),
        }


class TestSchedulerStatus:

    def test_rejects_negative_active_jobs(self):
        with pytest.raises(ValueError, match="active_jobs must be >= 0"):
            SchedulerStatus(running=True, active_jobs=-1, next_execution=None)

    def test_rejects_naive_next_execution(self):
        with pytest.raises(
            ValueError, match="next_execution must be timezone-aware"
        ):
            SchedulerStatus(
                running=True, active_jobs=0,
                next_execution=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict_with_next_execution(self):
        status = SchedulerStatus(
            running=True, active_jobs=2, next_execution=BASE_TIME,
        )

        assert status.to_dict() == {
            "running": True,
            "active_jobs": 2,
            "next_execution": BASE_TIME.isoformat(),
        }

    def test_to_dict_without_next_execution(self):
        status = SchedulerStatus(
            running=False, active_jobs=0, next_execution=None,
        )

        assert status.to_dict()["next_execution"] is None


# --- Startup / shutdown ----------------------------------------------


class TestSchedulerStartup:

    def test_start_marks_running(self):
        scheduler = GovernanceScheduler(clock=_clock)

        scheduler.start()

        assert scheduler.status().running is True

    def test_start_is_idempotent(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceScheduler(clock=_clock, event_bus=bus)

        scheduler.start()
        scheduler.start()

        assert received == ["scheduler_started"]


class TestSchedulerShutdown:

    def test_stop_marks_not_running(self):
        scheduler = GovernanceScheduler(clock=_clock)
        scheduler.start()

        scheduler.stop()

        assert scheduler.status().running is False

    def test_stop_is_idempotent(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceScheduler(clock=_clock, event_bus=bus)

        scheduler.stop()
        scheduler.stop()

        assert received == []

    def test_stop_leaves_registered_jobs_untouched(self):
        scheduler = GovernanceScheduler(clock=_clock)
        scheduler.start()
        scheduler.register("a", interval_seconds=60)

        scheduler.stop()

        assert [job.name for job in scheduler.jobs()] == ["a"]


# --- Job registration --------------------------------------------------


class TestJobRegistration:

    def test_register_returns_job(self):
        scheduler = GovernanceScheduler(clock=_clock)

        job = scheduler.register("a", interval_seconds=60)

        assert job.name == "a"
        assert job.interval_seconds == 60
        assert job.enabled is True

    def test_registered_job_appears_in_jobs(self):
        scheduler = GovernanceScheduler(clock=_clock)
        scheduler.register("a", interval_seconds=60)

        assert [job.name for job in scheduler.jobs()] == ["a"]

    def test_register_assigns_unique_job_ids(self):
        scheduler = GovernanceScheduler(clock=_clock)

        first = scheduler.register("a", interval_seconds=60)
        second = scheduler.register("b", interval_seconds=60)

        assert first.job_id != second.job_id

    def test_disabled_registration_has_no_pending_execution(self):
        scheduler = GovernanceScheduler(clock=_clock)

        job = scheduler.register("a", interval_seconds=60, enabled=False)

        assert scheduler.status().next_execution is None
        assert job.enabled is False


# --- Duplicate rejection -------------------------------------------------


def test_duplicate_job_name_rejected():
    scheduler = GovernanceScheduler(clock=_clock)
    scheduler.register("a", interval_seconds=60)

    with pytest.raises(ValueError, match="already registered"):
        scheduler.register("a", interval_seconds=30)


# --- Unregister ----------------------------------------------------------


class TestUnregister:

    def test_unregister_removes_job(self):
        scheduler = GovernanceScheduler(clock=_clock)
        job = scheduler.register("a", interval_seconds=60)

        scheduler.unregister(job.job_id)

        assert scheduler.jobs() == ()

    def test_unregister_unknown_job_raises(self):
        scheduler = GovernanceScheduler(clock=_clock)

        with pytest.raises(KeyError):
            scheduler.unregister("ghost")

    def test_unregister_frees_the_name_for_reuse(self):
        scheduler = GovernanceScheduler(clock=_clock)
        job = scheduler.register("a", interval_seconds=60)
        scheduler.unregister(job.job_id)

        second = scheduler.register("a", interval_seconds=30)

        assert second.name == "a"


# --- Schedule / cancel ----------------------------------------------------


class TestScheduleAndCancel:

    def test_schedule_returns_next_execution_time(self):
        scheduler = GovernanceScheduler(clock=_clock)
        job = scheduler.register("a", interval_seconds=60)

        next_run = scheduler.schedule(job.job_id)

        assert next_run == BASE_TIME + timedelta(seconds=60)

    def test_schedule_unknown_job_raises(self):
        scheduler = GovernanceScheduler(clock=_clock)

        with pytest.raises(KeyError):
            scheduler.schedule("ghost")

    def test_cancel_clears_pending_execution(self):
        scheduler = GovernanceScheduler(clock=_clock)
        job = scheduler.register("a", interval_seconds=60)

        scheduler.cancel(job.job_id)

        assert scheduler.status().next_execution is None

    def test_cancel_unknown_job_raises(self):
        scheduler = GovernanceScheduler(clock=_clock)

        with pytest.raises(KeyError):
            scheduler.cancel("ghost")

    def test_cancel_does_not_unregister_the_job(self):
        scheduler = GovernanceScheduler(clock=_clock)
        job = scheduler.register("a", interval_seconds=60)

        scheduler.cancel(job.job_id)

        assert [j.name for j in scheduler.jobs()] == ["a"]

    def test_schedule_can_reschedule_a_cancelled_job(self):
        scheduler = GovernanceScheduler(clock=_clock)
        job = scheduler.register("a", interval_seconds=60)
        scheduler.cancel(job.job_id)

        scheduler.schedule(job.job_id)

        assert scheduler.status().next_execution == (
            BASE_TIME + timedelta(seconds=60)
        )


# --- Deterministic ordering -----------------------------------------------


class TestDeterministicOrdering:

    def test_jobs_ordered_by_next_execution_then_job_id(self):
        scheduler = GovernanceScheduler(clock=_clock)
        far = scheduler.register("far", interval_seconds=120)
        near = scheduler.register("near", interval_seconds=10)

        assert [job.job_id for job in scheduler.jobs()] == [
            near.job_id, far.job_id,
        ]

    def test_jobs_with_no_pending_execution_sort_last(self):
        scheduler = GovernanceScheduler(clock=_clock)
        scheduled = scheduler.register("scheduled", interval_seconds=10)
        unscheduled = scheduler.register(
            "unscheduled", interval_seconds=10, enabled=False,
        )

        assert [job.job_id for job in scheduler.jobs()] == [
            scheduled.job_id, unscheduled.job_id,
        ]


# --- Status ----------------------------------------------------------


class TestStatus:

    def test_status_reports_active_job_count(self):
        scheduler = GovernanceScheduler(clock=_clock)
        scheduler.register("a", interval_seconds=60)
        scheduler.register("b", interval_seconds=60)

        assert scheduler.status().active_jobs == 2

    def test_status_reports_soonest_next_execution(self):
        scheduler = GovernanceScheduler(clock=_clock)
        scheduler.register("far", interval_seconds=120)
        scheduler.register("near", interval_seconds=10)

        assert scheduler.status().next_execution == (
            BASE_TIME + timedelta(seconds=10)
        )

    def test_status_next_execution_none_with_no_jobs(self):
        scheduler = GovernanceScheduler(clock=_clock)

        assert scheduler.status().next_execution is None


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_registration_publishes_job_registered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceScheduler(clock=_clock, event_bus=bus)
        scheduler.register("a", interval_seconds=60)

        assert received == ["job_registered"]

    def test_unregister_publishes_job_unregistered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceScheduler(clock=_clock, event_bus=bus)
        job = scheduler.register("a", interval_seconds=60)
        received.clear()

        scheduler.unregister(job.job_id)

        assert received == ["job_unregistered"]

    def test_start_stop_publish_lifecycle_events_in_order(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        scheduler = GovernanceScheduler(clock=_clock, event_bus=bus)
        scheduler.start()
        scheduler.stop()

        assert received == ["scheduler_started", "scheduler_stopped"]


# --- Singleton -------------------------------------------------------------


class TestSchedulerSingleton:

    def test_get_scheduler_returns_same_instance(self):
        from backend.observability.deployment_governance_scheduler import (
            get_scheduler,
        )

        assert get_scheduler() is get_scheduler()

    def test_default_scheduler_is_wired_into_lifecycle_manager(self):
        from backend.observability.deployment_governance_lifecycle import (
            get_lifecycle_manager,
        )

        names = {c.name for c in get_lifecycle_manager().status()}

        assert "scheduler" in names


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSchedulerApi:

    def test_get_scheduler_returns_status(self, client) -> None:
        response = client.get("/governance/scheduler")

        assert response.status_code == 200

        payload = response.json()

        assert "running" in payload
        assert "active_jobs" in payload

    def test_get_scheduler_jobs_returns_empty_list_initially(
        self, client
    ) -> None:
        response = client.get("/governance/scheduler/jobs")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_scheduler_start_marks_running(self, client) -> None:
        response = client.post("/governance/scheduler/start")

        assert response.status_code == 200
        assert response.json()["running"] is True

    def test_post_scheduler_stop_marks_not_running(self, client) -> None:
        client.post("/governance/scheduler/start")

        response = client.post("/governance/scheduler/stop")

        assert response.status_code == 200
        assert response.json()["running"] is False
