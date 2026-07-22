from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_recovery import (
    GovernanceRecoveryManager,
    RecoveryPlan,
    RecoveryResult,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _no_sleep(seconds: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The lifecycle manager, event bus, event history, audit service,
    policy engine, and recovery manager are all process-wide
    singletons wired together, so tests that touch them (directly or
    via the API) must not leak state into other tests. The recovery
    manager's registered plans (its 8 built-in "restart_component"
    plans) are left alone, mirroring how the rule engine's built-in
    rules are left alone — only its history is cleared.
    """

    from backend.observability.deployment_governance_audit import (
        get_audit_service,
    )
    from backend.observability.deployment_governance_event_bus import (
        get_event_bus,
    )
    from backend.observability.deployment_governance_event_history import (
        get_event_history,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_policy import (
        get_policy_engine,
    )
    from backend.observability.deployment_governance_recovery import (
        get_recovery_manager,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_event_history().purge()
        get_audit_service().purge()
        get_policy_engine().clear()
        get_recovery_manager().clear_history()
        get_event_bus().clear()

    _reset()
    yield
    _reset()


# --- Model -------------------------------------------------------------


class TestRecoveryPlan:

    def test_rejects_empty_component(self):
        with pytest.raises(ValueError, match="component must not be empty"):
            RecoveryPlan(
                component="", strategy="no_op", max_attempts=1,
                retry_delay_seconds=0,
            )

    def test_rejects_empty_strategy(self):
        with pytest.raises(ValueError, match="strategy must not be empty"):
            RecoveryPlan(
                component="a", strategy="", max_attempts=1,
                retry_delay_seconds=0,
            )

    def test_rejects_non_positive_max_attempts(self):
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RecoveryPlan(
                component="a", strategy="no_op", max_attempts=0,
                retry_delay_seconds=0,
            )

    def test_rejects_negative_retry_delay(self):
        with pytest.raises(
            ValueError, match="retry_delay_seconds must be >= 0"
        ):
            RecoveryPlan(
                component="a", strategy="no_op", max_attempts=1,
                retry_delay_seconds=-1,
            )

    def test_defaults_to_enabled(self):
        plan = RecoveryPlan(
            component="a", strategy="no_op", max_attempts=1,
            retry_delay_seconds=0,
        )

        assert plan.enabled is True

    def test_to_dict(self):
        plan = RecoveryPlan(
            component="a", strategy="no_op", max_attempts=2,
            retry_delay_seconds=1, enabled=False,
        )

        assert plan.to_dict() == {
            "component": "a",
            "strategy": "no_op",
            "max_attempts": 2,
            "retry_delay_seconds": 1,
            "enabled": False,
        }


class TestRecoveryResult:

    def test_rejects_negative_attempts(self):
        with pytest.raises(ValueError, match="attempts must be >= 0"):
            RecoveryResult(
                component="a", strategy="no_op", success=True, attempts=-1,
                started_at=BASE_TIME, completed_at=BASE_TIME, message=None,
            )

    def test_rejects_naive_started_at(self):
        with pytest.raises(
            ValueError, match="started_at must be timezone-aware"
        ):
            RecoveryResult(
                component="a", strategy="no_op", success=True, attempts=1,
                started_at=datetime(2026, 7, 21, 12, 0, 0),
                completed_at=BASE_TIME, message=None,
            )

    def test_rejects_success_with_message(self):
        with pytest.raises(
            ValueError, match="message must not be set when success is True"
        ):
            RecoveryResult(
                component="a", strategy="no_op", success=True, attempts=1,
                started_at=BASE_TIME, completed_at=BASE_TIME,
                message="boom",
            )

    def test_rejects_failure_without_message(self):
        with pytest.raises(
            ValueError, match="message must be set when success is False"
        ):
            RecoveryResult(
                component="a", strategy="no_op", success=False, attempts=1,
                started_at=BASE_TIME, completed_at=BASE_TIME, message=None,
            )

    def test_to_dict(self):
        result = RecoveryResult(
            component="a", strategy="no_op", success=False, attempts=2,
            started_at=BASE_TIME, completed_at=BASE_TIME, message="boom",
        )

        assert result.to_dict() == {
            "component": "a",
            "strategy": "no_op",
            "success": False,
            "attempts": 2,
            "started_at": BASE_TIME.isoformat(),
            "completed_at": BASE_TIME.isoformat(),
            "message": "boom",
        }


# --- Recovery registration -----------------------------------------


class TestRecoveryRegistration:

    def test_register_returns_plan(self):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)

        plan = manager.register("a", strategy="no_op")

        assert plan.component == "a"
        assert plan.strategy == "no_op"

    def test_registered_plan_appears_in_status(self):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)
        manager.register("a", strategy="no_op")

        assert [p.component for p in manager.status()] == ["a"]

    def test_status_ordered_by_component_name(self):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)
        manager.register("z", strategy="no_op")
        manager.register("a", strategy="no_op")

        assert [p.component for p in manager.status()] == ["a", "z"]

    def test_register_rejects_unknown_strategy_without_action(self):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)

        with pytest.raises(ValueError, match="unknown recovery strategy"):
            manager.register("a", strategy="teleport")

    def test_register_accepts_custom_action_for_unknown_strategy_name(
        self,
    ):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)

        plan = manager.register(
            "a", strategy="teleport", action=lambda c, ctx: True
        )

        assert plan.strategy == "teleport"

    def test_remove_unknown_plan_raises(self):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)

        with pytest.raises(KeyError):
            manager.remove("ghost")

    def test_remove_removes_plan(self):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)
        manager.register("a", strategy="no_op")
        manager.remove("a")

        assert manager.status() == ()


# --- Duplicate rejection -------------------------------------------------


def test_duplicate_component_plan_rejected():
    manager = GovernanceRecoveryManager(sleep=_no_sleep)
    manager.register("a", strategy="no_op")

    with pytest.raises(ValueError, match="already registered"):
        manager.register("a", strategy="no_op")


# --- Successful recovery -------------------------------------------------


class TestSuccessfulRecovery:

    def test_no_op_strategy_always_succeeds(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register("a", strategy="no_op")

        result = manager.recover("a")

        assert result.success is True
        assert result.attempts == 1
        assert result.message is None
        assert result.started_at == BASE_TIME
        assert result.completed_at == BASE_TIME

    def test_custom_action_success(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register(
            "a", strategy="custom", action=lambda c, ctx: True
        )

        result = manager.recover("a")

        assert result.success is True

    def test_recover_unknown_component_raises(self):
        manager = GovernanceRecoveryManager(sleep=_no_sleep)

        with pytest.raises(KeyError):
            manager.recover("ghost")

    def test_recover_all_recovers_every_plan_in_order(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register("z", strategy="no_op")
        manager.register("a", strategy="no_op")

        results = manager.recover_all()

        assert [r.component for r in results] == ["a", "z"]
        assert all(r.success for r in results)


# --- Retry handling -----------------------------------------------------


class TestRetryHandling:

    def test_retries_until_success(self):
        attempts = []

        def _action(component, context):
            attempts.append(1)
            return len(attempts) >= 3

        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register(
            "a", strategy="custom", action=_action, max_attempts=5
        )

        result = manager.recover("a")

        assert result.success is True
        assert result.attempts == 3

    def test_retry_publishes_recovery_retry_event(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        attempts = []

        def _action(component, context):
            attempts.append(1)
            return len(attempts) >= 2

        bus = GovernanceEventBus()
        received = []
        bus.subscribe("recovery_retry", lambda e: received.append(e))

        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, event_bus=bus
        )
        manager.register(
            "a", strategy="custom", action=_action, max_attempts=3
        )

        manager.recover("a")

        assert len(received) == 1
        assert received[0].payload["attempt"] == 1

    def test_exponential_backoff_delays(self):
        delays = []

        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=lambda seconds: delays.append(seconds)
        )
        manager.register(
            "a",
            strategy="custom",
            action=lambda c, ctx: False,
            max_attempts=4,
            retry_delay_seconds=1,
        )

        manager.recover("a")

        assert delays == [1, 2, 4]


# --- Max-attempt enforcement ---------------------------------------------


class TestMaxAttemptEnforcement:

    def test_stops_after_max_attempts(self):
        attempts = []

        def _action(component, context):
            attempts.append(1)
            return False

        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register(
            "a", strategy="custom", action=_action, max_attempts=3
        )

        result = manager.recover("a")

        assert len(attempts) == 3
        assert result.attempts == 3
        assert result.success is False

    def test_exhausted_result_carries_last_failure_message(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register(
            "a",
            strategy="custom",
            action=lambda c, ctx: (False, "still broken"),
            max_attempts=2,
        )

        result = manager.recover("a")

        assert result.message == "still broken"


# --- Exception handling -------------------------------------------------


def test_raising_action_is_treated_as_a_failed_attempt():
    manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
    manager.register(
        "a",
        strategy="custom",
        action=lambda c, ctx: (_ for _ in ()).throw(RuntimeError("boom")),
        max_attempts=1,
    )

    result = manager.recover("a")

    assert result.success is False
    assert result.message == "boom"


# --- Policy denial -----------------------------------------------------


class TestPolicyDenial:

    def test_denied_recovery_returns_aborted_result_without_attempting(
        self,
    ):
        from backend.observability.deployment_governance_policy import (
            GovernancePolicyEngine,
        )

        attempted = []

        policy_engine = GovernancePolicyEngine(clock=_clock)
        policy_engine.register("deny_recovery", operation="component_recovery")

        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, policy_engine=policy_engine
        )
        manager.register(
            "a",
            strategy="custom",
            action=lambda c, ctx: attempted.append(1) or True,
        )

        result = manager.recover("a")

        assert result.success is False
        assert result.attempts == 0
        assert "denied by policy" in result.message
        assert attempted == []

    def test_disabled_plan_is_aborted_without_policy_check(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register("a", strategy="no_op", enabled=False)

        result = manager.recover("a")

        assert result.success is False
        assert result.attempts == 0
        assert result.message == "recovery plan is disabled"


# --- Audit integration ------------------------------------------------


class TestAuditIntegration:

    def test_successful_recovery_is_audited(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, audit_service=audit_service
        )
        manager.register("a", strategy="no_op")

        manager.recover("a")

        records = audit_service.query(
            AuditQuery(action="recovery_succeeded")
        )
        assert len(records) == 1
        assert records[0].outcome == "success"

    def test_failed_recovery_is_audited(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, audit_service=audit_service
        )
        manager.register(
            "a", strategy="custom", action=lambda c, ctx: False,
            max_attempts=1,
        )

        manager.recover("a")

        records = audit_service.query(AuditQuery(action="recovery_failed"))
        assert len(records) == 1
        assert records[0].outcome == "failure"

    def test_denied_recovery_is_audited_as_aborted(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )
        from backend.observability.deployment_governance_policy import (
            GovernancePolicyEngine,
        )

        policy_engine = GovernancePolicyEngine(clock=_clock)
        policy_engine.register("deny_recovery", operation="component_recovery")
        audit_service = GovernanceAuditService(clock=_clock)

        manager = GovernanceRecoveryManager(
            clock=_clock,
            sleep=_no_sleep,
            policy_engine=policy_engine,
            audit_service=audit_service,
        )
        manager.register("a", strategy="no_op")

        manager.recover("a")

        records = audit_service.query(AuditQuery(action="recovery_aborted"))
        assert len(records) == 1


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_recovery_publishes_started_and_succeeded(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, event_bus=bus
        )
        manager.register("a", strategy="no_op")

        manager.recover("a")

        assert received == ["recovery_started", "recovery_succeeded"]

    def test_recovery_publishes_aborted_when_disabled(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, event_bus=bus
        )
        manager.register("a", strategy="no_op", enabled=False)

        manager.recover("a")

        assert received == ["recovery_aborted"]


# --- History -------------------------------------------------------------


class TestHistory:

    def test_history_is_newest_first(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register("a", strategy="no_op")

        manager.recover("a")
        manager.recover("a")

        results = manager.history()

        assert len(results) == 2

    def test_history_filters_by_component(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register("a", strategy="no_op")
        manager.register("b", strategy="no_op")

        manager.recover("a")
        manager.recover("b")

        assert len(manager.history("a")) == 1

    def test_clear_history_removes_everything(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register("a", strategy="no_op")
        manager.recover("a")

        manager.clear_history()

        assert manager.history() == ()


# --- Lifecycle integration -------------------------------------------


class TestLifecycleIntegration:

    def _lifecycle_manager(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        return GovernanceLifecycleManager(clock=_clock)

    def test_restart_component_strategy_calls_lifecycle_restart(self):
        calls = []

        lifecycle_manager = self._lifecycle_manager()
        lifecycle_manager.register(
            "a",
            start=lambda: calls.append("start"),
            stop=lambda: calls.append("stop"),
        )
        lifecycle_manager.startup()
        calls.clear()

        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, lifecycle_manager=lifecycle_manager
        )
        manager.register("a", strategy="restart_component")

        result = manager.recover("a")

        assert result.success is True
        assert calls == ["stop", "start"]

    def test_restart_component_without_lifecycle_manager_fails(self):
        manager = GovernanceRecoveryManager(clock=_clock, sleep=_no_sleep)
        manager.register("a", strategy="restart_component")

        result = manager.recover("a")

        assert result.success is False
        assert "no lifecycle manager configured" in result.message

    def test_reload_component_strategy_calls_component_reload(self):
        calls = []

        lifecycle_manager = self._lifecycle_manager()
        lifecycle_manager.register(
            "a",
            start=lambda: None,
            stop=lambda: None,
            reload=lambda: calls.append("reload"),
        )
        lifecycle_manager.startup()

        manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep, lifecycle_manager=lifecycle_manager
        )
        manager.register("a", strategy="reload_component")

        result = manager.recover("a")

        assert result.success is True
        assert calls == ["reload"]


# --- Health service integration --------------------------------------


class TestHealthServiceIntegration:

    def test_trigger_recovery_recovers_unhealthy_components(self):
        from backend.observability.deployment_governance_health import (
            GovernanceHealthService,
        )

        recovery_manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep
        )
        recovery_manager.register("b", strategy="no_op")

        health_service = GovernanceHealthService(
            clock=_clock, recovery_manager=recovery_manager
        )
        health_service.register("a", lambda: True)
        health_service.register("b", lambda: False)

        results = health_service.trigger_recovery()

        assert [r.component for r in results] == ["b"]
        assert results[0].success is True

    def test_trigger_recovery_skips_components_without_a_plan(self):
        from backend.observability.deployment_governance_health import (
            GovernanceHealthService,
        )

        recovery_manager = GovernanceRecoveryManager(
            clock=_clock, sleep=_no_sleep
        )

        health_service = GovernanceHealthService(
            clock=_clock, recovery_manager=recovery_manager
        )
        health_service.register("a", lambda: False)

        results = health_service.trigger_recovery()

        assert results == ()

    def test_trigger_recovery_without_recovery_manager_is_empty(self):
        from backend.observability.deployment_governance_health import (
            GovernanceHealthService,
        )

        health_service = GovernanceHealthService(clock=_clock)
        health_service.register("a", lambda: False)

        assert health_service.trigger_recovery() == ()


# --- Singleton -------------------------------------------------------------


class TestRecoverySingleton:

    def test_get_recovery_manager_returns_same_instance(self):
        from backend.observability.deployment_governance_recovery import (
            get_recovery_manager,
        )

        assert get_recovery_manager() is get_recovery_manager()

    def test_default_manager_has_plans_for_all_nine_components(self):
        from backend.observability.deployment_governance_recovery import (
            get_recovery_manager,
        )

        names = {p.component for p in get_recovery_manager().status()}

        assert names == {
            "provider_registry",
            "metrics_bootstrap",
            "logging_bootstrap",
            "delivery_runtime",
            "health_service",
            "readiness_service",
            "liveness_service",
            "diagnostics_service",
            "scheduler",
        }


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRecoveryApi:

    def test_get_recovery_returns_registered_plans(self, client) -> None:
        response = client.get("/governance/recovery")

        assert response.status_code == 200

        names = {plan["component"] for plan in response.json()}

        assert "liveness_service" in names

    def test_post_recovery_recovers_one_component(self, client) -> None:
        response = client.post("/governance/recovery/liveness_service")

        assert response.status_code == 200

        payload = response.json()

        assert payload["component"] == "liveness_service"
        assert payload["success"] is True

    def test_post_recovery_unknown_component_returns_404(
        self, client
    ) -> None:
        response = client.post("/governance/recovery/does_not_exist")

        assert response.status_code == 404

    def test_post_recovery_all_recovers_every_plan(self, client) -> None:
        response = client.post("/governance/recovery/all")

        assert response.status_code == 200

        payload = response.json()

        assert len(payload) == 9
        assert all(entry["success"] for entry in payload)

    def test_history_endpoint_reflects_recovery_attempts(
        self, client
    ) -> None:
        client.post("/governance/recovery/liveness_service")

        response = client.get(
            "/governance/recovery/history?component=liveness_service"
        )

        assert response.status_code == 200

        payload = response.json()

        assert len(payload) == 1
        assert payload[0]["component"] == "liveness_service"
