from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_trigger_engine import (
    GovernanceTriggerEngine,
    TriggerDefinition,
    TriggerEvaluation,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The trigger engine, job registry, and scheduler are all
    process-wide singletons wired together (the scheduler delegates
    eligibility to the trigger engine, which validates job_id against
    the job registry), so tests that touch any of them (directly or
    via the API) must not leak state into other tests.
    """

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
        get_trigger_engine().clear()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestTriggerDefinition:

    def test_rejects_empty_trigger_id(self):
        with pytest.raises(ValueError, match="trigger_id must not be empty"):
            TriggerDefinition(
                trigger_id="", job_id="job-1", trigger_type="manual",
                enabled=True, next_run=None,
            )

    def test_rejects_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must not be empty"):
            TriggerDefinition(
                trigger_id="t-1", job_id="", trigger_type="manual",
                enabled=True, next_run=None,
            )

    def test_rejects_empty_trigger_type(self):
        with pytest.raises(
            ValueError, match="trigger_type must not be empty"
        ):
            TriggerDefinition(
                trigger_id="t-1", job_id="job-1", trigger_type="",
                enabled=True, next_run=None,
            )

    def test_rejects_naive_next_run(self):
        with pytest.raises(
            ValueError, match="next_run must be timezone-aware"
        ):
            TriggerDefinition(
                trigger_id="t-1", job_id="job-1", trigger_type="interval",
                enabled=True, next_run=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict(self):
        trigger = TriggerDefinition(
            trigger_id="t-1", job_id="job-1", trigger_type="interval",
            enabled=False, next_run=BASE_TIME,
        )

        assert trigger.to_dict() == {
            "trigger_id": "t-1",
            "job_id": "job-1",
            "trigger_type": "interval",
            "enabled": False,
            "next_run": BASE_TIME.isoformat(),
        }

    def test_to_dict_with_no_next_run(self):
        trigger = TriggerDefinition(
            trigger_id="t-1", job_id="job-1", trigger_type="manual",
            enabled=True, next_run=None,
        )

        assert trigger.to_dict()["next_run"] is None


class TestTriggerEvaluation:

    def test_rejects_empty_trigger_id(self):
        with pytest.raises(ValueError, match="trigger_id must not be empty"):
            TriggerEvaluation(
                trigger_id="", should_run=True, evaluated_at=BASE_TIME,
            )

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            TriggerEvaluation(
                trigger_id="t-1", should_run=True,
                evaluated_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict(self):
        evaluation = TriggerEvaluation(
            trigger_id="t-1", should_run=True, evaluated_at=BASE_TIME,
        )

        assert evaluation.to_dict() == {
            "trigger_id": "t-1",
            "should_run": True,
            "evaluated_at": BASE_TIME.isoformat(),
        }


# --- Registration --------------------------------------------------------


class TestRegistration:

    def test_register_returns_trigger(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        trigger = engine.register("job-1", trigger_type="manual")

        assert trigger.job_id == "job-1"
        assert trigger.trigger_type == "manual"
        assert trigger.enabled is True

    def test_registered_trigger_appears_in_list(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        engine.register("job-1", trigger_type="manual")

        assert len(engine.list()) == 1

    def test_register_assigns_unique_trigger_ids(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        first = engine.register("job-1", trigger_type="manual")
        second = engine.register("job-2", trigger_type="manual")

        assert first.trigger_id != second.trigger_id

    def test_rejects_unknown_trigger_type_without_evaluator(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        with pytest.raises(ValueError, match="unknown trigger type"):
            engine.register("job-1", trigger_type="teleport")

    def test_accepts_custom_evaluator_for_unknown_trigger_type(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        trigger = engine.register(
            "job-1", trigger_type="teleport",
            evaluator=lambda trigger, at: True,
        )

        assert trigger.trigger_type == "teleport"

    def test_validates_job_exists_when_job_registry_given(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )

        registry = GovernanceJobRegistry(clock=_clock)
        engine = GovernanceTriggerEngine(clock=_clock, job_registry=registry)

        with pytest.raises(ValueError, match="is not registered"):
            engine.register("ghost-job", trigger_type="manual")

    def test_accepts_registration_for_a_job_that_exists(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )

        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")
        engine = GovernanceTriggerEngine(clock=_clock, job_registry=registry)

        trigger = engine.register("job-1", trigger_type="manual")

        assert trigger.job_id == "job-1"

    def test_no_job_registry_skips_validation(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        trigger = engine.register("ghost-job", trigger_type="manual")

        assert trigger.job_id == "ghost-job"


# --- Interval evaluation -------------------------------------------------


class TestIntervalEvaluation:

    def test_not_due_before_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=60),
        )

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is False

    def test_due_at_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is True

    def test_due_after_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="interval",
            next_run=BASE_TIME - timedelta(seconds=1),
        )

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is True

    def test_not_due_with_no_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register("job-1", trigger_type="interval")

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is False

    def test_evaluate_unknown_trigger_raises(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.evaluate("ghost")


# --- One-shot execution --------------------------------------------------


class TestOneShotExecution:

    def test_one_shot_due_at_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="one_shot", next_run=BASE_TIME,
        )

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is True

    def test_one_shot_does_not_refire_after_removal(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="one_shot", next_run=BASE_TIME,
        )

        engine.evaluate(trigger.trigger_id, at=BASE_TIME)
        engine.remove(trigger.trigger_id)

        with pytest.raises(KeyError):
            engine.evaluate(trigger.trigger_id, at=BASE_TIME)


# --- Manual trigger --------------------------------------------------


class TestManualTrigger:

    def test_manual_never_fires_automatically(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="manual", next_run=BASE_TIME,
        )

        evaluation = engine.evaluate(
            trigger.trigger_id, at=BASE_TIME + timedelta(days=1),
        )

        assert evaluation.should_run is False


# --- Immediate trigger --------------------------------------------------


class TestImmediateTrigger:

    def test_immediate_is_due_without_a_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register("job-1", trigger_type="immediate")

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is True

    def test_disabled_immediate_never_fires(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="immediate", enabled=False,
        )

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is False


# --- Rescheduling ------------------------------------------------------


class TestRescheduling:

    def test_reschedule_updates_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )

        new_next_run = BASE_TIME + timedelta(seconds=120)
        updated = engine.reschedule(trigger.trigger_id, new_next_run)

        assert updated.next_run == new_next_run
        assert engine.list()[0].next_run == new_next_run

    def test_reschedule_unknown_trigger_raises(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.reschedule("ghost", BASE_TIME)

    def test_reschedule_makes_a_past_due_trigger_not_due(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )

        engine.reschedule(
            trigger.trigger_id, BASE_TIME + timedelta(seconds=60),
        )

        evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert evaluation.should_run is False


# --- next_execution ------------------------------------------------------


class TestNextExecution:

    def test_reports_soonest_next_run(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        engine.register(
            "job-1", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=120),
        )
        engine.register(
            "job-2", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=10),
        )

        assert engine.next_execution() == BASE_TIME + timedelta(seconds=10)

    def test_none_with_no_triggers(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        assert engine.next_execution() is None

    def test_ignores_disabled_triggers(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
            enabled=False,
        )

        assert engine.next_execution() is None


# --- Deterministic ordering -----------------------------------------------


class TestDeterministicOrdering:

    def test_list_ordered_by_next_run_then_trigger_id(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        far = engine.register(
            "job-1", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=120),
        )
        near = engine.register(
            "job-2", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=10),
        )

        assert [t.trigger_id for t in engine.list()] == [
            near.trigger_id, far.trigger_id,
        ]

    def test_triggers_with_no_next_run_sort_last(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        scheduled = engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )
        unscheduled = engine.register("job-2", trigger_type="manual")

        assert [t.trigger_id for t in engine.list()] == [
            scheduled.trigger_id, unscheduled.trigger_id,
        ]

    def test_evaluate_all_matches_list_ordering(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        far = engine.register(
            "job-1", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=120),
        )
        near = engine.register(
            "job-2", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=10),
        )

        evaluations = engine.evaluate_all(at=BASE_TIME)

        assert [e.trigger_id for e in evaluations] == [
            near.trigger_id, far.trigger_id,
        ]


# --- Disabled triggers skipped -------------------------------------------


def test_disabled_trigger_is_skipped_regardless_of_next_run():
    engine = GovernanceTriggerEngine(clock=_clock)
    trigger = engine.register(
        "job-1", trigger_type="interval",
        next_run=BASE_TIME - timedelta(seconds=1), enabled=False,
    )

    evaluation = engine.evaluate(trigger.trigger_id, at=BASE_TIME)

    assert evaluation.should_run is False


# --- Removal / clear ----------------------------------------------------


class TestRemoval:

    def test_remove_removes_trigger(self):
        engine = GovernanceTriggerEngine(clock=_clock)
        trigger = engine.register("job-1", trigger_type="manual")

        engine.remove(trigger.trigger_id)

        assert engine.list() == ()

    def test_remove_unknown_trigger_raises(self):
        engine = GovernanceTriggerEngine(clock=_clock)

        with pytest.raises(KeyError):
            engine.remove("ghost")


def test_clear_removes_every_trigger():
    engine = GovernanceTriggerEngine(clock=_clock)
    engine.register("job-1", trigger_type="manual")
    engine.register("job-2", trigger_type="manual")

    engine.clear()

    assert engine.list() == ()


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_registration_publishes_trigger_registered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceTriggerEngine(clock=_clock, event_bus=bus)
        engine.register("job-1", trigger_type="manual")

        assert received == ["trigger_registered"]

    def test_remove_publishes_trigger_removed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceTriggerEngine(clock=_clock, event_bus=bus)
        trigger = engine.register("job-1", trigger_type="manual")
        received.clear()

        engine.remove(trigger.trigger_id)

        assert received == ["trigger_removed"]

    def test_evaluate_due_trigger_publishes_trigger_fired(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceTriggerEngine(clock=_clock, event_bus=bus)
        trigger = engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )
        received.clear()

        engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert received == ["trigger_fired"]

    def test_evaluate_not_due_trigger_publishes_nothing(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceTriggerEngine(clock=_clock, event_bus=bus)
        trigger = engine.register(
            "job-1", trigger_type="interval",
            next_run=BASE_TIME + timedelta(seconds=60),
        )
        received.clear()

        engine.evaluate(trigger.trigger_id, at=BASE_TIME)

        assert received == []

    def test_reschedule_publishes_trigger_rescheduled(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        engine = GovernanceTriggerEngine(clock=_clock, event_bus=bus)
        trigger = engine.register(
            "job-1", trigger_type="interval", next_run=BASE_TIME,
        )
        received.clear()

        engine.reschedule(trigger.trigger_id, BASE_TIME + timedelta(60))

        assert received == ["trigger_rescheduled"]


# --- Scheduler delegation -------------------------------------------------


class TestSchedulerDelegation:

    def test_scheduler_registration_creates_a_matching_trigger(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        job_registry = GovernanceJobRegistry(clock=_clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=_clock, job_registry=job_registry
        )
        scheduler = GovernanceScheduler(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )

        job = scheduler.register("a", interval_seconds=60)

        triggers = trigger_engine.list()

        assert len(triggers) == 1
        assert triggers[0].job_id == job.job_id
        assert triggers[0].trigger_type == "interval"
        assert triggers[0].next_run == BASE_TIME + timedelta(seconds=60)

    def test_scheduler_unregister_removes_the_trigger_too(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        job_registry = GovernanceJobRegistry(clock=_clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=_clock, job_registry=job_registry
        )
        scheduler = GovernanceScheduler(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )
        job = scheduler.register("a", interval_seconds=60)

        scheduler.unregister(job.job_id)

        assert trigger_engine.list() == ()

    def test_scheduler_schedule_reschedules_the_trigger_too(self):
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        job_registry = GovernanceJobRegistry(clock=_clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=_clock, job_registry=job_registry
        )
        scheduler = GovernanceScheduler(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine,
        )
        job = scheduler.register("a", interval_seconds=60)

        next_run = scheduler.schedule(job.job_id)

        assert trigger_engine.list()[0].next_run == next_run


# --- Singleton -------------------------------------------------------------


class TestTriggerEngineSingleton:

    def test_get_trigger_engine_returns_same_instance(self):
        from backend.observability.deployment_governance_trigger_engine import (
            get_trigger_engine,
        )

        assert get_trigger_engine() is get_trigger_engine()

    def test_default_scheduler_shares_the_singleton_trigger_engine(self):
        from backend.observability.deployment_governance_scheduler import (
            get_scheduler,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            get_trigger_engine,
        )

        job = get_scheduler().register("shared-job", interval_seconds=60)

        try:
            triggers = get_trigger_engine().list()
            assert any(t.job_id == job.job_id for t in triggers)

        finally:
            get_scheduler().unregister(job.job_id)


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _registered_job(_reset_singletons):
    """
    The API builds its trigger engine wired to the shared job
    registry singleton, which validates job_id at registration time,
    so every API test needs "job-1" to actually exist there first.

    Explicitly depends on _reset_singletons so its setup (which clears
    the registry) always runs before this registers "job-1" — two
    autouse fixtures with no declared dependency between them are not
    otherwise guaranteed to run in file definition order.
    """

    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )

    get_job_registry().register("job-1", "a")


class TestGovernanceTriggerEngineApi:

    def test_get_triggers_returns_empty_list_initially(self, client) -> None:
        response = client.get("/governance/triggers")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_trigger_registers_a_new_trigger(self, client) -> None:
        response = client.post(
            "/governance/triggers",
            params={"job_id": "job-1", "trigger_type": "manual"},
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["job_id"] == "job-1"
        assert payload["trigger_type"] == "manual"

    def test_post_trigger_unknown_type_returns_409(self, client) -> None:
        response = client.post(
            "/governance/triggers",
            params={"job_id": "job-1", "trigger_type": "teleport"},
        )

        assert response.status_code == 409

    def test_get_trigger_by_id(self, client) -> None:
        create_response = client.post(
            "/governance/triggers",
            params={"job_id": "job-1", "trigger_type": "manual"},
        )
        trigger_id = create_response.json()["trigger_id"]

        response = client.get(f"/governance/triggers/{trigger_id}")

        assert response.status_code == 200
        assert response.json()["trigger_id"] == trigger_id

    def test_get_unknown_trigger_returns_404(self, client) -> None:
        response = client.get("/governance/triggers/ghost")

        assert response.status_code == 404

    def test_patch_trigger_reschedules(self, client) -> None:
        create_response = client.post(
            "/governance/triggers",
            params={"job_id": "job-1", "trigger_type": "interval"},
        )
        trigger_id = create_response.json()["trigger_id"]

        response = client.patch(
            f"/governance/triggers/{trigger_id}",
            params={"next_run": "2026-07-22T00:00:00Z"},
        )

        assert response.status_code == 200
        assert response.json()["next_run"].startswith("2026-07-22")

    def test_patch_unknown_trigger_returns_404(self, client) -> None:
        response = client.patch(
            "/governance/triggers/ghost",
            params={"next_run": "2026-07-22T00:00:00Z"},
        )

        assert response.status_code == 404

    def test_delete_trigger(self, client) -> None:
        create_response = client.post(
            "/governance/triggers",
            params={"job_id": "job-1", "trigger_type": "manual"},
        )
        trigger_id = create_response.json()["trigger_id"]

        response = client.delete(f"/governance/triggers/{trigger_id}")

        assert response.status_code == 200
        assert response.json() == {"removed": trigger_id}

    def test_delete_unknown_trigger_returns_404(self, client) -> None:
        response = client.delete("/governance/triggers/ghost")

        assert response.status_code == 404
