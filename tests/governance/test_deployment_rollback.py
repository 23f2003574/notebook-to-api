from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_blue_green import (
    BlueGreenDeploymentEngine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rollback import (
    DeploymentRollbackEngine,
    RollbackPlan,
    RollbackResult,
    get_rollback_engine,
)
from backend.observability.deployment_governance_rollout_manager import (
    DeploymentRolloutManager,
    get_rollout_manager,
)
from backend.observability.deployment_governance_traffic_router import (
    DeploymentTrafficRouter,
)
from backend.observability.deployment_governance_version_registry import (
    DeploymentVersionRegistry,
    get_version_registry,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

VALID_CHECKSUM = "a" * 64


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentRollbackEngine:
    return DeploymentRollbackEngine(clock=_clock, **kwargs)


def _registry_with_history(deployment_id: str) -> DeploymentVersionRegistry:
    registry = DeploymentVersionRegistry(clock=_clock)
    registry.register(deployment_id, "1.0.0", "a.tar.gz", VALID_CHECKSUM)
    registry.update(deployment_id, "1.1.0", "a2.tar.gz", VALID_CHECKSUM)

    return registry


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The rollback engine, rollout manager, and version registry are all
    process-wide singletons; most tests below construct their own
    fresh engine instead (see _engine), and only the singleton and API
    tests touch the shared instances, matching
    test_deployment_traffic_router.py's own fixture.
    """

    def _reset():
        get_rollback_engine().clear_history()
        get_rollout_manager().clear()
        get_version_registry().clear()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestRollbackPlan:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            RollbackPlan(
                deployment_id="", target_version="1.0.0",
                trigger="MANUAL_ROLLBACK_REQUEST", automatic=False,
                created_at=BASE_TIME,
            )

    def test_rejects_invalid_target_version(self):
        with pytest.raises(ValueError, match="target_version"):
            RollbackPlan(
                deployment_id="dep-1", target_version="bogus",
                trigger="MANUAL_ROLLBACK_REQUEST", automatic=False,
                created_at=BASE_TIME,
            )

    def test_rejects_empty_trigger(self):
        with pytest.raises(ValueError, match="trigger must not be empty"):
            RollbackPlan(
                deployment_id="dep-1", target_version="1.0.0",
                trigger="", automatic=False, created_at=BASE_TIME,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            RollbackPlan(
                deployment_id="dep-1", target_version="1.0.0",
                trigger="MANUAL_ROLLBACK_REQUEST", automatic=False,
                created_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_to_dict(self):
        plan = RollbackPlan(
            deployment_id="dep-1", target_version="1.0.0",
            trigger="MANUAL_ROLLBACK_REQUEST", automatic=True,
            created_at=BASE_TIME,
        )

        assert plan.to_dict() == {
            "deployment_id": "dep-1",
            "target_version": "1.0.0",
            "trigger": "MANUAL_ROLLBACK_REQUEST",
            "automatic": True,
            "created_at": BASE_TIME.isoformat(),
        }


class TestRollbackResult:

    def test_rejects_empty_restored_version(self):
        with pytest.raises(
            ValueError, match="restored_version must not be empty"
        ):
            RollbackResult(
                deployment_id="dep-1", previous_version="1.1.0",
                restored_version="", success=True,
                completed_at=BASE_TIME,
            )

    def test_rejects_naive_completed_at(self):
        with pytest.raises(
            ValueError, match="completed_at must be timezone-aware"
        ):
            RollbackResult(
                deployment_id="dep-1", previous_version="1.1.0",
                restored_version="1.0.0", success=True,
                completed_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_previous_version_may_be_empty(self):
        result = RollbackResult(
            deployment_id="dep-1", previous_version="",
            restored_version="1.0.0", success=True,
            completed_at=BASE_TIME,
        )

        assert result.previous_version == ""


# --- Rollback plan creation -----------------------------------------------


class TestCreatePlan:

    def test_create_plan_with_explicit_target(self):
        engine = _engine()

        plan = engine.create_plan("dep-1", target_version="1.0.0")

        assert plan.target_version == "1.0.0"
        assert plan.trigger == "MANUAL_ROLLBACK_REQUEST"
        assert plan.automatic is False

    def test_create_plan_resolves_target_from_registry(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)

        plan = engine.create_plan("dep-1")

        assert plan.target_version == "1.0.0"

    def test_create_plan_without_target_or_registry_raises(self):
        engine = _engine()

        with pytest.raises(ValueError, match="target_version must be"):
            engine.create_plan("dep-1")

    def test_create_plan_with_only_one_version_ever_registered_raises(self):
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        engine = _engine(version_registry=registry)

        with pytest.raises(ValueError, match="no previous version"):
            engine.create_plan("dep-1")

    def test_create_plan_rejects_unregistered_trigger(self):
        engine = _engine()

        with pytest.raises(ValueError, match="is not registered"):
            engine.create_plan(
                "dep-1", target_version="1.0.0", trigger="BOGUS",
            )

    def test_register_trigger_allows_custom_trigger(self):
        engine = _engine()
        engine.register_trigger("CUSTOM_SIGNAL")

        plan = engine.create_plan(
            "dep-1", target_version="1.0.0", trigger="CUSTOM_SIGNAL",
        )

        assert plan.trigger == "CUSTOM_SIGNAL"

    def test_create_plan_publishes_rollback_planned(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollback_planned", events.append)

        engine.create_plan("dep-1", target_version="1.0.0")

        assert len(events) == 1
        assert events[0].source == "dep-1"

    def test_create_plan_records_audit_entry(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )

        audit = GovernanceAuditService(clock=_clock)
        engine = _engine(audit_service=audit)

        engine.create_plan("dep-1", target_version="1.0.0")

        records = audit.latest(1)

        assert len(records) == 1
        assert records[0].action == "rollback_planned"


# --- Duplicate rollback prevention -----------------------------------


class TestDuplicatePrevention:

    def test_second_plan_while_first_is_active_is_rejected(self):
        engine = _engine()
        engine.create_plan("dep-1", target_version="1.0.0")

        with pytest.raises(ValueError, match="already has an active"):
            engine.create_plan("dep-1", target_version="1.1.0")

    def test_new_plan_allowed_after_execution(self):
        engine = _engine()
        engine.create_plan("dep-1", target_version="1.0.0")
        engine.execute("dep-1")

        plan = engine.create_plan("dep-1", target_version="1.1.0")

        assert plan.target_version == "1.1.0"

    def test_new_plan_allowed_after_cancellation(self):
        engine = _engine()
        engine.create_plan("dep-1", target_version="1.0.0")
        engine.cancel("dep-1")

        plan = engine.create_plan("dep-1", target_version="1.1.0")

        assert plan.target_version == "1.1.0"


# --- Invalid target rejection -------------------------------------------


class TestInvalidTargetRejection:

    def test_validate_target_true_with_no_registry_wired(self):
        engine = _engine()

        assert engine.validate_target("dep-1", "9.9.9") is True

    def test_validate_target_true_for_a_real_prior_version(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)

        assert engine.validate_target("dep-1", "1.0.0") is True

    def test_validate_target_false_for_a_version_never_registered(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)

        assert engine.validate_target("dep-1", "9.9.9") is False

    def test_validate_target_true_for_a_version_registered_before_removal(
        self,
    ):
        # A version's legitimacy as a rollback target comes from
        # having genuinely been registered at some point, not from
        # the deployment still being currently active — removal
        # doesn't erase that history.
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.remove("dep-1")

        engine = _engine(version_registry=registry)

        assert engine.validate_target("dep-1", "1.0.0") is True

    def test_create_plan_rejects_a_version_never_registered(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)

        with pytest.raises(ValueError, match="is not a valid rollback"):
            engine.create_plan("dep-1", target_version="9.9.9")


# --- Manual rollback -----------------------------------------------------


class TestManualRollback:

    def test_execute_returns_a_successful_result(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)
        engine.create_plan("dep-1", target_version="1.0.0")

        result = engine.execute("dep-1")

        assert result.success is True
        assert result.restored_version == "1.0.0"
        assert result.previous_version == "1.1.0"

    def test_execute_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.execute("dep-1")

    def test_execute_after_cancel_raises(self):
        engine = _engine()
        engine.create_plan("dep-1", target_version="1.0.0")
        engine.cancel("dep-1")

        with pytest.raises(ValueError, match="is not active"):
            engine.execute("dep-1")

    def test_execute_is_idempotent(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)
        engine.create_plan("dep-1", target_version="1.0.0")

        first = engine.execute("dep-1")
        second = engine.execute("dep-1")

        assert first == second

    def test_execute_shifts_traffic_to_the_target_version(self):
        router = DeploymentTrafficRouter(clock=_clock)
        router.configure(
            "dep-1", [("1.0.0", 0.0), ("1.1.0", 100.0)],
            strategy="CANARY",
        )

        engine = _engine(traffic_router=router)
        engine.create_plan("dep-1", target_version="1.0.0")

        engine.execute("dep-1")

        snapshot = router.snapshot("dep-1")
        allocation_by_version = {
            a.version: a.percentage for a in snapshot.allocations
        }

        assert allocation_by_version["1.0.0"] == 100.0

    def test_execute_cancels_a_matching_active_rollout(self):
        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout = rollout_manager.create("dep-1", "CANARY")
        rollout_manager.start(rollout.rollout_id)

        engine = _engine(rollout_manager=rollout_manager)
        engine.create_plan("dep-1", target_version="1.0.0")

        engine.execute("dep-1")

        assert rollout_manager.status(rollout.rollout_id).state == (
            "CANCELLED"
        )

    def test_execute_does_not_touch_unrelated_rollouts(self):
        rollout_manager = DeploymentRolloutManager(clock=_clock)
        other = rollout_manager.create("dep-other", "CANARY")
        rollout_manager.start(other.rollout_id)

        engine = _engine(rollout_manager=rollout_manager)
        engine.create_plan("dep-1", target_version="1.0.0")

        engine.execute("dep-1")

        assert rollout_manager.status(other.rollout_id).state == (
            "RUNNING"
        )

    def test_execute_rolls_back_an_active_blue_green_deployment(self):
        blue_green = BlueGreenDeploymentEngine(clock=_clock)
        blue_green.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        blue_green.validate("dep-1")
        blue_green.switch("dep-1")

        engine = _engine(blue_green_engine=blue_green)
        engine.create_plan("dep-1", target_version="1.0.0")

        engine.execute("dep-1")

        assert blue_green.status("dep-1").active_environment == "BLUE"

    def test_execute_with_nothing_staged_in_any_engine_does_not_fail(self):
        engine = _engine(
            traffic_router=DeploymentTrafficRouter(clock=_clock),
            rollout_manager=DeploymentRolloutManager(clock=_clock),
            blue_green_engine=BlueGreenDeploymentEngine(clock=_clock),
        )
        engine.create_plan("dep-1", target_version="1.0.0")

        result = engine.execute("dep-1")

        assert result.success is True

    def test_execute_records_audit_entry(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )

        audit = GovernanceAuditService(clock=_clock)
        engine = _engine(audit_service=audit)
        engine.create_plan("dep-1", target_version="1.0.0")

        engine.execute("dep-1")

        actions = [record.action for record in audit.latest(10)]

        assert "rollback_completed" in actions

    def test_execute_publishes_started_and_completed(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.create_plan("dep-1", target_version="1.0.0")

        started_events = []
        completed_events = []
        bus.subscribe("rollback_started", started_events.append)
        bus.subscribe("rollback_completed", completed_events.append)

        engine.execute("dep-1")

        assert len(started_events) == 1
        assert len(completed_events) == 1


# --- Automatic rollback ---------------------------------------------------


class TestAutomaticRollback:

    def test_subscribes_to_rollout_failed_and_triggers_a_rollback(self):
        bus = GovernanceEventBus(clock=_clock)
        registry = _registry_with_history("dep-1")

        engine = _engine(event_bus=bus, version_registry=registry)

        bus.publish(
            "rollout_failed", "rollout-1", {"deployment_id": "dep-1"},
        )

        plan = engine.status("dep-1")

        assert plan.automatic is True
        assert plan.trigger == "HEALTH_CHECK_FAILURE"

        result = engine.latest("dep-1")

        assert result.success is True

    def test_rollout_failed_event_without_deployment_id_is_ignored(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        bus.publish("rollout_failed", "rollout-1", {})

        with pytest.raises(KeyError):
            engine.status("dep-1")

    def test_rollout_failed_with_no_resolvable_target_does_not_raise(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        # No version_registry wired, so target_version cannot be
        # resolved automatically; the handler must swallow this
        # rather than letting it propagate out of the event bus.
        bus.publish(
            "rollout_failed", "rollout-1", {"deployment_id": "dep-1"},
        )

        with pytest.raises(KeyError):
            engine.status("dep-1")

    def test_second_rollout_failed_event_does_not_raise(self):
        bus = GovernanceEventBus(clock=_clock)
        registry = _registry_with_history("dep-1")
        engine = _engine(event_bus=bus, version_registry=registry)

        bus.publish(
            "rollout_failed", "rollout-1", {"deployment_id": "dep-1"},
        )
        # The plan from the first event has already executed and
        # freed dep-1, so this one plans (and executes) again rather
        # than raising "already active" — this only asserts the bus
        # dispatch itself never raises out of the handler.
        bus.publish(
            "rollout_failed", "rollout-2", {"deployment_id": "dep-1"},
        )


# --- Rollback history -----------------------------------------------------


class TestHistory:

    def test_history_empty_before_execution(self):
        engine = _engine()
        engine.create_plan("dep-1", target_version="1.0.0")

        assert engine.history("dep-1") == ()

    def test_history_empty_for_unknown_deployment(self):
        engine = _engine()

        assert engine.history("dep-1") == ()

    def test_history_accumulates_across_plans(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)

        engine.create_plan("dep-1", target_version="1.0.0")
        engine.execute("dep-1")

        registry.update("dep-1", "1.2.0", "a3.tar.gz", VALID_CHECKSUM)
        engine.create_plan("dep-1", target_version="1.1.0")
        engine.execute("dep-1")

        assert len(engine.history("dep-1")) == 2

    def test_latest_returns_the_most_recent_result(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)
        engine.create_plan("dep-1", target_version="1.0.0")

        engine.execute("dep-1")

        assert engine.latest("dep-1").restored_version == "1.0.0"

    def test_latest_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.latest("dep-1")

    def test_status_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.status("dep-1")

    def test_list_orders_by_deployment_id(self):
        engine = _engine()
        engine.create_plan("dep-b", target_version="1.0.0")
        engine.create_plan("dep-a", target_version="1.0.0")

        listed = engine.list()

        assert [p.deployment_id for p in listed] == ["dep-a", "dep-b"]


class TestCancel:

    def test_cancel_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.cancel("dep-1")

    def test_cancel_after_execution_raises(self):
        registry = _registry_with_history("dep-1")
        engine = _engine(version_registry=registry)
        engine.create_plan("dep-1", target_version="1.0.0")
        engine.execute("dep-1")

        with pytest.raises(ValueError, match="already been executed"):
            engine.cancel("dep-1")

    def test_cancel_is_idempotent(self):
        engine = _engine()
        engine.create_plan("dep-1", target_version="1.0.0")

        engine.cancel("dep-1")
        plan = engine.cancel("dep-1")

        assert plan.deployment_id == "dep-1"

    def test_cancel_publishes_only_on_first_cancel(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.create_plan("dep-1", target_version="1.0.0")

        events = []
        bus.subscribe("rollback_cancelled", events.append)

        engine.cancel("dep-1")
        engine.cancel("dep-1")

        assert len(events) == 1


# --- Event publication -----------------------------------------------------


class TestEventPublication:

    def test_no_event_bus_is_safe(self):
        engine = _engine(event_bus=None)

        engine.create_plan("dep-1", target_version="1.0.0")
        engine.execute("dep-1")

        engine.create_plan("dep-2", target_version="1.0.0")
        engine.cancel("dep-2")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_rollback_engine_returns_same_instance(self):
        assert get_rollback_engine() is get_rollback_engine()

    def test_singleton_is_wired_to_singleton_rollout_manager(self):
        assert (
            get_rollback_engine()._rollout_manager
            is get_rollout_manager()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


def _register(deployment_id: str, version: str = "1.0.0") -> None:
    """
    The process-wide rollback engine is wired to the process-wide
    version registry (see build_default_governance_rollback_engine),
    so validate_target() requires deployment_id to already have
    version registered there before any API test can create a plan
    targeting it.
    """

    get_version_registry().register(
        deployment_id, version, "artifact.tar.gz", VALID_CHECKSUM,
    )


class TestGovernanceRollbackApi:

    def test_post_triggers_a_manual_rollback(self, client):
        get_version_registry().register(
            "dep-api-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM,
        )
        get_version_registry().update(
            "dep-api-1", "1.1.0", "a2.tar.gz", VALID_CHECKSUM,
        )

        response = client.post(
            "/governance/rollbacks/dep-api-1",
            params={"target_version": "1.0.0"},
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["restored_version"] == "1.0.0"
        assert payload["success"] is True

    def test_post_resolves_target_automatically(self, client):
        get_version_registry().register(
            "dep-api-2", "1.0.0", "a.tar.gz", VALID_CHECKSUM,
        )
        get_version_registry().update(
            "dep-api-2", "1.1.0", "a2.tar.gz", VALID_CHECKSUM,
        )

        response = client.post("/governance/rollbacks/dep-api-2")

        assert response.status_code == 200
        assert response.json()["restored_version"] == "1.0.0"

    def test_post_with_no_resolvable_target_returns_409(self, client):
        response = client.post("/governance/rollbacks/dep-api-3")

        assert response.status_code == 409

    def test_get_status(self, client):
        _register("dep-api-4")

        client.post(
            "/governance/rollbacks/dep-api-4",
            params={"target_version": "1.0.0"},
        )

        response = client.get("/governance/rollbacks/dep-api-4")

        assert response.status_code == 200
        assert response.json()["target_version"] == "1.0.0"

    def test_get_unknown_deployment_returns_404(self, client):
        response = client.get("/governance/rollbacks/does-not-exist")

        assert response.status_code == 404

    def test_list_rollbacks(self, client):
        _register("dep-api-5")

        client.post(
            "/governance/rollbacks/dep-api-5",
            params={"target_version": "1.0.0"},
        )

        response = client.get("/governance/rollbacks")

        assert response.status_code == 200
        assert any(
            p["deployment_id"] == "dep-api-5" for p in response.json()
        )

    def test_delete_after_execution_returns_409(self, client):
        _register("dep-api-6")

        client.post(
            "/governance/rollbacks/dep-api-6",
            params={"target_version": "1.0.0"},
        )

        response = client.delete("/governance/rollbacks/dep-api-6")

        assert response.status_code == 409

    def test_delete_unknown_deployment_returns_404(self, client):
        response = client.delete("/governance/rollbacks/does-not-exist")

        assert response.status_code == 404
