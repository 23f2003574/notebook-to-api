from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_risk import (
    DEFAULT_RISK_FACTORS,
    RISK_LEVELS,
    DeploymentRiskEngine,
    RiskAssessment,
    RiskRule,
    RiskSummary,
    get_risk_engine,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentRiskEngine:
    return DeploymentRiskEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The risk engine is a process-wide singleton; most tests below
    construct their own fresh engine instead (see _engine), and only
    the singleton and API tests touch the shared instance, matching
    test_deployment_governance_compliance.py's own fixture.
    """

    def _reset():
        get_risk_engine().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestRiskRule:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            RiskRule(name="", weight=10.0, enabled=True)

    def test_rejects_negative_weight(self):
        with pytest.raises(ValueError, match="weight must not be negative"):
            RiskRule(name="r", weight=-1.0, enabled=True)

    def test_to_dict(self):
        rule = RiskRule(name="r", weight=25.0, enabled=True)

        assert rule.to_dict() == {
            "name": "r", "weight": 25.0, "enabled": True,
        }


class TestRiskAssessment:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            RiskAssessment(deployment_id="", score=0.0, level="LOW")

    def test_rejects_score_out_of_range(self):
        with pytest.raises(
            ValueError, match="score must be between 0.0 and 100.0"
        ):
            RiskAssessment(deployment_id="d1", score=101.0, level="CRITICAL")

    def test_rejects_invalid_level(self):
        with pytest.raises(ValueError, match="level must be one of"):
            RiskAssessment(deployment_id="d1", score=0.0, level="BOGUS")

    def test_rejects_mismatched_level(self):
        with pytest.raises(ValueError, match="does not match score"):
            RiskAssessment(deployment_id="d1", score=0.0, level="CRITICAL")

    def test_to_dict(self):
        assessment = RiskAssessment(
            deployment_id="d1", score=60.0, level="HIGH"
        )

        assert assessment.to_dict() == {
            "deployment_id": "d1", "score": 60.0, "level": "HIGH",
        }


class TestRiskSummary:

    def test_rejects_mismatched_counts(self):
        with pytest.raises(
            ValueError, match="enabled_rules \\+ disabled_rules"
        ):
            RiskSummary(
                total_rules=2, enabled_rules=2, disabled_rules=1,
                total_weight=0.0,
            )

    def test_to_dict(self):
        summary = RiskSummary(
            total_rules=1, enabled_rules=1, disabled_rules=0,
            total_weight=25.0,
        )

        assert summary.to_dict() == {
            "total_rules": 1, "enabled_rules": 1, "disabled_rules": 0,
            "total_weight": 25.0,
        }


class TestConstants:

    def test_risk_levels(self):
        assert RISK_LEVELS == ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_default_risk_factors(self):
        assert set(DEFAULT_RISK_FACTORS) == {
            "production_deployment", "rollback_frequency",
            "failed_health_checks", "required_approvals_missing",
            "policy_violations",
        }


# --- Rule registration ---------------------------------------------------


class TestRuleRegistration:

    def test_register(self):
        engine = _engine()

        rule = engine.register_rule("custom", 20.0)

        assert rule.name == "custom"
        assert rule.weight == 20.0
        assert rule.enabled is True
        assert rule in engine.list()

    def test_register_disabled(self):
        engine = _engine()

        rule = engine.register_rule("custom", 20.0, enabled=False)

        assert rule.enabled is False

    def test_duplicate_name_raises(self):
        engine = _engine()
        engine.register_rule("r1", 10.0)

        with pytest.raises(ValueError, match="already registered"):
            engine.register_rule("r1", 5.0)

    def test_register_with_built_in_factor(self):
        engine = _engine()

        rule = engine.register_rule(
            "prod", 30.0, factor="production_deployment"
        )

        assert rule.name == "prod"

    def test_register_with_unknown_factor_raises(self):
        engine = _engine()

        with pytest.raises(ValueError, match="unknown default risk"):
            engine.register_rule("r1", 10.0, factor="does-not-exist")

    def test_remove_rule(self):
        engine = _engine()
        engine.register_rule("r1", 10.0)

        engine.remove_rule("r1")

        assert "r1" not in {r.name for r in engine.list()}

    def test_remove_unknown_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.remove_rule("does-not-exist")

    def test_list_ordered_by_name(self):
        engine = _engine()
        engine.register_rule("zeta", 10.0)
        engine.register_rule("alpha", 10.0)

        names = [r.name for r in engine.list()]

        assert names == ["alpha", "zeta"]

    def test_no_evaluator_never_triggers(self):
        engine = _engine()
        engine.register_rule("no-op", 50.0)

        assessment = engine.assess("d1")

        assert assessment.score == 0.0
        assert assessment.level == "LOW"


# --- Low-risk assessment ---------------------------------------------------


class TestLowRiskAssessment:

    def test_no_triggered_rules_is_low(self):
        engine = _engine()
        engine.register_rule(
            "prod", 30.0, factor="production_deployment"
        )

        assessment = engine.assess("d1", {"environment": "staging"})

        assert assessment.score == 0.0
        assert assessment.level == "LOW"

    def test_small_triggered_weight_stays_low(self):
        engine = _engine()

        def _always(rule, deployment_id, context):
            return True

        engine.register_rule("minor", 10.0, evaluator=_always)

        assessment = engine.assess("d1")

        assert assessment.score == 10.0
        assert assessment.level == "LOW"

    def test_rejects_empty_deployment_id(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            engine.assess("")

    def test_publishes_risk_assessed(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("risk_assessed", events.append)
        engine = _engine(event_bus=bus)

        engine.assess("d1")

        assert len(events) == 1

    def test_low_risk_does_not_publish_high_or_critical(self):
        bus = GovernanceEventBus()
        high_events = []
        critical_events = []
        bus.subscribe("high_risk_detected", high_events.append)
        bus.subscribe("critical_risk_detected", critical_events.append)
        engine = _engine(event_bus=bus)

        engine.assess("d1")

        assert high_events == []
        assert critical_events == []


# --- High-risk assessment --------------------------------------------------


class TestHighRiskAssessment:

    def test_production_deployment_triggers(self):
        engine = _engine()
        engine.register_rule(
            "prod", 60.0, factor="production_deployment"
        )

        assessment = engine.assess("d1", {"environment": "production"})

        assert assessment.score == 60.0
        assert assessment.level == "HIGH"

    def test_publishes_high_risk_detected(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("high_risk_detected", events.append)
        engine = _engine(event_bus=bus)
        engine.register_rule(
            "prod", 60.0, factor="production_deployment"
        )

        engine.assess("d1", {"environment": "production"})

        assert len(events) == 1

    def test_critical_risk_score(self):
        engine = _engine()
        engine.register_rule(
            "prod", 40.0, factor="production_deployment"
        )
        engine.register_rule(
            "rollbacks", 40.0, factor="rollback_frequency"
        )

        assessment = engine.assess(
            "d1", {"environment": "production", "rollback_count": 5}
        )

        assert assessment.score == 80.0
        assert assessment.level == "CRITICAL"

    def test_publishes_critical_risk_detected(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("critical_risk_detected", events.append)
        engine = _engine(event_bus=bus)
        engine.register_rule(
            "prod", 40.0, factor="production_deployment"
        )
        engine.register_rule(
            "rollbacks", 40.0, factor="rollback_frequency"
        )

        engine.assess(
            "d1", {"environment": "production", "rollback_count": 5}
        )

        assert len(events) == 1

    def test_score_caps_at_100(self):
        engine = _engine()
        engine.register_rule(
            "prod", 70.0, factor="production_deployment"
        )
        engine.register_rule(
            "rollbacks", 70.0, factor="rollback_frequency"
        )

        assessment = engine.assess(
            "d1", {"environment": "production", "rollback_count": 5}
        )

        assert assessment.score == 100.0

    def test_failed_health_checks_factor(self):
        engine = _engine()
        engine.register_rule(
            "health", 50.0, factor="failed_health_checks"
        )

        assessment = engine.assess("d1", {"failed_health_checks": 2})

        assert assessment.score == 50.0
        assert assessment.level == "HIGH"

    def test_required_approvals_missing_context_fallback(self):
        engine = _engine()
        engine.register_rule(
            "approvals", 50.0, factor="required_approvals_missing"
        )

        assessment = engine.assess(
            "d1", {"required_approvals_missing": True}
        )

        assert assessment.score == 50.0

    def test_policy_violations_context_fallback(self):
        engine = _engine()
        engine.register_rule(
            "violations", 50.0, factor="policy_violations"
        )

        assessment = engine.assess("d1", {"policy_violations": 2})

        assert assessment.score == 50.0

    def test_assess_all(self):
        engine = _engine()
        engine.register_rule(
            "prod", 60.0, factor="production_deployment"
        )

        results = engine.assess_all(
            {
                "d2": {"environment": "staging"},
                "d1": {"environment": "production"},
            }
        )

        assert list(results.keys()) == ["d1", "d2"]
        assert results["d1"].level == "HIGH"
        assert results["d2"].level == "LOW"


# --- Approval/compliance engine integration (this commit's Update files) --


class TestEngineIntegration:

    def test_required_approvals_missing_uses_approval_engine(self):
        from backend.observability.deployment_governance_approval import (
            DeploymentApprovalEngine,
        )

        approval = DeploymentApprovalEngine(clock=_clock)
        engine = _engine(approval_engine=approval)
        engine.register_rule(
            "approvals", 50.0, factor="required_approvals_missing"
        )

        missing = engine.assess("d1", {"operation": "deploy"})
        assert missing.score == 50.0

        request = approval.create_request("d1", "deploy", "alice")
        approval.approve(request.request_id, "anyone")

        approved = engine.assess("d1", {"operation": "deploy"})
        assert approved.score == 0.0

    def test_policy_violations_uses_compliance_engine(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)

        def _fails(policy, context):
            return False, "not compliant"

        compliance.register("must-pass", "Security", evaluator=_fails)

        engine = _engine(compliance_engine=compliance)
        engine.register_rule(
            "violations", 50.0, factor="policy_violations"
        )

        assessment = engine.assess("d1")

        assert assessment.score == 50.0

    def test_no_violations_when_compliant(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("passes", "Security")

        engine = _engine(compliance_engine=compliance)
        engine.register_rule(
            "violations", 50.0, factor="policy_violations"
        )

        assessment = engine.assess("d1")

        assert assessment.score == 0.0


# --- Disabled rule handling ------------------------------------------------


class TestDisabledRuleHandling:

    def test_disabled_rule_is_ignored(self):
        engine = _engine()
        engine.register_rule(
            "prod", 60.0, factor="production_deployment", enabled=False
        )

        assessment = engine.assess("d1", {"environment": "production"})

        assert assessment.score == 0.0
        assert assessment.level == "LOW"

    def test_disabled_rule_alongside_enabled(self):
        engine = _engine()
        engine.register_rule(
            "enabled-one", 30.0, factor="production_deployment",
            enabled=True,
        )
        engine.register_rule(
            "disabled-one", 30.0, factor="rollback_frequency",
            enabled=False,
        )

        assessment = engine.assess(
            "d1", {"environment": "production", "rollback_count": 5}
        )

        assert assessment.score == 30.0

    def test_disabled_rule_still_appears_in_list(self):
        engine = _engine()
        engine.register_rule("r1", 10.0, enabled=False)

        assert len(engine.list()) == 1


# --- Latest / summary generation --------------------------------------


class TestLatestAndSummary:

    def test_latest_unassessed_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.latest("does-not-exist")

    def test_latest_returns_last_assessment(self):
        engine = _engine()
        engine.assess("d1")

        assert engine.latest("d1").deployment_id == "d1"

    def test_summary_of_empty_registry(self):
        engine = _engine()

        summary = engine.summary()

        assert summary.total_rules == 0
        assert summary.enabled_rules == 0
        assert summary.disabled_rules == 0
        assert summary.total_weight == 0.0

    def test_summary_counts_and_weight(self):
        engine = _engine()
        engine.register_rule("r1", 10.0, enabled=True)
        engine.register_rule("r2", 20.0, enabled=False)
        engine.register_rule("r3", 30.0, enabled=True)

        summary = engine.summary()

        assert summary.total_rules == 3
        assert summary.enabled_rules == 2
        assert summary.disabled_rules == 1
        assert summary.total_weight == 40.0


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_rules_and_cache(self):
        engine = _engine()
        engine.register_rule("r1", 10.0)
        engine.assess("d1")

        engine.clear()

        assert engine.list() == ()

        with pytest.raises(KeyError):
            engine.latest("d1")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_risk_engine_returns_same_instance(self):
        assert get_risk_engine() is get_risk_engine()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRiskApi:

    def test_post_assess(self, client):
        get_risk_engine().register_rule(
            "api-prod", 60.0, factor="production_deployment"
        )

        response = client.post(
            "/governance/security/risk/assess",
            params={
                "deployment_id": "api-d-1",
                "context": '{"environment": "production"}',
            },
        )

        assert response.status_code == 200
        assert response.json()["level"] == "HIGH"

    def test_get_latest(self, client):
        client.post(
            "/governance/security/risk/assess",
            params={"deployment_id": "api-d-2"},
        )

        response = client.get("/governance/security/risk/api-d-2")

        assert response.status_code == 200
        assert response.json()["deployment_id"] == "api-d-2"

    def test_get_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/risk/does-not-exist"
        )

        assert response.status_code == 404

    def test_get_summary(self, client):
        get_risk_engine().register_rule("api-r1", 10.0)

        response = client.get("/governance/security/risk/summary")

        assert response.status_code == 200
        assert response.json()["total_rules"] >= 1
