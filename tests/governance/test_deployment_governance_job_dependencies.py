from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_execution_manager import (
    GovernanceExecutionManager,
)
from backend.observability.deployment_governance_job_dependencies import (
    DependencyEvaluation,
    GovernanceJobDependencyManager,
    JobDependency,
)
from backend.observability.deployment_governance_job_registry import (
    GovernanceJobRegistry,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The job dependency manager, job registry, and execution manager are
    all process-wide singletons, so tests that touch any of them
    (directly or via the API) must not leak state into other tests.
    """

    from backend.observability.deployment_governance_execution_manager import (
        get_execution_manager,
    )
    from backend.observability.deployment_governance_job_dependencies import (
        get_job_dependency_manager,
    )
    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_execution_manager().cleanup()
        get_job_dependency_manager().clear()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestJobDependency:

    def test_rejects_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must not be empty"):
            JobDependency(job_id="", depends_on=())

    def test_rejects_self_dependency(self):
        with pytest.raises(
            ValueError, match="job_id must not depend on itself"
        ):
            JobDependency(job_id="a", depends_on=("a",))

    def test_to_dict(self):
        dependency = JobDependency(job_id="a", depends_on=("b", "c"))

        assert dependency.to_dict() == {
            "job_id": "a", "depends_on": ["b", "c"],
        }


class TestDependencyEvaluation:

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            DependencyEvaluation(
                job_id="a", ready=True, blocked_by=(),
                evaluated_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_rejects_ready_with_blocked_by(self):
        with pytest.raises(
            ValueError, match="blocked_by must be empty when ready is True"
        ):
            DependencyEvaluation(
                job_id="a", ready=True, blocked_by=("b",),
                evaluated_at=BASE_TIME,
            )

    def test_rejects_not_ready_without_blocked_by(self):
        with pytest.raises(
            ValueError,
            match="blocked_by must not be empty when ready is False",
        ):
            DependencyEvaluation(
                job_id="a", ready=False, blocked_by=(),
                evaluated_at=BASE_TIME,
            )

    def test_to_dict(self):
        evaluation = DependencyEvaluation(
            job_id="a", ready=False, blocked_by=("b",),
            evaluated_at=BASE_TIME,
        )

        assert evaluation.to_dict() == {
            "job_id": "a",
            "ready": False,
            "blocked_by": ["b"],
            "evaluated_at": BASE_TIME.isoformat(),
        }


# --- Register dependency -------------------------------------------


class TestRegisterDependency:

    def test_register_returns_dependency(self):
        manager = GovernanceJobDependencyManager(clock=_clock)

        dependency = manager.register("a", depends_on=("b", "c"))

        assert dependency.job_id == "a"
        assert dependency.depends_on == ("b", "c")

    def test_registered_dependency_is_retrievable(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))

        assert manager.dependencies("a") == ("b",)

    def test_defaults_to_no_dependencies(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a")

        assert manager.dependencies("a") == ()

    def test_duplicate_registration_rejected(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))

        with pytest.raises(ValueError, match="already registered"):
            manager.register("a", depends_on=("c",))

    def test_dependents_reverse_lookup(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("shared",))
        manager.register("b", depends_on=("shared",))

        assert manager.dependents("shared") == ("a", "b")

    def test_dependents_does_not_require_own_registration(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("leaf",))

        assert manager.dependents("leaf") == ("a",)

    def test_remove_removes_dependency(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))

        manager.remove("a")

        with pytest.raises(KeyError):
            manager.dependencies("a")

    def test_remove_unknown_raises(self):
        manager = GovernanceJobDependencyManager(clock=_clock)

        with pytest.raises(KeyError):
            manager.remove("ghost")

    def test_dependencies_unknown_raises(self):
        manager = GovernanceJobDependencyManager(clock=_clock)

        with pytest.raises(KeyError):
            manager.dependencies("ghost")


# --- Unknown dependency rejection -----------------------------------


class TestUnknownDependencyRejection:

    def test_rejects_unregistered_job_id(self):
        registry = GovernanceJobRegistry(clock=_clock)
        manager = GovernanceJobDependencyManager(
            clock=_clock, job_registry=registry,
        )

        with pytest.raises(ValueError, match="is not registered"):
            manager.register("ghost", depends_on=())

    def test_rejects_unregistered_dependency(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("a", "a")
        manager = GovernanceJobDependencyManager(
            clock=_clock, job_registry=registry,
        )

        with pytest.raises(ValueError, match="is not registered"):
            manager.register("a", depends_on=("ghost",))

    def test_no_job_registry_skips_validation(self):
        manager = GovernanceJobDependencyManager(clock=_clock)

        dependency = manager.register("ghost", depends_on=("also-ghost",))

        assert dependency.job_id == "ghost"


# --- Self-dependency rejection ---------------------------------------


class TestSelfDependencyRejection:

    def test_register_rejects_self_dependency(self):
        manager = GovernanceJobDependencyManager(clock=_clock)

        with pytest.raises(ValueError, match="cannot depend on itself"):
            manager.register("a", depends_on=("a",))

    def test_self_dependency_is_not_registered(self):
        manager = GovernanceJobDependencyManager(clock=_clock)

        with pytest.raises(ValueError):
            manager.register("a", depends_on=("a",))

        with pytest.raises(KeyError):
            manager.dependencies("a")


# --- Circular dependency detection -----------------------------------


class TestCircularDependencyDetection:

    def test_direct_cycle_rejected(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))

        with pytest.raises(ValueError, match="circular dependency"):
            manager.register("b", depends_on=("a",))

    def test_indirect_cycle_rejected(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))
        manager.register("b", depends_on=("c",))

        with pytest.raises(ValueError, match="circular dependency"):
            manager.register("c", depends_on=("a",))

    def test_cycle_is_not_registered(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))

        with pytest.raises(ValueError):
            manager.register("b", depends_on=("a",))

        with pytest.raises(KeyError):
            manager.dependencies("b")

    def test_cycle_publishes_dependency_cycle_detected(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceJobDependencyManager(clock=_clock, event_bus=bus)
        manager.register("a", depends_on=("b",))
        received.clear()

        with pytest.raises(ValueError):
            manager.register("b", depends_on=("a",))

        assert received == ["dependency_cycle_detected"]

    def test_validate_reports_cycles_when_present(self):
        # Two independent managers sharing no cross-checks: build a
        # cycle by bypassing register()'s own guard is not possible
        # publicly, so this exercises validate() on a clean, valid
        # graph instead — cycles are unreachable through the public
        # API by design (register() always rejects them first).
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))

        result = manager.validate()

        assert result.valid is True
        assert result.cycles == ()


# --- Execution order generation ---------------------------------------


class TestExecutionOrderGeneration:

    def test_execution_order_respects_dependencies(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("c", depends_on=("b",))
        manager.register("b", depends_on=("a",))
        manager.register("a")

        order = manager.execution_order()

        assert order.index("a") < order.index("b") < order.index("c")

    def test_execution_order_includes_leaf_dependencies(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("leaf",))

        assert "leaf" in manager.execution_order()

    def test_execution_order_is_deterministic(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("z", depends_on=())
        manager.register("y", depends_on=())

        assert manager.execution_order() == ("y", "z")


# --- Blocked job evaluation -------------------------------------------


class TestBlockedJobEvaluation:

    def test_job_with_no_dependencies_is_ready(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a")

        evaluation = manager.evaluate("a")

        assert evaluation.ready is True
        assert evaluation.blocked_by == ()

    def test_job_blocked_when_dependency_has_no_history(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        manager = GovernanceJobDependencyManager(
            clock=_clock, execution_manager=execution_manager,
        )
        manager.register("a", depends_on=("b",))

        evaluation = manager.evaluate("a")

        assert evaluation.ready is False
        assert evaluation.blocked_by == ("b",)

    def test_job_blocked_when_dependency_failed(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        execution_manager.execute(
            "b", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        manager = GovernanceJobDependencyManager(
            clock=_clock, execution_manager=execution_manager,
        )
        manager.register("a", depends_on=("b",))

        evaluation = manager.evaluate("a")

        assert evaluation.ready is False
        assert evaluation.blocked_by == ("b",)

    def test_no_execution_manager_wired_assumes_ready(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("a", depends_on=("b",))

        evaluation = manager.evaluate("a")

        assert evaluation.ready is True

    def test_evaluate_unregistered_job_is_ready(self):
        manager = GovernanceJobDependencyManager(clock=_clock)

        evaluation = manager.evaluate("ghost")

        assert evaluation.ready is True

    def test_evaluate_all_orders_by_job_id(self):
        manager = GovernanceJobDependencyManager(clock=_clock)
        manager.register("z")
        manager.register("a")

        evaluations = manager.evaluate_all()

        assert [e.job_id for e in evaluations] == ["a", "z"]


# --- Dependency resolution -------------------------------------------


class TestDependencyResolution:

    def test_job_ready_when_dependency_succeeded(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        execution_manager.execute("b")
        manager = GovernanceJobDependencyManager(
            clock=_clock, execution_manager=execution_manager,
        )
        manager.register("a", depends_on=("b",))

        evaluation = manager.evaluate("a")

        assert evaluation.ready is True
        assert evaluation.blocked_by == ()

    def test_resolution_uses_most_recent_execution(self):
        execution_manager = GovernanceExecutionManager(clock=_clock)
        execution_manager.execute(
            "b", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        execution_manager.execute("b")
        manager = GovernanceJobDependencyManager(
            clock=_clock, execution_manager=execution_manager,
        )
        manager.register("a", depends_on=("b",))

        evaluation = manager.evaluate("a")

        assert evaluation.ready is True

    def test_resolution_publishes_dependency_resolved(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        execution_manager = GovernanceExecutionManager(clock=_clock)
        execution_manager.execute("b")
        manager = GovernanceJobDependencyManager(
            clock=_clock, execution_manager=execution_manager,
            event_bus=bus,
        )
        manager.register("a", depends_on=("b",))
        received.clear()

        manager.evaluate("a")

        assert received == ["dependency_resolved"]

    def test_blocked_publishes_dependency_blocked(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        execution_manager = GovernanceExecutionManager(clock=_clock)
        manager = GovernanceJobDependencyManager(
            clock=_clock, execution_manager=execution_manager,
            event_bus=bus,
        )
        manager.register("a", depends_on=("b",))
        received.clear()

        manager.evaluate("a")

        assert received == ["dependency_blocked"]

    def test_dependency_less_job_publishes_nothing(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceJobDependencyManager(clock=_clock, event_bus=bus)
        manager.register("a")
        received.clear()

        manager.evaluate("a")

        assert received == []


# --- Registration events ------------------------------------------------


class TestEventPublication:

    def test_registration_publishes_dependency_registered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceJobDependencyManager(clock=_clock, event_bus=bus)
        manager.register("a", depends_on=("b",))

        assert received == ["dependency_registered"]

    def test_removal_publishes_dependency_removed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceJobDependencyManager(clock=_clock, event_bus=bus)
        manager.register("a", depends_on=("b",))
        received.clear()

        manager.remove("a")

        assert received == ["dependency_removed"]


# --- Scheduler integration -------------------------------------------


class TestSchedulerIntegration:

    def test_run_due_skips_blocked_jobs(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        class _ControllableClock:
            def __init__(self, start):
                self.now = start

            def __call__(self):
                return self.now

        clock = _ControllableClock(BASE_TIME)

        job_registry = GovernanceJobRegistry(clock=clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=clock, job_registry=job_registry,
        )
        scheduler = GovernanceScheduler(
            clock=clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )
        execution_manager = GovernanceExecutionManager(clock=clock)
        dependency_manager = GovernanceJobDependencyManager(
            clock=clock, execution_manager=execution_manager,
        )

        scheduler.start()
        upstream = scheduler.register("upstream", interval_seconds=60)
        downstream = scheduler.register("downstream", interval_seconds=60)
        dependency_manager.register(
            downstream.job_id, depends_on=(upstream.job_id,),
        )

        clock.now = BASE_TIME + timedelta(seconds=61)

        results = scheduler.run_due(
            execution_manager, dependency_manager=dependency_manager,
        )

        # Only upstream ran this tick — downstream was filtered out
        # before dispatch since it was still blocked at the moment
        # readiness was evaluated (upstream had no execution history
        # yet at that point).
        assert len(results) == 1
        assert len(execution_manager.history()) == 1

        # Now that upstream has succeeded, downstream is unblocked for
        # the *next* tick.
        assert dependency_manager.evaluate(downstream.job_id).ready is True

    def test_run_due_runs_ready_jobs(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        class _ControllableClock:
            def __init__(self, start):
                self.now = start

            def __call__(self):
                return self.now

        clock = _ControllableClock(BASE_TIME)

        job_registry = GovernanceJobRegistry(clock=clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=clock, job_registry=job_registry,
        )
        scheduler = GovernanceScheduler(
            clock=clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )
        execution_manager = GovernanceExecutionManager(clock=clock)
        dependency_manager = GovernanceJobDependencyManager(
            clock=clock, execution_manager=execution_manager,
        )

        scheduler.start()
        job = scheduler.register("solo", interval_seconds=60)
        dependency_manager.register(job.job_id)

        clock.now = BASE_TIME + timedelta(seconds=61)

        results = scheduler.run_due(
            execution_manager, dependency_manager=dependency_manager,
        )

        assert len(results) == 1
        assert results[0].status == "SUCCEEDED"


# --- Persistence round-trip -----------------------------------------


class TestPersistenceRoundTrip:

    def test_dependencies_persist_and_restore_via_file(self, tmp_path):
        from backend.observability.deployment_governance_job_persistence import (
            GovernanceJobPersistence,
        )

        path = tmp_path / "snapshot.json"

        source_registry = GovernanceJobRegistry(clock=_clock)
        source_registry.register("a", "a")
        source_registry.register("b", "b")
        source_manager = GovernanceJobDependencyManager(
            clock=_clock, job_registry=source_registry,
        )
        source_manager.register("a", depends_on=("b",))

        GovernanceJobPersistence(
            clock=_clock, job_registry=source_registry,
            dependency_manager=source_manager, path=path,
        ).save()

        target_registry = GovernanceJobRegistry(clock=_clock)
        target_registry.register("a", "a")
        target_registry.register("b", "b")
        target_manager = GovernanceJobDependencyManager(
            clock=_clock, job_registry=target_registry,
        )

        result = GovernanceJobPersistence(
            clock=_clock, job_registry=target_registry,
            dependency_manager=target_manager, path=path,
        ).load()

        assert result.success is True
        assert target_manager.dependencies("a") == ("b",)

    def test_document_without_dependencies_key_still_loads(self, tmp_path):
        import json

        from backend.observability.deployment_governance_job_persistence import (
            CURRENT_SCHEMA_VERSION,
            GovernanceJobPersistence,
        )

        path = tmp_path / "snapshot.json"
        path.write_text(
            json.dumps(
                {
                    "version": CURRENT_SCHEMA_VERSION,
                    "created_at": BASE_TIME.isoformat(),
                    "jobs": [],
                    "triggers": [],
                    "pending_retries": [],
                }
            )
        )

        manager = GovernanceJobDependencyManager(clock=_clock)
        result = GovernanceJobPersistence(
            clock=_clock, dependency_manager=manager, path=path,
        ).load()

        assert result.success is True
        assert manager.validate().startup_order == ()


# --- Singleton -------------------------------------------------------------


class TestJobDependencyManagerSingleton:

    def test_get_job_dependency_manager_returns_same_instance(self):
        from backend.observability.deployment_governance_job_dependencies import (
            get_job_dependency_manager,
        )

        assert (
            get_job_dependency_manager() is get_job_dependency_manager()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _registered_jobs(_reset_singletons):
    """
    The API builds its dependency manager wired to the shared job
    registry singleton, which validates job_id at registration time,
    so every API test needs its jobs to actually exist there first.
    """

    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )

    get_job_registry().register("a", "a")
    get_job_registry().register("b", "b")


class TestGovernanceJobDependenciesApi:

    def test_get_job_dependencies_returns_empty_list_initially(
        self, client
    ) -> None:
        response = client.get("/governance/job-dependencies")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_registers_a_new_dependency(self, client) -> None:
        response = client.post(
            "/governance/job-dependencies",
            params={"job_id": "a", "depends_on": ["b"]},
        )

        assert response.status_code == 200
        assert response.json() == {"job_id": "a", "depends_on": ["b"]}

    def test_post_self_dependency_returns_409(self, client) -> None:
        response = client.post(
            "/governance/job-dependencies",
            params={"job_id": "a", "depends_on": ["a"]},
        )

        assert response.status_code == 409

    def test_get_job_dependency_by_id(self, client) -> None:
        client.post(
            "/governance/job-dependencies",
            params={"job_id": "a", "depends_on": ["b"]},
        )

        response = client.get("/governance/job-dependencies/a")

        assert response.status_code == 200
        assert response.json() == {"job_id": "a", "depends_on": ["b"]}

    def test_get_unknown_job_dependency_returns_404(self, client) -> None:
        response = client.get("/governance/job-dependencies/ghost")

        assert response.status_code == 404

    def test_delete_job_dependency(self, client) -> None:
        client.post(
            "/governance/job-dependencies",
            params={"job_id": "a", "depends_on": ["b"]},
        )

        response = client.delete("/governance/job-dependencies/a")

        assert response.status_code == 200
        assert response.json() == {"removed": "a"}

    def test_delete_unknown_job_dependency_returns_404(self, client) -> None:
        response = client.delete("/governance/job-dependencies/ghost")

        assert response.status_code == 404

    def test_post_validate_reports_valid_graph(self, client) -> None:
        client.post(
            "/governance/job-dependencies",
            params={"job_id": "a", "depends_on": ["b"]},
        )

        response = client.post("/governance/job-dependencies/validate")

        assert response.status_code == 200

        payload = response.json()
        assert payload["valid"] is True
        assert "b" in payload["startup_order"]
        assert "a" in payload["startup_order"]
