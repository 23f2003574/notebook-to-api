from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rollout_analytics import (
    DeploymentRolloutAnalytics,
)
from backend.observability.deployment_governance_rollout_policy import (
    BUILT_IN_ROLLOUT_POLICIES,
    DeploymentRolloutPolicyEngine,
    RolloutPolicy,
    RolloutPolicyDecision,
    get_rollout_policy_engine,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentRolloutPolicyEngine:
    return DeploymentRolloutPolicyEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The rollout policy engine is a process-wide singleton; most tests
    below construct their own fresh engine instead (see _engine), and
    only the singleton and API tests touch the shared instance,
    matching test_deployment_rollback.py's own fixture.
    """

    def _reset():
        get_rollout_policy_engine().clear()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestRolloutPolicy:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            RolloutPolicy(name="", priority=0, enabled=True)

    def test_defaults(self):
        policy = RolloutPolicy(name="p", priority=0, enabled=True)

        assert policy.strategy is None
        assert dict(policy.conditions) == {}

    def test_to_dict(self):
        policy = RolloutPolicy(
            name="p", priority=5, enabled=True, strategy="CANARY",
            conditions={"max_concurrent": 2},
        )

        assert policy.to_dict() == {
            "name": "p",
            "priority": 5,
            "enabled": True,
            "strategy": "CANARY",
            "conditions": {"max_concurrent": 2},
        }


class TestRolloutPolicyDecision:

    def test_rejects_empty_action(self):
        with pytest.raises(ValueError, match="action must not be empty"):
            RolloutPolicyDecision(
                allowed=True, policy=None, action="", reason=None,
                evaluated_at=BASE_TIME,
            )

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            RolloutPolicyDecision(
                allowed=True, policy=None, action="rollout_start",
                reason=None,
                evaluated_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_allowed_rejects_policy_set(self):
        with pytest.raises(
            ValueError, match="must not be set when allowed is True"
        ):
            RolloutPolicyDecision(
                allowed=True, policy="p", action="rollout_start",
                reason=None, evaluated_at=BASE_TIME,
            )

    def test_denied_requires_policy_and_reason(self):
        with pytest.raises(
            ValueError, match="must be set when allowed is False"
        ):
            RolloutPolicyDecision(
                allowed=False, policy=None, action="rollout_start",
                reason=None, evaluated_at=BASE_TIME,
            )

    def test_to_dict(self):
        decision = RolloutPolicyDecision(
            allowed=False, policy="p", action="rollout_start",
            reason="denied", evaluated_at=BASE_TIME,
        )

        assert decision.to_dict() == {
            "allowed": False,
            "policy": "p",
            "action": "rollout_start",
            "reason": "denied",
            "evaluated_at": BASE_TIME.isoformat(),
        }


# --- Policy registration -------------------------------------------


class TestPolicyRegistration:

    def test_register_returns_the_policy(self):
        engine = _engine()

        policy = engine.register("p", priority=1, conditions={"x": 1})

        assert policy.name == "p"
        assert policy.priority == 1

    def test_register_with_builtin_policy_type(self):
        engine = _engine()

        policy = engine.register(
            "p", policy_type="max_concurrent_rollouts",
            conditions={"max_concurrent": 1},
        )

        assert policy.name == "p"

    def test_register_with_unknown_policy_type_raises(self):
        engine = _engine()

        with pytest.raises(ValueError, match="unknown built-in"):
            engine.register("p", policy_type="bogus")

    def test_every_builtin_policy_type_is_registerable(self):
        engine = _engine()

        for i, policy_type in enumerate(BUILT_IN_ROLLOUT_POLICIES):
            policy = engine.register(f"p-{i}", policy_type=policy_type)
            assert policy.name == f"p-{i}"

    def test_register_publishes_rollout_policy_registered(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollout_policy_registered", events.append)

        engine.register("p")

        assert len(events) == 1
        assert events[0].source == "p"


# --- Duplicate rejection ------------------------------------------------


class TestDuplicateRejection:

    def test_duplicate_name_is_rejected(self):
        engine = _engine()
        engine.register("p")

        with pytest.raises(ValueError, match="is already registered"):
            engine.register("p")

    def test_reuse_after_remove(self):
        engine = _engine()
        engine.register("p")
        engine.remove("p")

        policy = engine.register("p", priority=9)

        assert policy.priority == 9


class TestRemove:

    def test_remove_unknown_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.remove("does-not-exist")

    def test_remove_publishes_rollout_policy_removed(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.register("p")

        events = []
        bus.subscribe("rollout_policy_removed", events.append)

        engine.remove("p")

        assert len(events) == 1


# --- Priority ordering -----------------------------------------------


class TestPriorityOrdering:

    def test_list_orders_by_priority_then_name(self):
        engine = _engine()
        engine.register("z", priority=1)
        engine.register("a", priority=1)
        engine.register("b", priority=0)

        listed = engine.list()

        assert [p.name for p in listed] == ["b", "a", "z"]

    def test_evaluation_is_deterministic_and_short_circuits(self):
        engine = _engine()
        calls = []

        def _always_deny(policy, context):
            calls.append(policy.name)
            return True, f"{policy.name} denied it"

        def _never_deny(policy, context):
            calls.append(policy.name)
            return False, None

        engine.register("second", priority=1, evaluator=_never_deny)
        engine.register("first", priority=0, evaluator=_always_deny)

        decision = engine.evaluate("dep-1", "rollout_start")

        assert decision.policy == "first"
        assert calls == ["first"]  # "second" never evaluated


# --- Allow / deny decisions -----------------------------------------


class TestAllowDecision:

    def test_no_policies_registered_allows(self):
        engine = _engine()

        decision = engine.evaluate("dep-1", "rollout_start")

        assert decision.allowed is True
        assert decision.policy is None
        assert decision.reason is None

    def test_evaluate_rejects_empty_action(self):
        engine = _engine()

        with pytest.raises(ValueError, match="action must not be empty"):
            engine.evaluate("dep-1", "")

    def test_publishes_rollout_policy_allowed(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("rollout_policy_allowed", events.append)

        engine.evaluate("dep-1", "rollout_start")

        assert len(events) == 1


class TestDenyDecision:

    def test_matching_condition_denies(self):
        engine = _engine()
        engine.register("freeze-all", conditions={"strategy": "CANARY"})

        decision = engine.evaluate(
            "dep-1", "rollout_start", {"strategy": "CANARY"},
        )

        assert decision.allowed is False
        assert decision.policy == "freeze-all"

    def test_disabled_policy_never_matches(self):
        engine = _engine()
        engine.register(
            "freeze-all", enabled=False,
            conditions={"strategy": "CANARY"},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_start", {"strategy": "CANARY"},
        )

        assert decision.allowed is True

    def test_policy_scoped_to_a_different_strategy_is_skipped(self):
        engine = _engine()
        engine.register(
            "canary-only", strategy="CANARY",
            conditions={"active_rollouts": 0},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_start",
            {"strategy": "ROLLING", "active_rollouts": 0},
        )

        assert decision.allowed is True

    def test_universal_policy_applies_to_every_strategy(self):
        engine = _engine()
        engine.register("universal", conditions={"blocked": True})

        decision = engine.evaluate(
            "dep-1", "rollout_start",
            {"strategy": "ROLLING", "blocked": True},
        )

        assert decision.allowed is False
        assert decision.policy == "universal"

    def test_publishes_rollout_policy_denied(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.register("deny-all", conditions={})

        events = []
        bus.subscribe("rollout_policy_denied", events.append)

        engine.evaluate("dep-1", "rollout_start")

        assert len(events) == 1

    def test_evaluate_all_orders_by_deployment_id(self):
        engine = _engine()
        engine.register("deny-canary", conditions={"strategy": "CANARY"})

        decisions = engine.evaluate_all(
            "rollout_start",
            {
                "dep-b": {"strategy": "CANARY"},
                "dep-a": {"strategy": "ROLLING"},
            },
        )

        assert [d.allowed for d in decisions] == [True, False]


# --- Built-in policies -------------------------------------------------


class TestBuiltinMaxConcurrentRollouts:

    def test_denies_when_at_or_over_the_limit(self):
        engine = _engine()
        engine.register(
            "p", policy_type="max_concurrent_rollouts",
            conditions={"max_concurrent": 2},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_creation", {"active_rollouts": 2},
        )

        assert decision.allowed is False

    def test_allows_when_under_the_limit(self):
        engine = _engine()
        engine.register(
            "p", policy_type="max_concurrent_rollouts",
            conditions={"max_concurrent": 2},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_creation", {"active_rollouts": 1},
        )

        assert decision.allowed is True

    def test_no_op_without_max_concurrent_condition(self):
        engine = _engine()
        engine.register("p", policy_type="max_concurrent_rollouts")

        decision = engine.evaluate(
            "dep-1", "rollout_creation", {"active_rollouts": 1000},
        )

        assert decision.allowed is True


class TestBuiltinRequiredHealthScore:

    def test_denies_below_minimum(self):
        engine = _engine()
        engine.register(
            "p", policy_type="required_health_score",
            conditions={"min_score": 80},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_promotion", {"health_score": 50},
        )

        assert decision.allowed is False

    def test_allows_at_or_above_minimum(self):
        engine = _engine()
        engine.register(
            "p", policy_type="required_health_score",
            conditions={"min_score": 80},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_promotion", {"health_score": 90},
        )

        assert decision.allowed is True

    def test_no_op_without_health_score_in_context(self):
        engine = _engine()
        engine.register(
            "p", policy_type="required_health_score",
            conditions={"min_score": 80},
        )

        decision = engine.evaluate("dep-1", "rollout_promotion", {})

        assert decision.allowed is True


class TestBuiltinMaxRollbackRate:

    def test_denies_above_the_max_rate(self):
        engine = _engine()
        engine.register(
            "p", policy_type="max_rollback_rate",
            conditions={"max_rate": 0.1},
        )

        decision = engine.evaluate(
            "dep-1", "rollback_execution", {"rollback_rate": 0.5},
        )

        assert decision.allowed is False

    def test_allows_at_or_below_the_max_rate(self):
        engine = _engine()
        engine.register(
            "p", policy_type="max_rollback_rate",
            conditions={"max_rate": 0.1},
        )

        decision = engine.evaluate(
            "dep-1", "rollback_execution", {"rollback_rate": 0.05},
        )

        assert decision.allowed is True


class TestBuiltinDeploymentFreezeWindow:

    def test_denies_inside_the_window(self):
        engine = _engine()
        engine.register(
            "p", policy_type="deployment_freeze_window",
            conditions={"start_hour": 22, "end_hour": 6},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_start",
            {"current_time": datetime(2026, 7, 23, 23, 0, 0, tzinfo=timezone.utc)},
        )

        assert decision.allowed is False

    def test_allows_outside_the_window(self):
        engine = _engine()
        engine.register(
            "p", policy_type="deployment_freeze_window",
            conditions={"start_hour": 22, "end_hour": 6},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_start",
            {"current_time": datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)},
        )

        assert decision.allowed is True

    def test_denies_inside_a_same_day_window(self):
        engine = _engine()
        engine.register(
            "p", policy_type="deployment_freeze_window",
            conditions={"start_hour": 9, "end_hour": 17},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_start",
            {"current_time": datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)},
        )

        assert decision.allowed is False


class TestBuiltinStrategyAllowList:

    def test_denies_a_strategy_not_in_the_list(self):
        engine = _engine()
        engine.register(
            "p", policy_type="strategy_allow_list",
            conditions={"allowed_strategies": ["BLUE_GREEN"]},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_creation", {"strategy": "CANARY"},
        )

        assert decision.allowed is False

    def test_allows_a_strategy_in_the_list(self):
        engine = _engine()
        engine.register(
            "p", policy_type="strategy_allow_list",
            conditions={"allowed_strategies": ["CANARY"]},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_creation", {"strategy": "CANARY"},
        )

        assert decision.allowed is True


class TestBuiltinApprovalRequired:

    def test_denies_when_not_approved(self):
        engine = _engine()
        engine.register("p", policy_type="approval_required")

        decision = engine.evaluate(
            "dep-1", "rollout_promotion", {"approved": False},
        )

        assert decision.allowed is False

    def test_allows_when_approved(self):
        engine = _engine()
        engine.register("p", policy_type="approval_required")

        decision = engine.evaluate(
            "dep-1", "rollout_promotion", {"approved": True},
        )

        assert decision.allowed is True

    def test_no_op_when_not_required(self):
        engine = _engine()
        engine.register(
            "p", policy_type="approval_required",
            conditions={"required": False},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_promotion", {"approved": False},
        )

        assert decision.allowed is True


class TestBuiltinTargetEnvironmentRestriction:

    def test_denies_an_environment_not_in_the_list(self):
        engine = _engine()
        engine.register(
            "p", policy_type="target_environment_restriction",
            conditions={"allowed_environments": ["staging"]},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_creation", {"environment": "production"},
        )

        assert decision.allowed is False

    def test_allows_an_environment_in_the_list(self):
        engine = _engine()
        engine.register(
            "p", policy_type="target_environment_restriction",
            conditions={"allowed_environments": ["production"]},
        )

        decision = engine.evaluate(
            "dep-1", "rollout_creation", {"environment": "production"},
        )

        assert decision.allowed is True


# --- Enable / disable ------------------------------------------------


class TestEnableDisable:

    def test_disable_then_enable(self):
        engine = _engine()
        engine.register("p", conditions={"blocked": True})

        engine.disable("p")
        assert engine.evaluate(
            "dep-1", "rollout_start", {"blocked": True}
        ).allowed is True

        engine.enable("p")
        assert engine.evaluate(
            "dep-1", "rollout_start", {"blocked": True}
        ).allowed is False

    def test_enable_unknown_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.enable("does-not-exist")

    def test_disable_unknown_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.disable("does-not-exist")

    def test_disable_is_idempotent(self):
        engine = _engine()
        engine.register("p")

        engine.disable("p")
        policy = engine.disable("p")

        assert policy.enabled is False


# --- Clear ---------------------------------------------------------


class TestClear:

    def test_clear_removes_all_policies(self):
        engine = _engine()
        engine.register("p")

        engine.clear()

        assert engine.list() == ()

    def test_clear_resets_denial_rate_counters(self):
        analytics = DeploymentRolloutAnalytics(clock=_clock)
        engine = _engine(analytics=analytics)
        engine.register("deny-all", conditions={})
        engine.evaluate("dep-1", "rollout_start")

        engine.clear()

        assert analytics.summary()["rollout_policy_denial_rate"] == 0.0


# --- Analytics integration -----------------------------------------


class TestAnalyticsIntegration:

    def test_denial_rate_kpi_is_registered_on_construction(self):
        analytics = DeploymentRolloutAnalytics(clock=_clock)

        _engine(analytics=analytics)

        assert "rollout_policy_denial_rate" in analytics.summary()

    def test_denial_rate_reflects_decisions(self):
        analytics = DeploymentRolloutAnalytics(clock=_clock)
        engine = _engine(analytics=analytics)
        engine.register("deny-all", conditions={})

        engine.evaluate("dep-1", "rollout_start")

        assert analytics.summary()["rollout_policy_denial_rate"] == 1.0

    def test_denial_rate_zero_with_no_decisions(self):
        analytics = DeploymentRolloutAnalytics(clock=_clock)

        _engine(analytics=analytics)

        assert analytics.summary()["rollout_policy_denial_rate"] == 0.0

    def test_no_analytics_wired_is_safe(self):
        engine = _engine(analytics=None)
        engine.register("deny-all", conditions={})

        engine.evaluate("dep-1", "rollout_start")


# --- Audit integration -----------------------------------------------


class TestAuditIntegration:

    def test_allow_records_a_success_audit_entry(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )

        audit = GovernanceAuditService(clock=_clock)
        engine = _engine(audit_service=audit)

        engine.evaluate("dep-1", "rollout_start")

        records = audit.latest(1)

        assert records[0].outcome == "success"
        assert records[0].resource == "dep-1"

    def test_deny_records_a_failure_audit_entry(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )

        audit = GovernanceAuditService(clock=_clock)
        engine = _engine(audit_service=audit)
        engine.register("deny-all", conditions={})

        engine.evaluate("dep-1", "rollout_start")

        records = audit.latest(1)

        assert records[0].outcome == "failure"

    def test_no_audit_service_wired_is_safe(self):
        engine = _engine(audit_service=None)

        engine.evaluate("dep-1", "rollout_start")


# --- Runtime integration (rollout manager / traffic router / rollback) --


class TestRuntimeIntegration:

    def test_rollout_manager_create_denied_by_policy(self):
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            DeploymentRolloutManager,
        )

        policy_engine = _engine()
        policy_engine.register("deny-all", conditions={})

        manager = DeploymentRolloutManager(
            clock=_clock, policy_engine=policy_engine,
        )

        with pytest.raises(ValueError, match="denied by policy"):
            manager.create("dep-1", "CANARY")

    def test_rollout_manager_create_allowed_by_default(self):
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            DeploymentRolloutManager,
        )

        policy_engine = _engine()
        manager = DeploymentRolloutManager(
            clock=_clock, policy_engine=policy_engine,
        )

        rollout = manager.create("dep-1", "CANARY")

        assert rollout.deployment_id == "dep-1"

    def test_traffic_router_configure_denied_by_policy(self):
        from backend.observability.deployment_governance_traffic_router import (  # noqa: E501
            DeploymentTrafficRouter,
        )

        policy_engine = _engine()
        policy_engine.register("deny-all", conditions={})

        router = DeploymentTrafficRouter(
            clock=_clock, policy_engine=policy_engine,
        )

        with pytest.raises(ValueError, match="denied by policy"):
            router.configure("dep-1", [("1.0.0", 100.0)])

    def test_rollback_execute_denied_by_policy_returns_failed_result(
        self,
    ):
        from backend.observability.deployment_governance_rollback import (
            DeploymentRollbackEngine,
        )

        policy_engine = _engine()
        policy_engine.register("deny-all", conditions={})

        rollback_engine = DeploymentRollbackEngine(
            clock=_clock, policy_engine=policy_engine,
        )
        rollback_engine.create_plan("dep-1", target_version="1.0.0")

        result = rollback_engine.execute("dep-1")

        assert result.success is False

    def test_singleton_is_wired_into_rollout_manager_traffic_router_rollback(
        self,
    ):
        from backend.observability.deployment_governance_rollback import (
            get_rollback_engine,
        )
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            get_rollout_manager,
        )
        from backend.observability.deployment_governance_traffic_router import (  # noqa: E501
            get_traffic_router,
        )

        engine = get_rollout_policy_engine()

        assert get_rollout_manager()._policy_engine is engine
        assert get_traffic_router()._policy_engine is engine
        assert get_rollback_engine()._policy_engine is engine


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_rollout_policy_engine_returns_same_instance(self):
        assert (
            get_rollout_policy_engine() is get_rollout_policy_engine()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRolloutPolicyApi:

    def test_post_registers_policy(self, client):
        response = client.post(
            "/governance/rollout/policies",
            params={"name": "p-api-1", "priority": 1},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "p-api-1"

    def test_post_duplicate_returns_409(self, client):
        client.post(
            "/governance/rollout/policies", params={"name": "p-api-2"},
        )

        response = client.post(
            "/governance/rollout/policies", params={"name": "p-api-2"},
        )

        assert response.status_code == 409

    def test_get_list(self, client):
        client.post(
            "/governance/rollout/policies", params={"name": "p-api-3"},
        )

        response = client.get("/governance/rollout/policies")

        assert response.status_code == 200
        assert any(
            p["name"] == "p-api-3" for p in response.json()
        )

    def test_patch_disables_policy(self, client):
        client.post(
            "/governance/rollout/policies", params={"name": "p-api-4"},
        )

        response = client.patch(
            "/governance/rollout/policies/p-api-4",
            params={"enabled": False},
        )

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_patch_unknown_returns_404(self, client):
        response = client.patch(
            "/governance/rollout/policies/does-not-exist",
            params={"enabled": False},
        )

        assert response.status_code == 404

    def test_delete_removes_policy(self, client):
        client.post(
            "/governance/rollout/policies", params={"name": "p-api-5"},
        )

        response = client.delete(
            "/governance/rollout/policies/p-api-5"
        )

        assert response.status_code == 200
        assert response.json() == {"removed": "p-api-5"}

    def test_delete_unknown_returns_404(self, client):
        response = client.delete(
            "/governance/rollout/policies/does-not-exist"
        )

        assert response.status_code == 404

    def test_post_evaluate_allows_by_default(self, client):
        response = client.post(
            "/governance/rollout/policies/evaluate",
            params={
                "deployment_id": "dep-api-6", "action": "rollout_start",
            },
        )

        assert response.status_code == 200
        assert response.json()["allowed"] is True

    def test_post_evaluate_with_matching_conditions_denies(self, client):
        client.post(
            "/governance/rollout/policies",
            params={
                "name": "p-api-7",
                "conditions": '{"strategy": "CANARY"}',
            },
        )

        response = client.post(
            "/governance/rollout/policies/evaluate",
            params={
                "deployment_id": "dep-api-7", "action": "rollout_start",
                "context": '{"strategy": "CANARY"}',
            },
        )

        assert response.status_code == 200
        assert response.json()["allowed"] is False

    def test_post_evaluate_invalid_context_json_returns_422(self, client):
        response = client.post(
            "/governance/rollout/policies/evaluate",
            params={
                "deployment_id": "dep-api-8", "action": "rollout_start",
                "context": "not-json",
            },
        )

        assert response.status_code == 422
