from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_rules import (
    GovernanceRule,
    GovernanceRuleEngine,
    RuleEvaluationResult,
    conditions_match,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The lifecycle manager, event bus, event history, audit service,
    and policy engine are all process-wide singletons, so tests that
    touch them (directly or via the API) must not leak state into
    other tests. The rule engine singleton keeps its built-in rules
    (there is no equivalent of the others' "reset to defaults" here),
    so it is deliberately not cleared — API tests that add rules
    clean them up individually instead.
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


class TestGovernanceRule:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            GovernanceRule(name="", operation="x", priority=0)

    def test_rejects_empty_operation(self):
        with pytest.raises(ValueError, match="operation must not be empty"):
            GovernanceRule(name="a", operation="", priority=0)

    def test_defaults_to_enabled(self):
        rule = GovernanceRule(name="a", operation="x", priority=0)

        assert rule.enabled is True

    def test_to_dict(self):
        rule = GovernanceRule(
            name="a", operation="x", priority=3, enabled=False
        )

        assert rule.to_dict() == {
            "name": "a",
            "operation": "x",
            "priority": 3,
            "enabled": False,
        }


class TestRuleEvaluationResult:

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            RuleEvaluationResult(
                rule="a",
                passed=True,
                reason=None,
                evaluated_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_rejects_passed_with_reason(self):
        with pytest.raises(
            ValueError, match="reason must not be set when passed is True"
        ):
            RuleEvaluationResult(
                rule="a", passed=True, reason="boom", evaluated_at=BASE_TIME
            )

    def test_rejects_failed_without_reason(self):
        with pytest.raises(
            ValueError, match="reason must be set when passed is False"
        ):
            RuleEvaluationResult(
                rule="a", passed=False, reason=None, evaluated_at=BASE_TIME
            )

    def test_to_dict(self):
        result = RuleEvaluationResult(
            rule="a", passed=False, reason="nope", evaluated_at=BASE_TIME
        )

        assert result.to_dict() == {
            "rule": "a",
            "passed": False,
            "reason": "nope",
            "evaluated_at": BASE_TIME.isoformat(),
        }


def test_conditions_match_requires_all_keys():
    assert conditions_match({"a": 1, "b": 2}, {"a": 1, "b": 2}) is True
    assert conditions_match({"a": 1, "b": 2}, {"a": 1, "b": 3}) is False


def test_conditions_match_empty_conditions_always_match():
    assert conditions_match({}, {"anything": "x"}) is True


# --- Rule registration -------------------------------------------------


class TestRuleRegistration:

    def test_register_returns_definition(self):
        engine = GovernanceRuleEngine()

        rule = engine.register("a", operation="x", check=lambda ctx: True)

        assert rule.name == "a"
        assert rule.operation == "x"

    def test_registered_rule_appears_in_list(self):
        engine = GovernanceRuleEngine()
        engine.register("a", check=lambda ctx: True)

        assert [r.name for r in engine.list()] == ["a"]

    def test_register_defaults_to_wildcard_operation(self):
        engine = GovernanceRuleEngine()

        rule = engine.register("a", check=lambda ctx: True)

        assert rule.operation == "*"

    def test_remove_unknown_rule_raises(self):
        engine = GovernanceRuleEngine()

        with pytest.raises(KeyError):
            engine.remove("ghost")

    def test_remove_removes_rule(self):
        engine = GovernanceRuleEngine()
        engine.register("a", check=lambda ctx: True)
        engine.remove("a")

        assert engine.list() == ()


# --- Duplicate rejection -------------------------------------------------


def test_duplicate_rule_name_rejected():
    engine = GovernanceRuleEngine()
    engine.register("a", check=lambda ctx: True)

    with pytest.raises(ValueError, match="already registered"):
        engine.register("a", check=lambda ctx: True)


# --- Enable / disable ----------------------------------------------------


class TestEnableDisable:

    def test_disabled_rule_evaluate_reports_disabled(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=lambda ctx: True)
        engine.disable("a")

        result = engine.evaluate("a")

        assert result.passed is False
        assert result.reason == "rule is disabled"

    def test_enable_restores_evaluation(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=lambda ctx: True)
        engine.disable("a")
        engine.enable("a")

        result = engine.evaluate("a")

        assert result.passed is True

    def test_disabled_rules_skipped_by_evaluate_all(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", operation="x", check=lambda ctx: True)
        engine.register("b", operation="x", check=lambda ctx: True)
        engine.disable("b")

        results = engine.evaluate_all("x")

        assert [r.rule for r in results] == ["a"]

    def test_enable_unknown_rule_raises(self):
        engine = GovernanceRuleEngine()

        with pytest.raises(KeyError):
            engine.enable("ghost")

    def test_disable_unknown_rule_raises(self):
        engine = GovernanceRuleEngine()

        with pytest.raises(KeyError):
            engine.disable("ghost")

    def test_disable_is_idempotent(self):
        engine = GovernanceRuleEngine()
        engine.register("a", check=lambda ctx: True)

        engine.disable("a")
        engine.disable("a")

        assert engine.list()[0].enabled is False


def test_clear_removes_every_rule():
    engine = GovernanceRuleEngine()
    engine.register("a", check=lambda ctx: True)
    engine.register("b", check=lambda ctx: True)

    engine.clear()

    assert engine.list() == ()


# --- Priority ordering -----------------------------------------------


class TestPriorityOrdering:

    def test_list_ordered_by_priority_then_name(self):
        engine = GovernanceRuleEngine()
        engine.register("z", priority=1, check=lambda ctx: True)
        engine.register("a", priority=1, check=lambda ctx: True)
        engine.register("m", priority=0, check=lambda ctx: True)

        assert [r.name for r in engine.list()] == ["m", "a", "z"]

    def test_evaluate_all_respects_priority_order(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register(
            "z", operation="x", priority=1, check=lambda ctx: True
        )
        engine.register(
            "a", operation="x", priority=0, check=lambda ctx: True
        )

        results = engine.evaluate_all("x")

        assert [r.rule for r in results] == ["a", "z"]

    def test_ordering_independent_of_registration_order(self):
        engine_a = GovernanceRuleEngine()
        engine_a.register("b", priority=1, check=lambda ctx: True)
        engine_a.register("a", priority=1, check=lambda ctx: True)

        engine_b = GovernanceRuleEngine()
        engine_b.register("a", priority=1, check=lambda ctx: True)
        engine_b.register("b", priority=1, check=lambda ctx: True)

        assert [r.name for r in engine_a.list()] == [
            r.name for r in engine_b.list()
        ]


# --- Successful evaluation -------------------------------------------


class TestSuccessfulEvaluation:

    def test_evaluate_bool_true(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=lambda ctx: True)

        result = engine.evaluate("a")

        assert result.passed is True
        assert result.reason is None
        assert result.evaluated_at == BASE_TIME

    def test_evaluate_tuple_true(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=lambda ctx: (True, None))

        result = engine.evaluate("a")

        assert result.passed is True

    def test_evaluate_passes_context_to_check(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=lambda ctx: ctx.get("x") == 1)

        assert engine.evaluate("a", {"x": 1}).passed is True
        assert engine.evaluate("a", {"x": 2}).passed is False

    def test_evaluate_unknown_rule_raises(self):
        engine = GovernanceRuleEngine()

        with pytest.raises(LookupError):
            engine.evaluate("ghost")


# --- Failed evaluation -----------------------------------------------


class TestFailedEvaluation:

    def test_evaluate_bool_false_gets_default_reason(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=lambda ctx: False)

        result = engine.evaluate("a")

        assert result.passed is False
        assert result.reason == "rule 'a' did not pass"

    def test_evaluate_tuple_false_reason(self):
        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=lambda ctx: (False, "custom reason"))

        result = engine.evaluate("a")

        assert result.passed is False
        assert result.reason == "custom reason"


# --- Exception handling ------------------------------------------------


class TestExceptionHandling:

    def test_raising_check_is_converted_to_failed_evaluation(self):
        def _boom(ctx):
            raise RuntimeError("boom")

        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", check=_boom)

        result = engine.evaluate("a")

        assert result.passed is False
        assert result.reason == "boom"

    def test_one_raising_rule_does_not_stop_evaluate_all(self):
        def _boom(ctx):
            raise RuntimeError("boom")

        engine = GovernanceRuleEngine(clock=_clock)
        engine.register("a", operation="x", check=_boom)
        engine.register("b", operation="x", check=lambda ctx: True)

        results = engine.evaluate_all("x")

        assert {r.rule: r.passed for r in results} == {
            "a": False,
            "b": True,
        }


# --- Built-in rules ------------------------------------------------------


class TestBuiltInRules:

    def test_default_engine_has_all_seven_built_ins(self):
        from backend.observability.deployment_governance_rules import (
            build_default_governance_rule_engine,
        )

        names = {
            r.name
            for r in build_default_governance_rule_engine().list()
        }

        assert names == {
            "runtime_initialized",
            "component_healthy",
            "provider_registered",
            "dependency_satisfied",
            "lifecycle_idle",
            "audit_chain_valid",
            "event_history_available",
        }

    def test_built_in_rules_evaluate_without_raising(self):
        from backend.observability.deployment_governance_rules import (
            get_rule_engine,
        )

        engine = get_rule_engine()

        for rule in engine.list():
            result = engine.evaluate(rule.name)
            assert isinstance(result, RuleEvaluationResult)

    def test_lifecycle_idle_true_when_nothing_started(self):
        from backend.observability.deployment_governance_rules import (
            get_rule_engine,
        )

        result = get_rule_engine().evaluate("lifecycle_idle")

        assert result.passed is True

    def test_lifecycle_idle_false_once_started(self):
        from backend.observability.deployment_governance_lifecycle import (
            get_lifecycle_manager,
        )
        from backend.observability.deployment_governance_rules import (
            get_rule_engine,
        )

        get_lifecycle_manager().startup()

        result = get_rule_engine().evaluate("lifecycle_idle")

        assert result.passed is False

    def test_audit_chain_valid_true_for_fresh_audit_trail(self):
        from backend.observability.deployment_governance_rules import (
            get_rule_engine,
        )

        result = get_rule_engine().evaluate("audit_chain_valid")

        assert result.passed is True


# --- Policy integration ------------------------------------------------


class TestPolicyIntegration:

    def test_policy_denies_when_referenced_rule_fails(self):
        from backend.observability.deployment_governance_policy import (
            GovernancePolicyEngine,
        )

        rule_engine = GovernanceRuleEngine(clock=_clock)
        rule_engine.register("always_fail", check=lambda ctx: False)

        policy_engine = GovernancePolicyEngine(
            clock=_clock, rule_engine=rule_engine
        )
        policy_engine.register(
            "deny_via_rule", operation="lifecycle_start", rule="always_fail"
        )

        decision = policy_engine.evaluate("lifecycle_start")

        assert decision.allowed is False
        assert decision.policy == "deny_via_rule"
        assert "always_fail" in decision.reason

    def test_policy_allows_when_referenced_rule_passes(self):
        from backend.observability.deployment_governance_policy import (
            GovernancePolicyEngine,
        )

        rule_engine = GovernanceRuleEngine(clock=_clock)
        rule_engine.register("always_pass", check=lambda ctx: True)

        policy_engine = GovernancePolicyEngine(
            clock=_clock, rule_engine=rule_engine
        )
        policy_engine.register(
            "allow_via_rule", operation="lifecycle_start", rule="always_pass"
        )

        decision = policy_engine.evaluate("lifecycle_start")

        assert decision.allowed is True

    def test_policy_falls_back_to_conditions_without_rule_engine(self):
        from backend.observability.deployment_governance_policy import (
            GovernancePolicyEngine,
        )

        policy_engine = GovernancePolicyEngine(clock=_clock)
        policy_engine.register(
            "deny_guest",
            operation="lifecycle_start",
            conditions={"actor": "guest"},
        )

        decision = policy_engine.evaluate(
            "lifecycle_start", {"actor": "guest"}
        )

        assert decision.allowed is False
        assert decision.policy == "deny_guest"

    def test_policy_with_rule_ignores_conditions(self):
        from backend.observability.deployment_governance_policy import (
            GovernancePolicyEngine,
        )

        rule_engine = GovernanceRuleEngine(clock=_clock)
        rule_engine.register("always_pass", check=lambda ctx: True)

        policy_engine = GovernancePolicyEngine(
            clock=_clock, rule_engine=rule_engine
        )
        # conditions would deny (empty dict = unconditional match) but
        # the attached rule always passes, so the rule wins.
        policy_engine.register(
            "rule_wins",
            operation="lifecycle_start",
            rule="always_pass",
            conditions={},
        )

        decision = policy_engine.evaluate("lifecycle_start")

        assert decision.allowed is True

    def test_singleton_policy_engine_is_wired_to_singleton_rule_engine(
        self,
    ):
        from backend.observability.deployment_governance_policy import (
            get_policy_engine,
        )
        from backend.observability.deployment_governance_rules import (
            get_rule_engine,
        )

        assert get_policy_engine()._rule_engine is get_rule_engine()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRulesApi:

    def test_get_rules_includes_built_ins(self, client) -> None:
        response = client.get("/governance/rules")

        assert response.status_code == 200

        names = {rule["name"] for rule in response.json()}

        assert "runtime_initialized" in names

    def test_post_rule_registers_and_returns_it(self, client) -> None:
        response = client.post(
            "/governance/rules",
            params={
                "name": "api_test_rule",
                "operation": "lifecycle_start",
                "priority": 5,
                "conditions": '{"actor": "guest"}',
            },
        )

        try:
            assert response.status_code == 200

            payload = response.json()

            assert payload["name"] == "api_test_rule"
            assert payload["priority"] == 5

        finally:
            client.delete("/governance/rules/api_test_rule")

    def test_post_duplicate_rule_returns_409(self, client) -> None:
        client.post(
            "/governance/rules", params={"name": "api_dup_rule"}
        )

        try:
            response = client.post(
                "/governance/rules", params={"name": "api_dup_rule"}
            )

            assert response.status_code == 409

        finally:
            client.delete("/governance/rules/api_dup_rule")

    def test_patch_rule_disables_it(self, client) -> None:
        client.post(
            "/governance/rules", params={"name": "api_patch_rule"}
        )

        try:
            response = client.patch(
                "/governance/rules/api_patch_rule",
                params={"enabled": False},
            )

            assert response.status_code == 200
            assert response.json()["enabled"] is False

        finally:
            client.delete("/governance/rules/api_patch_rule")

    def test_patch_unknown_rule_returns_404(self, client) -> None:
        response = client.patch(
            "/governance/rules/does_not_exist", params={"enabled": True}
        )

        assert response.status_code == 404

    def test_delete_rule_removes_it(self, client) -> None:
        client.post(
            "/governance/rules", params={"name": "api_delete_rule"}
        )

        response = client.delete("/governance/rules/api_delete_rule")

        assert response.status_code == 200
        assert response.json() == {"removed": "api_delete_rule"}

    def test_delete_unknown_rule_returns_404(self, client) -> None:
        response = client.delete("/governance/rules/does_not_exist")

        assert response.status_code == 404

    def test_evaluate_endpoint_returns_result_for_built_in_rule(
        self, client
    ) -> None:
        response = client.post(
            "/governance/rules/evaluate",
            params={"name": "lifecycle_idle"},
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["rule"] == "lifecycle_idle"
        assert payload["passed"] is True

    def test_evaluate_endpoint_records_audit_entry(self, client) -> None:
        client.post(
            "/governance/rules/evaluate",
            params={"name": "lifecycle_idle"},
        )

        response = client.get(
            "/governance/audit?action=rule_evaluation"
        )

        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_evaluate_endpoint_unknown_rule_returns_404(
        self, client
    ) -> None:
        response = client.post(
            "/governance/rules/evaluate",
            params={"name": "does_not_exist"},
        )

        assert response.status_code == 404
