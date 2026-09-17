from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_policy import (
    GovernancePolicy,
    GovernancePolicyEngine,
    GovernancePolicyViolation,
    PolicyDecision,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The lifecycle manager, event bus, event history, event router,
    audit service, and policy engine are all process-wide singletons
    wired together, so tests touching any of them (directly or via
    the API) must not leak state into other tests.
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

    def _reset():
        get_lifecycle_manager().shutdown()
        get_event_history().purge()
        get_audit_service().purge()
        get_policy_engine().clear()
        get_event_bus().clear()

    _reset()
    yield
    _reset()


# --- Model -------------------------------------------------------------


class TestGovernancePolicy:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            GovernancePolicy(name="", operation="x", priority=0)

    def test_rejects_empty_operation(self):
        with pytest.raises(ValueError, match="operation must not be empty"):
            GovernancePolicy(name="a", operation="", priority=0)

    def test_defaults(self):
        policy = GovernancePolicy(name="a", operation="x", priority=0)

        assert policy.enabled is True
        assert dict(policy.conditions) == {}

    def test_conditions_are_immutable(self):
        policy = GovernancePolicy(
            name="a", operation="x", priority=0, conditions={"k": "v"}
        )

        with pytest.raises(TypeError):
            policy.conditions["k"] = "other"

    def test_to_dict(self):
        policy = GovernancePolicy(
            name="a",
            operation="x",
            priority=3,
            enabled=False,
            conditions={"k": "v"},
        )

        assert policy.to_dict() == {
            "name": "a",
            "operation": "x",
            "priority": 3,
            "enabled": False,
            "conditions": {"k": "v"},
            "rule": None,
        }


class TestPolicyDecision:

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            PolicyDecision(
                allowed=True,
                policy=None,
                reason=None,
                evaluated_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_rejects_allowed_with_policy(self):
        with pytest.raises(
            ValueError, match="policy and reason must not be set"
        ):
            PolicyDecision(
                allowed=True, policy="a", reason=None, evaluated_at=BASE_TIME
            )

    def test_rejects_denied_without_policy(self):
        with pytest.raises(
            ValueError, match="policy and reason must be set"
        ):
            PolicyDecision(
                allowed=False, policy=None, reason=None, evaluated_at=BASE_TIME
            )

    def test_to_dict(self):
        decision = PolicyDecision(
            allowed=False, policy="a", reason="nope", evaluated_at=BASE_TIME
        )

        assert decision.to_dict() == {
            "allowed": False,
            "policy": "a",
            "reason": "nope",
            "evaluated_at": BASE_TIME.isoformat(),
        }


def test_policy_violation_message_includes_policy_and_reason():
    decision = PolicyDecision(
        allowed=False, policy="a", reason="nope", evaluated_at=BASE_TIME
    )

    error = GovernancePolicyViolation(decision)

    assert "a" in str(error)
    assert "nope" in str(error)


# --- Policy registration -------------------------------------------------


class TestPolicyRegistration:

    def test_register_returns_policy(self):
        engine = GovernancePolicyEngine()

        policy = engine.register("a", operation="lifecycle_start")

        assert policy.name == "a"
        assert policy.operation == "lifecycle_start"

    def test_registered_policy_appears_in_list(self):
        engine = GovernancePolicyEngine()
        engine.register("a", operation="x")

        assert [p.name for p in engine.list()] == ["a"]

    def test_remove_unknown_policy_raises(self):
        engine = GovernancePolicyEngine()

        with pytest.raises(KeyError):
            engine.remove("ghost")

    def test_remove_removes_policy(self):
        engine = GovernancePolicyEngine()
        engine.register("a", operation="x")
        engine.remove("a")

        assert engine.list() == ()


# --- Duplicate policy rejection ------------------------------------------


def test_duplicate_policy_name_rejected():
    engine = GovernancePolicyEngine()
    engine.register("a", operation="x")

    with pytest.raises(ValueError, match="already registered"):
        engine.register("a", operation="y")


# --- Allow decision --------------------------------------------------


class TestAllowDecision:

    def test_no_policies_allows(self):
        engine = GovernancePolicyEngine(clock=_clock)

        decision = engine.evaluate("lifecycle_start")

        assert decision.allowed is True
        assert decision.policy is None
        assert decision.reason is None
        assert decision.evaluated_at == BASE_TIME

    def test_policy_for_different_operation_does_not_block(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register("a", operation="lifecycle_stop")

        decision = engine.evaluate("lifecycle_start")

        assert decision.allowed is True

    def test_conditions_that_do_not_match_context_allow(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register(
            "a", operation="lifecycle_start", conditions={"actor": "guest"}
        )

        decision = engine.evaluate(
            "lifecycle_start", {"actor": "admin"}
        )

        assert decision.allowed is True


# --- Deny decision --------------------------------------------------------


class TestDenyDecision:

    def test_unconditional_policy_denies(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register("a", operation="lifecycle_start")

        decision = engine.evaluate("lifecycle_start")

        assert decision.allowed is False
        assert decision.policy == "a"
        assert decision.reason is not None

    def test_matching_conditions_deny(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register(
            "a", operation="lifecycle_start", conditions={"actor": "guest"}
        )

        decision = engine.evaluate(
            "lifecycle_start", {"actor": "guest"}
        )

        assert decision.allowed is False
        assert decision.policy == "a"

    def test_wildcard_operation_denies_everything(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register("a", operation="*")

        assert engine.evaluate("lifecycle_start").allowed is False
        assert engine.evaluate("route_create").allowed is False

    def test_multiple_conditions_require_all_to_match(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register(
            "a",
            operation="lifecycle_start",
            conditions={"actor": "guest", "region": "eu"},
        )

        assert (
            engine.evaluate(
                "lifecycle_start", {"actor": "guest", "region": "us"}
            ).allowed
            is True
        )
        assert (
            engine.evaluate(
                "lifecycle_start", {"actor": "guest", "region": "eu"}
            ).allowed
            is False
        )


# --- Priority evaluation -------------------------------------------------


class TestPriorityEvaluation:

    def test_lower_priority_evaluated_first(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register(
            "deny_all", operation="lifecycle_start", priority=10
        )
        engine.register(
            "deny_guest",
            operation="lifecycle_start",
            priority=0,
            conditions={"actor": "guest"},
        )

        decision = engine.evaluate(
            "lifecycle_start", {"actor": "guest"}
        )

        # Both match; the lower-priority ("deny_guest") one wins.
        assert decision.policy == "deny_guest"

    def test_ties_broken_by_name(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register("z", operation="lifecycle_start", priority=0)
        engine.register("a", operation="lifecycle_start", priority=0)

        decision = engine.evaluate("lifecycle_start")

        assert decision.policy == "a"

    def test_first_denying_policy_short_circuits(self):
        evaluated = []

        class _TrackingEngine(GovernancePolicyEngine):
            def _policy_matches(self, policy, operation, context):
                evaluated.append(policy.name)
                return super()._policy_matches(policy, operation, context)

        engine = _TrackingEngine(clock=_clock)
        engine.register("a", operation="x", priority=0)
        engine.register("b", operation="x", priority=1)

        engine.evaluate("x")

        # Only the first (denying) policy should have been checked.
        assert len(evaluated) == 1

    def test_list_ordered_by_priority_then_name(self):
        engine = GovernancePolicyEngine()
        engine.register("z", operation="x", priority=1)
        engine.register("a", operation="x", priority=1)
        engine.register("m", operation="x", priority=0)

        assert [p.name for p in engine.list()] == ["m", "a", "z"]


# --- Disabled policies -------------------------------------------------


class TestDisabledPolicies:

    def test_disabled_policy_is_ignored(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register("a", operation="lifecycle_start")
        engine.disable("a")

        decision = engine.evaluate("lifecycle_start")

        assert decision.allowed is True

    def test_enable_restores_enforcement(self):
        engine = GovernancePolicyEngine(clock=_clock)
        engine.register("a", operation="lifecycle_start")
        engine.disable("a")
        engine.enable("a")

        decision = engine.evaluate("lifecycle_start")

        assert decision.allowed is False

    def test_enable_unknown_policy_raises(self):
        engine = GovernancePolicyEngine()

        with pytest.raises(KeyError):
            engine.enable("ghost")

    def test_disable_unknown_policy_raises(self):
        engine = GovernancePolicyEngine()

        with pytest.raises(KeyError):
            engine.disable("ghost")

    def test_disable_is_idempotent(self):
        engine = GovernancePolicyEngine()
        engine.register("a", operation="x")

        engine.disable("a")
        engine.disable("a")

        assert engine.list()[0].enabled is False


def test_clear_removes_every_policy():
    engine = GovernancePolicyEngine()
    engine.register("a", operation="x")
    engine.register("b", operation="y")

    engine.clear()

    assert engine.list() == ()


# --- Audit integration ------------------------------------------------


class TestAuditIntegration:

    def test_audit_purge_records_policy_evaluation_when_allowed(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )

        policy_engine = GovernancePolicyEngine(clock=_clock)
        audit_service = GovernanceAuditService(
            clock=_clock, policy_engine=policy_engine
        )
        audit_service.record(
            action="a", actor="x", resource="r", outcome="success"
        )

        audit_service.purge()

        records = audit_service.query(AuditQuery(action="policy_evaluation"))
        assert len(records) == 1
        assert records[0].outcome == "success"

    def test_audit_purge_denied_by_policy_raises_and_records(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )

        policy_engine = GovernancePolicyEngine(clock=_clock)
        policy_engine.register("deny_purge", operation="audit_purge")

        audit_service = GovernanceAuditService(
            clock=_clock, policy_engine=policy_engine
        )
        audit_service.record(
            action="a", actor="x", resource="r", outcome="success"
        )

        with pytest.raises(GovernancePolicyViolation):
            audit_service.purge()

        # Nothing was removed.
        assert audit_service.size() == 2  # original + the recorded decision

        records = audit_service.query(AuditQuery(action="policy_evaluation"))
        assert len(records) == 1
        assert records[0].outcome == "denied"

    def test_lifecycle_start_denied_by_policy_raises_and_records(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        policy_engine = GovernancePolicyEngine(clock=_clock)
        policy_engine.register("deny_start", operation="lifecycle_start")
        audit_service = GovernanceAuditService(clock=_clock)

        manager = GovernanceLifecycleManager(
            audit_service=audit_service, policy_engine=policy_engine
        )
        manager.register("a", start=lambda: None, stop=lambda: None)

        with pytest.raises(GovernancePolicyViolation):
            manager.startup()

        # The component was never actually started.
        assert manager.status()[0].started is False

        records = audit_service.query(AuditQuery(action="policy_evaluation"))
        assert len(records) == 1
        assert records[0].outcome == "denied"

        # And lifecycle_start itself was never recorded, since it
        # never actually happened.
        assert audit_service.query(AuditQuery(action="lifecycle_start")) == ()

    def test_route_create_denied_by_policy_raises_and_records(self):
        from backend.observability.deployment_governance_audit import (
            AuditQuery,
            GovernanceAuditService,
        )
        from backend.observability.deployment_governance_event_router import (
            GovernanceEventRouter,
        )

        policy_engine = GovernancePolicyEngine(clock=_clock)
        policy_engine.register("deny_route_create", operation="route_create")
        audit_service = GovernanceAuditService(clock=_clock)

        router = GovernanceEventRouter(
            audit_service=audit_service, policy_engine=policy_engine
        )

        with pytest.raises(GovernancePolicyViolation):
            router.register_route("a")

        assert router.routes() == ()

        records = audit_service.query(AuditQuery(action="policy_evaluation"))
        assert len(records) == 1
        assert records[0].outcome == "denied"


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernancePoliciesApi:

    def test_get_policies_empty_by_default(self, client) -> None:
        response = client.get("/governance/policies")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_policy_registers_and_returns_it(self, client) -> None:
        response = client.post(
            "/governance/policies",
            params={
                "name": "api_test_policy",
                "operation": "lifecycle_start",
                "priority": 5,
                "conditions": '{"actor": "guest"}',
            },
        )

        try:
            assert response.status_code == 200

            payload = response.json()

            assert payload["name"] == "api_test_policy"
            assert payload["conditions"] == {"actor": "guest"}

        finally:
            client.delete("/governance/policies/api_test_policy")

    def test_post_duplicate_policy_returns_409(self, client) -> None:
        client.post(
            "/governance/policies",
            params={"name": "api_dup_policy", "operation": "x"},
        )

        try:
            response = client.post(
                "/governance/policies",
                params={"name": "api_dup_policy", "operation": "x"},
            )

            assert response.status_code == 409

        finally:
            client.delete("/governance/policies/api_dup_policy")

    def test_post_policy_rejects_invalid_json_conditions(self, client) -> None:
        response = client.post(
            "/governance/policies",
            params={
                "name": "api_bad_policy",
                "operation": "x",
                "conditions": "not-json",
            },
        )

        assert response.status_code == 422

    def test_patch_policy_disables_it(self, client) -> None:
        client.post(
            "/governance/policies",
            params={"name": "api_patch_policy", "operation": "x"},
        )

        try:
            response = client.patch(
                "/governance/policies/api_patch_policy",
                params={"enabled": False},
            )

            assert response.status_code == 200
            assert response.json()["enabled"] is False

        finally:
            client.delete("/governance/policies/api_patch_policy")

    def test_patch_unknown_policy_returns_404(self, client) -> None:
        response = client.patch(
            "/governance/policies/does_not_exist",
            params={"enabled": True},
        )

        assert response.status_code == 404

    def test_delete_policy_removes_it(self, client) -> None:
        client.post(
            "/governance/policies",
            params={"name": "api_delete_policy", "operation": "x"},
        )

        response = client.delete("/governance/policies/api_delete_policy")

        assert response.status_code == 200
        assert response.json() == {"removed": "api_delete_policy"}

    def test_delete_unknown_policy_returns_404(self, client) -> None:
        response = client.delete("/governance/policies/does_not_exist")

        assert response.status_code == 404

    def test_evaluate_endpoint_returns_allow_by_default(self, client) -> None:
        response = client.post(
            "/governance/policies/evaluate",
            params={"operation": "lifecycle_start"},
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["allowed"] is True

    def test_evaluate_endpoint_returns_deny_for_matching_policy(
        self, client
    ) -> None:
        client.post(
            "/governance/policies",
            params={
                "name": "api_eval_policy",
                "operation": "lifecycle_start",
                "conditions": '{"actor": "guest"}',
            },
        )

        try:
            response = client.post(
                "/governance/policies/evaluate",
                params={
                    "operation": "lifecycle_start",
                    "context": '{"actor": "guest"}',
                },
            )

            payload = response.json()

            assert payload["allowed"] is False
            assert payload["policy"] == "api_eval_policy"

        finally:
            client.delete("/governance/policies/api_eval_policy")
