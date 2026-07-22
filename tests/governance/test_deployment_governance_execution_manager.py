from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_execution_manager import (
    ExecutionResult,
    GovernanceExecutionManager,
    JobExecution,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


class _AdvancingClock:
    """
    A clock that returns BASE_TIME, then BASE_TIME + step on every
    subsequent call — lets tests observe a nonzero duration_ms without
    depending on wall-clock time.
    """

    def __init__(self, step_ms: int = 250) -> None:
        self._calls = 0
        self._step = timedelta(milliseconds=step_ms)

    def __call__(self) -> datetime:
        at = BASE_TIME + self._step * self._calls
        self._calls += 1
        return at


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The execution manager, trigger engine, job registry, and scheduler
    are all process-wide singletons wired together, so tests that
    touch any of them (directly or via the API) must not leak state
    into other tests. The execution manager's lifetime counters
    (status()) are not reset by cleanup() by design (they are meant to
    survive history pruning, like real metrics counters), so tests
    that assert on exact counts use their own fresh manager instance
    rather than the shared singleton.
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
    from backend.observability.deployment_governance_trigger_engine import (
        get_trigger_engine,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_execution_manager().cleanup()
        get_trigger_engine().clear()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestJobExecution:

    def test_rejects_empty_execution_id(self):
        with pytest.raises(
            ValueError, match="execution_id must not be empty"
        ):
            JobExecution(
                execution_id="", job_id="job-1", started_at=BASE_TIME,
                status="RUNNING",
            )

    def test_rejects_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must not be empty"):
            JobExecution(
                execution_id="e-1", job_id="", started_at=BASE_TIME,
                status="RUNNING",
            )

    def test_rejects_naive_started_at(self):
        with pytest.raises(
            ValueError, match="started_at must be timezone-aware"
        ):
            JobExecution(
                execution_id="e-1", job_id="job-1",
                started_at=datetime(2026, 7, 21, 12, 0, 0),
                status="RUNNING",
            )

    def test_rejects_unknown_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            JobExecution(
                execution_id="e-1", job_id="job-1", started_at=BASE_TIME,
                status="TELEPORTING",
            )

    def test_to_dict(self):
        execution = JobExecution(
            execution_id="e-1", job_id="job-1", started_at=BASE_TIME,
            status="RUNNING",
        )

        assert execution.to_dict() == {
            "execution_id": "e-1",
            "job_id": "job-1",
            "started_at": BASE_TIME.isoformat(),
            "status": "RUNNING",
        }


class TestExecutionResult:

    def test_rejects_non_terminal_status(self):
        with pytest.raises(
            ValueError, match="status must be a terminal state"
        ):
            ExecutionResult(
                execution_id="e-1", status="RUNNING",
                completed_at=BASE_TIME, duration_ms=0, error=None,
            )

    def test_rejects_naive_completed_at(self):
        with pytest.raises(
            ValueError, match="completed_at must be timezone-aware"
        ):
            ExecutionResult(
                execution_id="e-1", status="SUCCEEDED",
                completed_at=datetime(2026, 7, 21, 12, 0, 0),
                duration_ms=0, error=None,
            )

    def test_rejects_negative_duration(self):
        with pytest.raises(ValueError, match="duration_ms must be >= 0"):
            ExecutionResult(
                execution_id="e-1", status="SUCCEEDED",
                completed_at=BASE_TIME, duration_ms=-1, error=None,
            )

    def test_rejects_failed_without_error(self):
        with pytest.raises(
            ValueError, match="error must be set when status is 'FAILED'"
        ):
            ExecutionResult(
                execution_id="e-1", status="FAILED",
                completed_at=BASE_TIME, duration_ms=0, error=None,
            )

    def test_rejects_error_when_not_failed(self):
        with pytest.raises(
            ValueError, match="error must not be set unless"
        ):
            ExecutionResult(
                execution_id="e-1", status="SUCCEEDED",
                completed_at=BASE_TIME, duration_ms=0, error="boom",
            )

    def test_to_dict(self):
        result = ExecutionResult(
            execution_id="e-1", status="FAILED", completed_at=BASE_TIME,
            duration_ms=42, error="boom",
        )

        assert result.to_dict() == {
            "execution_id": "e-1",
            "status": "FAILED",
            "completed_at": BASE_TIME.isoformat(),
            "duration_ms": 42,
            "error": "boom",
        }


# --- Successful execution -------------------------------------------------


class TestSuccessfulExecution:

    def test_execute_returns_succeeded_result(self):
        manager = GovernanceExecutionManager(clock=_clock)

        result = manager.execute("job-1")

        assert result.status == "SUCCEEDED"
        assert result.error is None

    def test_execute_calls_the_given_callable(self):
        manager = GovernanceExecutionManager(clock=_clock)
        calls = []

        manager.execute("job-1", lambda: calls.append("ran"))

        assert calls == ["ran"]

    def test_duration_ms_reflects_elapsed_clock_time(self):
        manager = GovernanceExecutionManager(clock=_AdvancingClock(250))

        result = manager.execute("job-1")

        assert result.duration_ms > 0

    def test_successful_execution_appears_in_history(self):
        manager = GovernanceExecutionManager(clock=_clock)

        result = manager.execute("job-1")

        assert manager.history()[0].execution_id == result.execution_id

    def test_execution_no_longer_active_after_completion(self):
        manager = GovernanceExecutionManager(clock=_clock)

        manager.execute("job-1")

        assert manager.active() == ()


# --- Failed execution ------------------------------------------------


class TestFailedExecution:

    def test_exception_is_captured_as_failed(self):
        manager = GovernanceExecutionManager(clock=_clock)

        def _boom():
            raise RuntimeError("kaboom")

        result = manager.execute("job-1", _boom)

        assert result.status == "FAILED"
        assert result.error == "kaboom"

    def test_failed_execution_does_not_propagate(self):
        manager = GovernanceExecutionManager(clock=_clock)

        def _boom():
            raise RuntimeError("kaboom")

        # Should not raise.
        manager.execute("job-1", _boom)

    def test_failed_execution_frees_up_the_job_id(self):
        manager = GovernanceExecutionManager(clock=_clock)

        def _boom():
            raise RuntimeError("kaboom")

        manager.execute("job-1", _boom)

        # A second execution of the same job_id must not be treated as
        # a duplicate now that the first one has completed.
        result = manager.execute("job-1")

        assert result.status == "SUCCEEDED"


# --- Duplicate execution prevention -------------------------------------


class TestDuplicateExecutionPrevention:

    def test_nested_execution_of_the_same_job_is_rejected(self):
        manager = GovernanceExecutionManager(clock=_clock)

        def _reentrant():
            with pytest.raises(ValueError, match="already executing"):
                manager.execute("job-1")

        manager.execute("job-1", _reentrant)

    def test_different_job_ids_do_not_collide(self):
        manager = GovernanceExecutionManager(clock=_clock)
        results = []

        def _nested_other_job():
            results.append(manager.execute("job-2").status)

        outer = manager.execute("job-1", _nested_other_job)

        assert outer.status == "SUCCEEDED"
        assert results == ["SUCCEEDED"]


# --- Execution cancellation -----------------------------------------------


class TestExecutionCancellation:

    def test_cancel_marks_execution_cancelled(self):
        manager = GovernanceExecutionManager(clock=_clock)

        def _cancel_self():
            [active] = manager.active()
            manager.cancel(active.execution_id)

        result = manager.execute("job-1", _cancel_self)

        assert result.status == "CANCELLED"
        assert result.error is None

    def test_cancelled_execution_frees_up_the_job_id(self):
        manager = GovernanceExecutionManager(clock=_clock)

        def _cancel_self():
            [active] = manager.active()
            manager.cancel(active.execution_id)

        manager.execute("job-1", _cancel_self)

        result = manager.execute("job-1")

        assert result.status == "SUCCEEDED"

    def test_cancel_unknown_execution_raises(self):
        manager = GovernanceExecutionManager(clock=_clock)

        with pytest.raises(KeyError):
            manager.cancel("ghost")

    def test_cancel_already_completed_execution_raises(self):
        manager = GovernanceExecutionManager(clock=_clock)
        result = manager.execute("job-1")

        with pytest.raises(KeyError):
            manager.cancel(result.execution_id)


# --- Concurrent execution limit -------------------------------------------


class TestConcurrentExecutionLimit:

    def test_rejects_beyond_max_concurrent(self):
        manager = GovernanceExecutionManager(clock=_clock, max_concurrent=1)

        def _nested_second_job():
            with pytest.raises(ValueError, match="maximum concurrent"):
                manager.execute("job-2")

        manager.execute("job-1", _nested_second_job)

    def test_allows_up_to_the_configured_limit(self):
        manager = GovernanceExecutionManager(clock=_clock, max_concurrent=2)
        results = []

        def _nested_second_job():
            results.append(manager.execute("job-2").status)

        outer = manager.execute("job-1", _nested_second_job)

        assert outer.status == "SUCCEEDED"
        assert results == ["SUCCEEDED"]

    def test_rejects_non_positive_max_concurrent(self):
        with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
            GovernanceExecutionManager(clock=_clock, max_concurrent=0)


# --- Execution history ---------------------------------------------------


class TestExecutionHistory:

    def test_history_is_newest_first(self):
        manager = GovernanceExecutionManager(clock=_clock)
        first = manager.execute("job-1")
        second = manager.execute("job-1")

        history = manager.history()

        assert [r.execution_id for r in history] == [
            second.execution_id, first.execution_id,
        ]

    def test_history_filters_by_job_id(self):
        manager = GovernanceExecutionManager(clock=_clock)
        manager.execute("job-1")
        manager.execute("job-2")

        assert len(manager.history("job-1")) == 1

    def test_history_respects_limit(self):
        manager = GovernanceExecutionManager(clock=_clock)
        manager.execute("job-1")
        manager.execute("job-1")

        assert len(manager.history(limit=1)) == 1

    def test_cleanup_removes_history_and_returns_count(self):
        manager = GovernanceExecutionManager(clock=_clock)
        manager.execute("job-1")
        manager.execute("job-1")

        removed = manager.cleanup()

        assert removed == 2
        assert manager.history() == ()

    def test_cleanup_does_not_affect_status_counts(self):
        manager = GovernanceExecutionManager(clock=_clock)
        manager.execute("job-1")

        manager.cleanup()

        assert manager.status().succeeded_count == 1

    def test_status_counts_by_outcome(self):
        manager = GovernanceExecutionManager(clock=_clock)
        manager.execute("job-1")
        manager.execute("job-2", lambda: (_ for _ in ()).throw(
            RuntimeError("boom")
        ))

        status = manager.status()

        assert status.succeeded_count == 1
        assert status.failed_count == 1
        assert status.cancelled_count == 0

    def test_status_active_count(self):
        manager = GovernanceExecutionManager(clock=_clock)
        seen = {}

        def _check_active():
            seen["count"] = manager.status().active_count

        manager.execute("job-1", _check_active)

        assert seen["count"] == 1
        assert manager.status().active_count == 0


# --- Batch execution -------------------------------------------------


class TestBatchExecution:

    def test_execute_batch_runs_every_job(self):
        manager = GovernanceExecutionManager(clock=_clock)
        seen = []

        manager.execute_batch(
            ["job-b", "job-a"], run=lambda job_id: seen.append(job_id),
        )

        assert set(seen) == {"job-a", "job-b"}

    def test_execute_batch_deterministic_order(self):
        manager = GovernanceExecutionManager(clock=_clock)
        seen = []

        manager.execute_batch(
            ["job-z", "job-a", "job-m"],
            run=lambda job_id: seen.append(job_id),
        )

        assert seen == ["job-a", "job-m", "job-z"]

    def test_execute_batch_returns_results_in_order(self):
        manager = GovernanceExecutionManager(clock=_clock)

        results = manager.execute_batch(["job-b", "job-a"])

        assert len(results) == 2
        assert all(r.status == "SUCCEEDED" for r in results)

    def test_execute_batch_with_no_trigger_engine_and_no_ids_is_empty(self):
        manager = GovernanceExecutionManager(clock=_clock)

        assert manager.execute_batch() == ()

    def test_execute_batch_auto_discovers_due_jobs_from_trigger_engine(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        job_registry = GovernanceJobRegistry(clock=_clock)
        job_registry.register("job-1", "a")
        trigger_engine = GovernanceTriggerEngine(
            clock=_clock, job_registry=job_registry
        )
        trigger_engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )

        manager = GovernanceExecutionManager(
            clock=_clock, trigger_engine=trigger_engine,
        )

        results = manager.execute_batch()

        assert len(results) == 1


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_successful_execution_publishes_started_then_completed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceExecutionManager(clock=_clock, event_bus=bus)
        manager.execute("job-1")

        assert received == ["execution_started", "execution_completed"]

    def test_failed_execution_publishes_execution_failed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceExecutionManager(clock=_clock, event_bus=bus)
        manager.execute(
            "job-1",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert received == ["execution_started", "execution_failed"]

    def test_cancelled_execution_publishes_execution_cancelled(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceExecutionManager(clock=_clock, event_bus=bus)

        def _cancel_self():
            [active] = manager.active()
            manager.cancel(active.execution_id)

        manager.execute("job-1", _cancel_self)

        assert received == ["execution_started", "execution_cancelled"]

    def test_forwards_to_event_history_via_the_shared_bus(self):
        from backend.observability.deployment_governance_event_history import (
            get_event_history,
        )
        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )

        # get_event_history() (re-)wires its bus subscription on call,
        # not merely on import, so it must be called before execute()
        # publishes anything for those events to actually be captured.
        history = get_event_history()

        manager = GovernanceExecutionManager(
            clock=_clock, event_bus=get_event_bus(),
        )
        manager.execute("job-1")

        entries = history.latest(10)

        assert any(
            entry.event.event_type == "execution_completed"
            for entry in entries
        )

    def test_forwards_to_audit_service(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceExecutionManager(
            clock=_clock, audit_service=audit_service,
        )
        manager.execute("job-1")

        records = audit_service.query(
            AuditQuery(action="execution_completed")
        )

        assert len(records) == 1
        assert records[0].outcome == "success"
        assert records[0].resource == "job-1"


# --- Scheduler integration -------------------------------------------------


class _ControllableClock:
    """
    A mutable clock a test can advance explicitly — needed here since
    a fixed clock would mean "now" never actually reaches a job's
    next_run, so it would never become due.
    """

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class TestSchedulerIntegration:

    def _build(self, clock):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        job_registry = GovernanceJobRegistry(clock=clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=clock, job_registry=job_registry
        )
        scheduler = GovernanceScheduler(
            clock=clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )
        execution_manager = GovernanceExecutionManager(clock=clock)

        return scheduler, execution_manager

    def test_run_due_executes_and_reschedules_due_jobs(self):
        clock = _ControllableClock(BASE_TIME)
        scheduler, execution_manager = self._build(clock)
        scheduler.start()
        job = scheduler.register("a", interval_seconds=60)

        # Advance past the job's next_run (created_at + 60s) so it
        # actually becomes due.
        clock.now = BASE_TIME + timedelta(seconds=61)

        seen = []

        results = scheduler.run_due(
            execution_manager, run=lambda job_id: seen.append(job_id),
        )

        assert len(results) == 1
        assert results[0].status == "SUCCEEDED"
        assert seen == [job.job_id]

        # Rescheduled forward, past the moment it just ran.
        assert scheduler.status().next_execution == (
            clock.now + timedelta(seconds=60)
        )

    def test_run_due_is_a_no_op_when_scheduler_is_not_running(self):
        scheduler, execution_manager = self._build(_clock)
        scheduler.register("a", interval_seconds=60)

        results = scheduler.run_due(execution_manager)

        assert results == ()

    def test_run_due_skips_jobs_not_yet_due(self):
        scheduler, execution_manager = self._build(_clock)
        scheduler.start()
        scheduler.register("a", interval_seconds=3600)

        results = scheduler.run_due(execution_manager)

        assert results == ()


# --- Singleton -------------------------------------------------------------


class TestExecutionManagerSingleton:

    def test_get_execution_manager_returns_same_instance(self):
        from backend.observability.deployment_governance_execution_manager import (
            get_execution_manager,
        )

        assert get_execution_manager() is get_execution_manager()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceExecutionManagerApi:

    def test_get_executions_returns_empty_list_initially(
        self, client
    ) -> None:
        response = client.get("/governance/executions")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_execution_runs_a_job(self, client) -> None:
        response = client.post("/governance/executions/job-1")

        assert response.status_code == 200
        assert response.json()["status"] == "SUCCEEDED"

    def test_get_execution_by_id_after_completion(self, client) -> None:
        create_response = client.post("/governance/executions/job-1")
        execution_id = create_response.json()["execution_id"]

        response = client.get(f"/governance/executions/{execution_id}")

        assert response.status_code == 200
        assert response.json()["execution_id"] == execution_id

    def test_get_unknown_execution_returns_404(self, client) -> None:
        response = client.get("/governance/executions/ghost")

        assert response.status_code == 404

    def test_get_executions_active_returns_empty_after_completion(
        self, client
    ) -> None:
        client.post("/governance/executions/job-1")

        response = client.get("/governance/executions/active")

        assert response.status_code == 200
        assert response.json() == []

    def test_delete_unknown_execution_returns_404(self, client) -> None:
        response = client.delete("/governance/executions/ghost")

        assert response.status_code == 404

    def test_post_execution_history_reflects_the_run(self, client) -> None:
        client.post("/governance/executions/job-1")

        response = client.get("/governance/executions")

        assert response.status_code == 200
        assert len(response.json()) == 1
