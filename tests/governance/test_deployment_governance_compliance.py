from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_compliance import (
    BUILT_IN_COMPLIANCE_CATEGORIES,
    CompliancePolicy,
    ComplianceResult,
    ComplianceSummary,
    DeploymentComplianceEngine,
    get_compliance_engine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentComplianceEngine:
    return DeploymentComplianceEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The compliance engine is a process-wide singleton; most tests
    below construct their own fresh engine instead (see _engine), and
    only the singleton and API tests touch the shared instance,
    matching test_deployment_governance_rbac.py's own fixture.
    """

    def _reset():
        get_compliance_engine().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestCompliancePolicy:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            CompliancePolicy(name="", category="Security", enabled=True)

    def test_rejects_empty_category(self):
        with pytest.raises(
            ValueError, match="category must not be empty"
        ):
            CompliancePolicy(name="p", category="", enabled=True)

    def test_to_dict(self):
        policy = CompliancePolicy(
            name="p", category="Security", enabled=True
        )

        assert policy.to_dict() == {
            "name": "p", "category": "Security", "enabled": True,
        }


class TestComplianceResult:

    def test_rejects_empty_policy(self):
        with pytest.raises(ValueError, match="policy must not be empty"):
            ComplianceResult(policy="", compliant=True, reason=None)

    def test_rejects_reason_when_compliant(self):
        with pytest.raises(
            ValueError, match="reason must not be set"
        ):
            ComplianceResult(policy="p", compliant=True, reason="why")

    def test_rejects_missing_reason_when_not_compliant(self):
        with pytest.raises(ValueError, match="reason must be set"):
            ComplianceResult(policy="p", compliant=False, reason=None)

    def test_to_dict(self):
        result = ComplianceResult(
            policy="p", compliant=False, reason="not encrypted"
        )

        assert result.to_dict() == {
            "policy": "p", "compliant": False,
            "reason": "not encrypted",
        }


class TestComplianceSummary:

    def test_rejects_mismatched_counts(self):
        with pytest.raises(
            ValueError,
            match="enabled_policies \\+ disabled_policies",
        ):
            ComplianceSummary(
                total_policies=3, enabled_policies=1,
                disabled_policies=1, categories={},
            )

    def test_to_dict(self):
        summary = ComplianceSummary(
            total_policies=2, enabled_policies=1, disabled_policies=1,
            categories={"Security": 2},
        )

        assert summary.to_dict() == {
            "total_policies": 2, "enabled_policies": 1,
            "disabled_policies": 1, "categories": {"Security": 2},
        }


class TestBuiltInCategories:

    def test_expected_categories(self):
        assert set(BUILT_IN_COMPLIANCE_CATEGORIES) == {
            "Security", "Operations", "Change Management",
            "Data Protection", "Internal Policy",
        }


# --- Policy registration ---------------------------------------------------


class TestPolicyRegistration:

    def test_register(self):
        engine = _engine()

        policy = engine.register("encryption-at-rest", "Security")

        assert policy.name == "encryption-at-rest"
        assert policy.category == "Security"
        assert policy.enabled is True
        assert policy in engine.list()

    def test_register_disabled(self):
        engine = _engine()

        policy = engine.register(
            "draft-policy", "Internal Policy", enabled=False
        )

        assert policy.enabled is False

    def test_duplicate_name_raises(self):
        engine = _engine()
        engine.register("p1", "Security")

        with pytest.raises(ValueError, match="already registered"):
            engine.register("p1", "Operations")

    def test_publishes_policy_registered(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("policy_registered", events.append)
        engine = _engine(event_bus=bus)

        engine.register("p1", "Security")

        assert len(events) == 1

    def test_remove(self):
        engine = _engine()
        engine.register("p1", "Security")

        engine.remove("p1")

        assert "p1" not in {p.name for p in engine.list()}

    def test_remove_unknown_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.remove("does-not-exist")

    def test_publishes_policy_removed(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("policy_removed", events.append)
        engine = _engine(event_bus=bus)
        engine.register("p1", "Security")

        engine.remove("p1")

        assert len(events) == 1

    def test_list_ordered_by_name(self):
        engine = _engine()
        engine.register("zeta", "Security")
        engine.register("alpha", "Security")

        names = [p.name for p in engine.list()]

        assert names == ["alpha", "zeta"]


# --- Policy evaluation -------------------------------------------------


class TestPolicyEvaluation:

    def test_evaluate_with_no_evaluator_is_compliant(self):
        engine = _engine()
        engine.register("p1", "Security")

        results = engine.evaluate("d1")

        assert len(results) == 1
        assert results[0].compliant is True
        assert results[0].reason is None

    def test_evaluate_runs_every_enabled_policy(self):
        engine = _engine()
        engine.register("p1", "Security")
        engine.register("p2", "Operations")

        results = engine.evaluate("d1")

        assert {r.policy for r in results} == {"p1", "p2"}

    def test_evaluate_with_custom_evaluator(self):
        engine = _engine()

        def _requires_flag(policy, context):
            if context.get("flagged"):
                return True, None

            return False, "flag not set"

        engine.register("p1", "Security", evaluator=_requires_flag)

        results = engine.evaluate("d1", {"flagged": True})

        assert results[0].compliant is True

    def test_rejects_empty_deployment_id(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            engine.evaluate("")

    def test_publishes_compliance_passed(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("compliance_passed", events.append)
        engine = _engine(event_bus=bus)
        engine.register("p1", "Security")

        engine.evaluate("d1")

        assert len(events) == 1

    def test_evaluate_all(self):
        engine = _engine()
        engine.register("p1", "Security")

        results = engine.evaluate_all(
            {"d2": {}, "d1": {}}
        )

        assert list(results.keys()) == ["d1", "d2"]
        assert results["d1"][0].policy == "p1"

    def test_evaluate_all_empty(self):
        engine = _engine()

        assert engine.evaluate_all() == {}


# --- Failed compliance ------------------------------------------------


class TestFailedCompliance:

    def test_evaluate_reports_failure_with_reason(self):
        engine = _engine()

        def _always_fails(policy, context):
            return False, "not compliant with policy"

        engine.register("p1", "Security", evaluator=_always_fails)

        results = engine.evaluate("d1")

        assert results[0].compliant is False
        assert results[0].reason == "not compliant with policy"

    def test_publishes_compliance_failed(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("compliance_failed", events.append)
        engine = _engine(event_bus=bus)

        def _always_fails(policy, context):
            return False, "nope"

        engine.register("p1", "Security", evaluator=_always_fails)

        engine.evaluate("d1")

        assert len(events) == 1

    def test_mixed_pass_and_fail(self):
        engine = _engine()

        def _fails(policy, context):
            return False, "nope"

        engine.register("passes", "Security")
        engine.register("fails", "Security", evaluator=_fails)

        results = engine.evaluate("d1")

        by_name = {r.policy: r for r in results}

        assert by_name["passes"].compliant is True
        assert by_name["fails"].compliant is False


# --- Disabled policy -----------------------------------------------------


class TestDisabledPolicy:

    def test_disabled_policy_is_skipped(self):
        engine = _engine()
        engine.register("p1", "Security", enabled=False)

        results = engine.evaluate("d1")

        assert results == ()

    def test_disabled_policy_alongside_enabled(self):
        engine = _engine()
        engine.register("enabled-one", "Security", enabled=True)
        engine.register("disabled-one", "Security", enabled=False)

        results = engine.evaluate("d1")

        assert [r.policy for r in results] == ["enabled-one"]

    def test_disabled_policy_still_appears_in_list(self):
        engine = _engine()
        engine.register("p1", "Security", enabled=False)

        assert len(engine.list()) == 1


# --- Summary generation --------------------------------------------------


class TestSummaryGeneration:

    def test_summary_of_empty_registry(self):
        engine = _engine()

        summary = engine.summary()

        assert summary.total_policies == 0
        assert summary.enabled_policies == 0
        assert summary.disabled_policies == 0
        assert dict(summary.categories) == {}

    def test_summary_counts(self):
        engine = _engine()
        engine.register("p1", "Security", enabled=True)
        engine.register("p2", "Security", enabled=False)
        engine.register("p3", "Operations", enabled=True)

        summary = engine.summary()

        assert summary.total_policies == 3
        assert summary.enabled_policies == 2
        assert summary.disabled_policies == 1
        assert dict(summary.categories) == {
            "Security": 2, "Operations": 1,
        }


# --- Audit integration ---------------------------------------------------


class TestAuditIntegration:

    def test_evaluate_records_audit_entries(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        engine = _engine(audit_service=audit_service)
        engine.register("p1", "Security")

        engine.evaluate("d1")

        records = audit_service.latest()

        assert any(r.action == "compliance_passed" for r in records)


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_every_policy(self):
        engine = _engine()
        engine.register("p1", "Security")

        engine.clear()

        assert engine.list() == ()


# --- Approval engine bridge (this commit's Update file) --------------------


class TestApprovalComplianceScopeBridge:

    def test_compliance_scope_returns_deployment_and_operation(self):
        from backend.observability.deployment_governance_approval import (
            DeploymentApprovalEngine,
        )

        approval = DeploymentApprovalEngine(clock=_clock)
        request = approval.create_request("d1", "deploy", "alice")

        deployment_id, operation = approval.compliance_scope(
            request.request_id
        )

        assert deployment_id == "d1"
        assert operation == "deploy"

    def test_compliance_scope_unknown_request_raises(self):
        from backend.observability.deployment_governance_approval import (
            DeploymentApprovalEngine,
        )

        approval = DeploymentApprovalEngine(clock=_clock)

        with pytest.raises(KeyError):
            approval.compliance_scope("does-not-exist")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_compliance_engine_returns_same_instance(self):
        assert get_compliance_engine() is get_compliance_engine()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceComplianceApi:

    def test_post_registers_policy(self, client):
        response = client.post(
            "/governance/security/compliance",
            params={"name": "api-p-1", "category": "Security"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "api-p-1"

    def test_post_duplicate_returns_409(self, client):
        client.post(
            "/governance/security/compliance",
            params={"name": "api-p-2", "category": "Security"},
        )

        response = client.post(
            "/governance/security/compliance",
            params={"name": "api-p-2", "category": "Security"},
        )

        assert response.status_code == 409

    def test_get_list(self, client):
        client.post(
            "/governance/security/compliance",
            params={"name": "api-p-3", "category": "Security"},
        )

        response = client.get("/governance/security/compliance")

        assert response.status_code == 200
        assert any(
            p["name"] == "api-p-3" for p in response.json()
        )

    def test_post_evaluate(self, client):
        client.post(
            "/governance/security/compliance",
            params={"name": "api-p-4", "category": "Security"},
        )

        response = client.post(
            "/governance/security/compliance/evaluate",
            params={"deployment_id": "api-d-1"},
        )

        assert response.status_code == 200
        assert any(
            r["policy"] == "api-p-4" for r in response.json()
        )

    def test_get_summary(self, client):
        client.post(
            "/governance/security/compliance",
            params={"name": "api-p-5", "category": "Operations"},
        )

        response = client.get(
            "/governance/security/compliance/summary"
        )

        assert response.status_code == 200
        assert response.json()["total_policies"] >= 1
