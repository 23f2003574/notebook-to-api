from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_execution_manager import (
    GovernanceExecutionManager,
)
from backend.observability.deployment_governance_job_dependencies import (
    GovernanceJobDependencyManager,
)
from backend.observability.deployment_governance_job_persistence import (
    GovernanceJobPersistence,
)
from backend.observability.deployment_governance_job_registry import (
    GovernanceJobRegistry,
)
from backend.observability.deployment_governance_retry import (
    GovernanceRetryEngine,
)
from backend.observability.deployment_governance_scheduler import (
    GovernanceScheduler,
)
from backend.observability.deployment_governance_scheduler_bootstrap import (
    GovernanceSchedulerBootstrap,
    GovernanceSchedulerBootstrapError,
    SchedulerBootstrapReport,
    SchedulerBootstrapStatus,
    get_scheduler_bootstrap,
)
from backend.observability.deployment_governance_scheduler_locks import (
    GovernanceSchedulerLockManager,
)
from backend.observability.deployment_governance_scheduler_metrics import (
    GovernanceSchedulerMetrics,
)
from backend.observability.deployment_governance_scheduler_policy import (
    GovernanceSchedulerPolicyEngine,
)
from backend.observability.deployment_governance_trigger_engine import (
    GovernanceTriggerEngine,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _build_pipeline(clock=_clock, event_bus=None) -> dict:
    """
    Build one fresh, fully-wired set of Scheduler & Job Orchestration
    components, matching every other test file in this series
    (test_deployment_governance_job_persistence.py's _build_components,
    etc.): independent from the process-wide singletons, so most tests
    below can run in complete isolation from one another.
    """

    job_registry = GovernanceJobRegistry(clock=clock)
    trigger_engine = GovernanceTriggerEngine(
        clock=clock, job_registry=job_registry
    )
    dependency_manager = GovernanceJobDependencyManager(
        clock=clock, job_registry=job_registry
    )
    lock_manager = GovernanceSchedulerLockManager(clock=clock)
    execution_manager = GovernanceExecutionManager(
        clock=clock, trigger_engine=trigger_engine
    )
    retry_engine = GovernanceRetryEngine(clock=clock)
    metrics = GovernanceSchedulerMetrics(clock=clock)
    policy_engine = GovernanceSchedulerPolicyEngine(clock=clock)
    scheduler = GovernanceScheduler(
        clock=clock, job_registry=job_registry, trigger_engine=trigger_engine,
    )
    job_persistence = GovernanceJobPersistence(
        clock=clock, job_registry=job_registry, trigger_engine=trigger_engine,
        retry_engine=retry_engine, scheduler=scheduler,
    )

    return dict(
        clock=clock,
        event_bus=event_bus,
        job_registry=job_registry,
        trigger_engine=trigger_engine,
        dependency_manager=dependency_manager,
        lock_manager=lock_manager,
        execution_manager=execution_manager,
        retry_engine=retry_engine,
        metrics=metrics,
        policy_engine=policy_engine,
        scheduler=scheduler,
        job_persistence=job_persistence,
    )


FULL_PIPELINE_ORDER = (
    "job_registry",
    "trigger_engine",
    "dependency_manager",
    "lock_manager",
    "execution_manager",
    "retry_engine",
    "metrics",
    "policy_engine",
)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The scheduler bootstrap (and everything it wraps) is a process-
    wide singleton. Most tests below construct their own fresh
    pipeline instead (see _build_pipeline); only the singleton and API
    tests touch the shared instance, so only those need resetting —
    matching test_deployment_governance_job_persistence.py's own
    fixture.
    """

    from backend.observability.deployment_governance_job_persistence import (
        get_job_persistence,
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
    from backend.observability.deployment_governance_scheduler import (
        get_scheduler,
    )
    from backend.observability.deployment_governance_trigger_engine import (
        get_trigger_engine,
    )

    def _reset():
        get_scheduler_bootstrap().shutdown()
        get_lifecycle_manager().shutdown()
        get_scheduler().stop()
        get_job_persistence().clear()

        retry_engine = get_retry_engine()

        for attempt in retry_engine.pending():
            retry_engine.cancel_retry(attempt.execution_id)

        get_trigger_engine().clear()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


# --- Models ----------------------------------------------------------------


class TestSchedulerBootstrapReport:

    def test_rejects_naive_completed_at(self):
        with pytest.raises(
            ValueError, match="completed_at must be timezone-aware"
        ):
            SchedulerBootstrapReport(
                started=True, restored_jobs=0, restored_triggers=0,
                restored_retry_queue=0, initialized_components=(),
                completed_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_rejects_negative_restored_jobs(self):
        with pytest.raises(ValueError, match="restored_jobs must be >= 0"):
            SchedulerBootstrapReport(
                started=True, restored_jobs=-1, restored_triggers=0,
                restored_retry_queue=0, initialized_components=(),
                completed_at=BASE_TIME,
            )

    def test_to_dict(self):
        report = SchedulerBootstrapReport(
            started=True, restored_jobs=2, restored_triggers=1,
            restored_retry_queue=0,
            initialized_components=("job_registry", "trigger_engine"),
            completed_at=BASE_TIME,
        )

        assert report.to_dict() == {
            "started": True,
            "restored_jobs": 2,
            "restored_triggers": 1,
            "restored_retry_queue": 0,
            "initialized_components": ["job_registry", "trigger_engine"],
            "completed_at": BASE_TIME.isoformat(),
        }


class TestSchedulerBootstrapStatus:

    def test_rejects_naive_started_at(self):
        with pytest.raises(
            ValueError, match="started_at must be timezone-aware"
        ):
            SchedulerBootstrapStatus(
                initialized=True, running=True, version="1",
                started_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict_with_no_started_at(self):
        status = SchedulerBootstrapStatus(
            initialized=False, running=False, version="1", started_at=None,
        )

        assert status.to_dict() == {
            "initialized": False,
            "running": False,
            "version": "1",
            "started_at": None,
        }

    def test_to_dict_with_started_at(self):
        status = SchedulerBootstrapStatus(
            initialized=True, running=True, version="1",
            started_at=BASE_TIME,
        )

        assert status.to_dict()["started_at"] == BASE_TIME.isoformat()


# --- Dependency validation ---------------------------------------------


class TestGovernanceSchedulerBootstrapValidate:

    def test_fully_wired_pipeline_is_valid(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        result = bootstrap.validate()

        assert result.valid is True
        assert result.startup_order == FULL_PIPELINE_ORDER

    def test_nothing_wired_is_trivially_valid(self):
        bootstrap = GovernanceSchedulerBootstrap(clock=_clock)

        result = bootstrap.validate()

        assert result.valid is True
        assert result.startup_order == ()

    def test_gap_in_the_middle_is_reported_missing(self):
        components = _build_pipeline()
        components["lock_manager"] = None

        bootstrap = GovernanceSchedulerBootstrap(**components)

        result = bootstrap.validate()

        assert result.valid is False
        assert "lock_manager" in result.missing

    def test_component_after_the_gap_is_never_registered(self):
        components = _build_pipeline()
        components["metrics"] = None

        bootstrap = GovernanceSchedulerBootstrap(**components)

        result = bootstrap.validate()

        assert result.valid is False
        # "policy_engine" depends on "metrics", which is missing; it is
        # not itself reported as missing (only unresolved *references*
        # are), but the graph as a whole is invalid either way.
        assert "metrics" in result.missing


# --- Successful bootstrap / ordering -------------------------------------


class TestGovernanceSchedulerBootstrapInitialize:

    def test_successful_bootstrap_starts_the_scheduler(self):
        components = _build_pipeline()
        bootstrap = GovernanceSchedulerBootstrap(**components)

        report = bootstrap.initialize()

        assert report.started is True
        assert components["scheduler"].status().running is True

    def test_initialization_ordering_is_deterministic(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        report = bootstrap.initialize()

        assert report.initialized_components == FULL_PIPELINE_ORDER

    def test_partial_wiring_omits_unwired_components(self):
        components = _build_pipeline()
        components["policy_engine"] = None

        bootstrap = GovernanceSchedulerBootstrap(**components)
        report = bootstrap.initialize()

        assert "policy_engine" not in report.initialized_components
        assert report.initialized_components == FULL_PIPELINE_ORDER[:-1]

    def test_invalid_graph_raises_and_never_starts_scheduler(self):
        components = _build_pipeline()
        components["lock_manager"] = None

        bootstrap = GovernanceSchedulerBootstrap(**components)

        with pytest.raises(GovernanceSchedulerBootstrapError):
            bootstrap.initialize()

        assert components["scheduler"].status().running is False

    def test_invalid_graph_never_restores_persisted_state(self, tmp_path):
        path = tmp_path / "scheduler.json"

        source_registry = GovernanceJobRegistry(clock=_clock)
        source_registry.register("job-1", "Job One")
        GovernanceJobPersistence(
            clock=_clock, job_registry=source_registry, path=path,
        ).save()

        components = _build_pipeline()
        components["lock_manager"] = None
        components["job_persistence"] = GovernanceJobPersistence(
            clock=_clock, job_registry=components["job_registry"], path=path,
        )

        bootstrap = GovernanceSchedulerBootstrap(**components)

        with pytest.raises(GovernanceSchedulerBootstrapError):
            bootstrap.initialize()

        assert components["job_registry"].exists("job-1") is False

    def test_failed_event_is_published_on_invalid_graph(self):
        received: "list[str]" = []
        bus = GovernanceEventBus(clock=_clock)
        bus.subscribe_all(lambda event: received.append(event.event_type))

        components = _build_pipeline(event_bus=bus)
        components["lock_manager"] = None

        bootstrap = GovernanceSchedulerBootstrap(**components)

        with pytest.raises(GovernanceSchedulerBootstrapError):
            bootstrap.initialize()

        assert "scheduler_bootstrap_started" in received
        assert "scheduler_bootstrap_failed" in received
        assert "scheduler_bootstrap_completed" not in received

    def test_success_events_are_published_in_order(self):
        received: "list[str]" = []
        bus = GovernanceEventBus(clock=_clock)
        bus.subscribe_all(lambda event: received.append(event.event_type))

        bootstrap = GovernanceSchedulerBootstrap(
            **_build_pipeline(event_bus=bus)
        )
        bootstrap.initialize()

        assert received == [
            "scheduler_bootstrap_started",
            "scheduler_bootstrap_completed",
            "scheduler_runtime_ready",
        ]


# --- State restoration ----------------------------------------------------


class TestGovernanceSchedulerBootstrapRestore:

    def test_restore_with_no_job_persistence_wired_is_zeroed(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        counts = bootstrap.restore()

        assert counts.jobs == 0
        assert counts.triggers == 0
        assert counts.pending_retries == 0

    def test_initialize_restores_persisted_jobs_and_triggers(
        self, tmp_path,
    ):
        path = tmp_path / "scheduler.json"

        source_registry = GovernanceJobRegistry(clock=_clock)
        source_trigger_engine = GovernanceTriggerEngine(
            clock=_clock, job_registry=source_registry,
        )
        source_registry.register("job-1", "Job One")
        source_trigger_engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )
        GovernanceJobPersistence(
            clock=_clock, job_registry=source_registry,
            trigger_engine=source_trigger_engine, path=path,
        ).save()

        components = _build_pipeline()
        components["job_persistence"] = GovernanceJobPersistence(
            clock=_clock, job_registry=components["job_registry"],
            trigger_engine=components["trigger_engine"], path=path,
        )

        bootstrap = GovernanceSchedulerBootstrap(**components)
        report = bootstrap.initialize()

        assert report.restored_jobs == 1
        assert report.restored_triggers == 1
        assert components["job_registry"].exists("job-1") is True


# --- Graceful shutdown -----------------------------------------------------


class TestGovernanceSchedulerBootstrapShutdown:

    def test_shutdown_stops_the_scheduler(self):
        components = _build_pipeline()
        bootstrap = GovernanceSchedulerBootstrap(**components)
        bootstrap.initialize()

        bootstrap.shutdown()

        assert components["scheduler"].status().running is False

    def test_shutdown_saves_state_for_the_next_restore(self, tmp_path):
        path = tmp_path / "scheduler.json"

        components = _build_pipeline()
        components["job_persistence"] = GovernanceJobPersistence(
            clock=_clock, job_registry=components["job_registry"],
            scheduler=components["scheduler"], path=path,
        )

        bootstrap = GovernanceSchedulerBootstrap(**components)
        bootstrap.initialize()
        bootstrap.shutdown()

        # Saved while the scheduler was still marked running (save()
        # runs before stop()), so a later restore knows to resume it.
        assert json.loads(path.read_text())["scheduler_running"] is True

    def test_shutdown_when_not_initialized_is_a_no_op(self):
        components = _build_pipeline()
        bootstrap = GovernanceSchedulerBootstrap(**components)

        bootstrap.shutdown()

        assert components["scheduler"].status().running is False

    def test_shutdown_publishes_runtime_shutdown_event(self):
        received: "list[str]" = []
        bus = GovernanceEventBus(clock=_clock)
        bus.subscribe_all(lambda event: received.append(event.event_type))

        bootstrap = GovernanceSchedulerBootstrap(
            **_build_pipeline(event_bus=bus)
        )
        bootstrap.initialize()
        received.clear()

        bootstrap.shutdown()

        assert received == ["scheduler_runtime_shutdown"]


# --- Idempotency -------------------------------------------------------


class TestGovernanceSchedulerBootstrapIdempotency:

    def test_second_initialize_call_returns_the_cached_report(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        first_report = bootstrap.initialize()
        second_report = bootstrap.initialize()

        assert second_report is first_report

    def test_second_initialize_call_publishes_nothing_further(self):
        bus = GovernanceEventBus(clock=_clock)
        bootstrap = GovernanceSchedulerBootstrap(
            **_build_pipeline(event_bus=bus)
        )

        bootstrap.initialize()

        received: "list[str]" = []
        bus.subscribe_all(lambda event: received.append(event.event_type))

        bootstrap.initialize()

        assert received == []

    def test_second_shutdown_call_is_a_no_op(self):
        bus = GovernanceEventBus(clock=_clock)
        bootstrap = GovernanceSchedulerBootstrap(
            **_build_pipeline(event_bus=bus)
        )
        bootstrap.initialize()
        bootstrap.shutdown()

        received: "list[str]" = []
        bus.subscribe_all(lambda event: received.append(event.event_type))

        bootstrap.shutdown()

        assert received == []


# --- Restart ---------------------------------------------------------------


class TestGovernanceSchedulerBootstrapRestart:

    def test_restart_reinitializes_after_shutdown(self):
        components = _build_pipeline()
        bootstrap = GovernanceSchedulerBootstrap(**components)
        bootstrap.initialize()

        report = bootstrap.restart()

        assert report.started is True
        assert components["scheduler"].status().running is True

    def test_restart_produces_a_fresh_report(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        first_report = bootstrap.initialize()
        second_report = bootstrap.restart()

        assert second_report is not first_report


# --- Status ------------------------------------------------------------


class TestGovernanceSchedulerBootstrapStatusMethod:

    def test_status_before_initialize(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        status = bootstrap.status()

        assert status.initialized is False
        assert status.running is False
        assert status.started_at is None

    def test_status_after_initialize(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        bootstrap.initialize()
        status = bootstrap.status()

        assert status.initialized is True
        assert status.running is True
        assert status.started_at == BASE_TIME


# --- Health / readiness integration --------------------------------------


class TestGovernanceSchedulerBootstrapHealthIntegration:

    def test_unhealthy_before_initialize(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        summary = bootstrap.build_health_service().summary()

        assert summary.healthy is False

    def test_healthy_after_initialize(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())
        bootstrap.initialize()

        summary = bootstrap.build_health_service().summary()

        assert summary.healthy is True

    def test_dependency_graph_check_reflects_validate(self):
        components = _build_pipeline()
        components["lock_manager"] = None
        bootstrap = GovernanceSchedulerBootstrap(**components)

        status = bootstrap.build_health_service().check("dependency_graph")

        assert status.healthy is False


class TestGovernanceSchedulerBootstrapReadinessIntegration:

    def test_not_ready_before_initialize(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())

        summary = bootstrap.build_readiness_service().summary()

        assert summary.ready is False

    def test_ready_after_initialize(self):
        bootstrap = GovernanceSchedulerBootstrap(**_build_pipeline())
        bootstrap.initialize()

        summary = bootstrap.build_readiness_service().summary()

        assert summary.ready is True


class TestGovernanceSchedulerBootstrapDiagnostics:

    def test_diagnostics_snapshot_reflects_registered_jobs(self):
        components = _build_pipeline()
        components["job_registry"].register("job-1", "Job One")

        bootstrap = GovernanceSchedulerBootstrap(**components)
        bootstrap.initialize()

        snapshot = bootstrap.build_diagnostics_service().snapshot()

        assert snapshot.runtime_state == "running"
        assert snapshot.registered_providers == 1


# --- Singleton / lifecycle integration ------------------------------------


class TestGovernanceSchedulerBootstrapSingleton:

    def test_get_scheduler_bootstrap_returns_same_instance(self):
        assert get_scheduler_bootstrap() is get_scheduler_bootstrap()

    def test_lifecycle_startup_initializes_the_bootstrap(self):
        from backend.observability.deployment_governance_lifecycle import (
            get_lifecycle_manager,
        )

        manager = get_lifecycle_manager()
        manager.startup()

        try:
            assert get_scheduler_bootstrap().status().initialized is True

        finally:
            manager.shutdown()

    def test_lifecycle_shutdown_shuts_down_the_bootstrap(self):
        from backend.observability.deployment_governance_lifecycle import (
            get_lifecycle_manager,
        )

        manager = get_lifecycle_manager()
        manager.startup()
        manager.shutdown()

        assert get_scheduler_bootstrap().status().initialized is False


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSchedulerBootstrapApi:

    def test_get_bootstrap_status(self, client):
        response = client.get("/governance/scheduler/bootstrap")

        assert response.status_code == 200
        assert response.json()["initialized"] is False

    def test_post_bootstrap_initializes(self, client):
        response = client.post("/governance/scheduler/bootstrap")

        assert response.status_code == 200

        payload = response.json()

        assert payload["started"] is True
        assert set(payload["initialized_components"]) == set(
            FULL_PIPELINE_ORDER
        )

        status_response = client.get("/governance/scheduler/bootstrap")

        assert status_response.json()["initialized"] is True

    def test_post_restart(self, client):
        client.post("/governance/scheduler/bootstrap")

        response = client.post("/governance/scheduler/restart")

        assert response.status_code == 200
        assert response.json()["started"] is True

        status_response = client.get("/governance/scheduler/bootstrap")

        assert status_response.json()["initialized"] is True
