from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_incident_response import (
    DEFAULT_RESPONSE_ACTIONS,
    DEFAULT_TRIGGERS,
    INCIDENT_STATUSES,
    SEVERITY_LEVELS,
    DeploymentIncident,
    DeploymentIncidentResponseEngine,
    IncidentAction,
    IncidentSummary,
    get_incident_response_engine,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> DeploymentIncidentResponseEngine:
    return DeploymentIncidentResponseEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The incident response engine is a process-wide singleton wired to
    the process-wide event bus (and every other governance singleton);
    most tests below construct their own fresh engine instead (see
    _engine), and only the singleton and API tests touch the shared
    instance, matching test_deployment_governance_risk.py's own
    fixture.
    """

    def _reset():
        get_incident_response_engine().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestDeploymentIncident:

    def test_rejects_empty_incident_id(self):
        with pytest.raises(
            ValueError, match="incident_id must not be empty"
        ):
            DeploymentIncident(
                incident_id="", severity="LOW", status="OPEN",
                source="s1",
            )

    def test_rejects_invalid_severity(self):
        with pytest.raises(ValueError, match="severity must be one of"):
            DeploymentIncident(
                incident_id="i1", severity="BOGUS", status="OPEN",
                source="s1",
            )

    def test_rejects_invalid_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            DeploymentIncident(
                incident_id="i1", severity="LOW", status="BOGUS",
                source="s1",
            )

    def test_rejects_empty_source(self):
        with pytest.raises(ValueError, match="source must not be empty"):
            DeploymentIncident(
                incident_id="i1", severity="LOW", status="OPEN",
                source="",
            )

    def test_to_dict(self):
        incident = DeploymentIncident(
            incident_id="i1", severity="HIGH", status="OPEN",
            source="s1",
        )

        assert incident.to_dict() == {
            "incident_id": "i1", "severity": "HIGH", "status": "OPEN",
            "source": "s1",
        }


class TestIncidentAction:

    def test_to_dict(self):
        action = IncidentAction(action="Flag Deployment", executed=True)

        assert action.to_dict() == {
            "action": "Flag Deployment", "executed": True,
        }


class TestIncidentSummary:

    def test_rejects_mismatched_counts(self):
        with pytest.raises(
            ValueError, match="open_incidents \\+ resolved_incidents"
        ):
            IncidentSummary(
                total_incidents=2, open_incidents=2,
                resolved_incidents=1, critical_incidents=0,
            )

    def test_to_dict(self):
        summary = IncidentSummary(
            total_incidents=1, open_incidents=1, resolved_incidents=0,
            critical_incidents=1,
        )

        assert summary.to_dict() == {
            "total_incidents": 1, "open_incidents": 1,
            "resolved_incidents": 0, "critical_incidents": 1,
        }


class TestConstants:

    def test_severity_levels(self):
        assert SEVERITY_LEVELS == ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_incident_statuses(self):
        assert INCIDENT_STATUSES == ("OPEN", "ASSIGNED", "RESOLVED")

    def test_default_triggers(self):
        assert set(DEFAULT_TRIGGERS) == {
            "critical_security_finding", "integrity_verification_failure",
            "repeated_authentication_failures", "compliance_violation",
            "critical_risk_score",
        }

    def test_default_response_actions(self):
        assert set(DEFAULT_RESPONSE_ACTIONS) == {
            "Flag Deployment", "Pause Rollout", "Require Manual Approval",
            "Trigger Rollback", "Record Audit Event",
        }


# --- Incident creation ---------------------------------------------------


class TestIncidentCreation:

    def test_create(self):
        engine = _engine()

        incident = engine.create("s1", "LOW")

        assert incident.source == "s1"
        assert incident.severity == "LOW"
        assert incident.status == "OPEN"

    def test_rejects_empty_source(self):
        engine = _engine()

        with pytest.raises(ValueError, match="source must not be empty"):
            engine.create("", "LOW")

    def test_rejects_invalid_severity(self):
        engine = _engine()

        with pytest.raises(ValueError, match="severity must be one of"):
            engine.create("s1", "BOGUS")

    def test_publishes_incident_created(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("incident_created", events.append)
        engine = _engine(event_bus=bus)

        engine.create("s1", "LOW")

        assert len(events) == 1

    def test_flag_deployment_executes_at_medium_and_above(self):
        engine = _engine()

        engine.create("s1", "MEDIUM")

        assert engine.is_flagged("s1") is True

    def test_flag_deployment_not_run_at_low_severity(self):
        engine = _engine()

        engine.create("s1", "LOW")

        assert engine.is_flagged("s1") is False

    def test_low_severity_only_records_audit_event(self):
        engine = _engine()

        incident = engine.create("s1", "LOW")

        actions = engine.actions(incident.incident_id)

        assert [a.action for a in actions] == ["Record Audit Event"]

    def test_critical_severity_runs_every_action(self):
        engine = _engine()

        incident = engine.create("s1", "CRITICAL")

        actions = engine.actions(incident.incident_id)

        assert [a.action for a in actions] == list(
            DEFAULT_RESPONSE_ACTIONS
        )

    def test_actions_unknown_incident_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.actions("does-not-exist")


# --- Severity classification -----------------------------------------


class TestSeverityClassification:

    def test_low_severity_action_set(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")

        actions = {a.action for a in engine.actions(incident.incident_id)}

        assert actions == {"Record Audit Event"}

    def test_medium_severity_action_set(self):
        engine = _engine()
        incident = engine.create("s1", "MEDIUM")

        actions = {a.action for a in engine.actions(incident.incident_id)}

        assert actions == {"Flag Deployment", "Record Audit Event"}

    def test_high_severity_action_set(self):
        engine = _engine()
        incident = engine.create("s1", "HIGH")

        actions = {a.action for a in engine.actions(incident.incident_id)}

        assert actions == {
            "Flag Deployment", "Require Manual Approval",
            "Record Audit Event",
        }

    def test_critical_severity_action_set(self):
        engine = _engine()
        incident = engine.create("s1", "CRITICAL")

        actions = {a.action for a in engine.actions(incident.incident_id)}

        assert actions == set(DEFAULT_RESPONSE_ACTIONS)

    def test_record_audit_event_uses_wired_audit_service(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )

        audit_service = GovernanceAuditService(clock=_clock)
        engine = _engine(audit_service=audit_service)

        incident = engine.create("s1", "LOW")

        actions = engine.actions(incident.incident_id)

        assert actions[0].executed is True
        assert len(audit_service.latest()) == 1

    def test_record_audit_event_false_without_audit_service(self):
        engine = _engine()

        incident = engine.create("s1", "LOW")

        actions = engine.actions(incident.incident_id)

        assert actions[0].executed is False

    def test_require_manual_approval_uses_wired_approval_engine(self):
        from backend.observability.deployment_governance_approval import (
            DeploymentApprovalEngine,
        )

        approval_engine = DeploymentApprovalEngine(clock=_clock)
        engine = _engine(approval_engine=approval_engine)

        incident = engine.create("s1", "HIGH")

        actions = {
            a.action: a.executed
            for a in engine.actions(incident.incident_id)
        }

        assert actions["Require Manual Approval"] is True
        assert len(approval_engine.list_pending()) == 1


# --- Duplicate detection (one active incident per source) ------------


class TestDuplicateDetection:

    def test_duplicate_same_severity_raises(self):
        engine = _engine()
        engine.create("s1", "LOW")

        with pytest.raises(ValueError, match="already has an active"):
            engine.create("s1", "LOW")

    def test_duplicate_lower_severity_raises(self):
        engine = _engine()
        engine.create("s1", "HIGH")

        with pytest.raises(ValueError, match="already has an active"):
            engine.create("s1", "LOW")

    def test_duplicate_higher_severity_escalates(self):
        engine = _engine()
        original = engine.create("s1", "LOW")

        escalated = engine.create("s1", "CRITICAL")

        assert escalated.incident_id == original.incident_id
        assert escalated.severity == "CRITICAL"

    def test_publishes_incident_escalated(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("incident_escalated", events.append)
        engine = _engine(event_bus=bus)
        engine.create("s1", "LOW")

        engine.create("s1", "CRITICAL")

        assert len(events) == 1

    def test_new_incident_allowed_after_resolution(self):
        engine = _engine()
        first = engine.create("s1", "LOW")
        engine.resolve(first.incident_id)

        second = engine.create("s1", "LOW")

        assert second.incident_id != first.incident_id
        assert second.status == "OPEN"

    def test_detect_does_not_raise_on_repeated_trigger(self):
        engine = _engine()

        first = engine.detect(
            "d1", {"critical_security_finding": True}
        )
        second = engine.detect(
            "d1", {"critical_security_finding": True}
        )

        assert len(first) == 1
        assert len(second) == 1
        assert first[0].incident_id == second[0].incident_id

    def test_detect_publishes_incident_detected(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("incident_detected", events.append)
        engine = _engine(event_bus=bus)

        engine.detect("d1", {"critical_security_finding": True})

        assert len(events) == 1

    def test_detect_rejects_empty_target_id(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="target_id must not be empty"
        ):
            engine.detect("")

    def test_detect_no_triggers_returns_empty(self):
        engine = _engine()

        assert engine.detect("d1") == ()


# --- Resolution workflow -------------------------------------------------


class TestResolutionWorkflow:

    def test_resolve_transitions_to_resolved(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")

        resolved = engine.resolve(incident.incident_id)

        assert resolved.status == "RESOLVED"

    def test_resolve_is_idempotent(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")
        engine.resolve(incident.incident_id)

        resolved_again = engine.resolve(incident.incident_id)

        assert resolved_again.status == "RESOLVED"

    def test_resolve_unknown_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.resolve("does-not-exist")

    def test_publishes_incident_resolved(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("incident_resolved", events.append)
        engine = _engine(event_bus=bus)
        incident = engine.create("s1", "LOW")

        engine.resolve(incident.incident_id)

        assert len(events) == 1

    def test_assign_transitions_to_assigned(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")

        assigned = engine.assign(incident.incident_id, "alice")

        assert assigned.status == "ASSIGNED"

    def test_assign_is_idempotent(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")
        engine.assign(incident.incident_id, "alice")

        assigned_again = engine.assign(incident.incident_id, "bob")

        assert assigned_again.status == "ASSIGNED"

    def test_assign_unknown_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.assign("does-not-exist", "alice")

    def test_assign_resolved_raises(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")
        engine.resolve(incident.incident_id)

        with pytest.raises(ValueError, match="already resolved"):
            engine.assign(incident.incident_id, "alice")

    def test_assign_rejects_empty_assignee(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")

        with pytest.raises(
            ValueError, match="assignee must not be empty"
        ):
            engine.assign(incident.incident_id, "")

    def test_resolve_then_assign_raises(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")
        engine.resolve(incident.incident_id)

        with pytest.raises(ValueError):
            engine.assign(incident.incident_id, "alice")


# --- History retrieval ---------------------------------------------------


class TestHistoryRetrieval:

    def test_history_includes_every_incident(self):
        engine = _engine()
        engine.create("s1", "LOW")
        engine.create("s2", "HIGH")

        history = engine.history()

        assert len(history) == 2

    def test_history_ordered_by_creation(self):
        engine = _engine()
        first = engine.create("s1", "LOW")
        second = engine.create("s2", "HIGH")

        history = engine.history()

        assert [i.incident_id for i in history] == [
            first.incident_id, second.incident_id,
        ]

    def test_history_includes_resolved(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")
        engine.resolve(incident.incident_id)

        assert len(engine.history()) == 1

    def test_get_unknown_raises(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.get("does-not-exist")

    def test_get_returns_current_state(self):
        engine = _engine()
        incident = engine.create("s1", "LOW")
        engine.resolve(incident.incident_id)

        assert engine.get(incident.incident_id).status == "RESOLVED"


# --- Repeated authentication failures (event-driven trigger) -----------


class TestRepeatedAuthenticationFailures:

    def test_three_failures_trigger_incident(self):
        from backend.observability.deployment_governance_authentication import (  # noqa: E501
            DeploymentAuthenticationManager,
        )

        bus = GovernanceEventBus()
        engine = _engine(event_bus=bus)
        auth_manager = DeploymentAuthenticationManager(
            clock=_clock, event_bus=bus,
        )

        for _ in range(3):
            auth_manager.authenticate(
                "mallory", "LOCAL", {"password": "wrong"}
            )

        incidents = engine.detect("mallory")

        assert len(incidents) == 1
        assert incidents[0].severity == "HIGH"

    def test_successful_authentication_resets_counter(self):
        from backend.observability.deployment_governance_authentication import (  # noqa: E501
            DeploymentAuthenticationManager,
        )

        bus = GovernanceEventBus()
        engine = _engine(event_bus=bus)
        auth_manager = DeploymentAuthenticationManager(
            clock=_clock, event_bus=bus,
        )
        auth_manager.register_local_credential("alice", "hunter2")

        for _ in range(2):
            auth_manager.authenticate(
                "alice", "LOCAL", {"password": "wrong"}
            )

        auth_manager.authenticate(
            "alice", "LOCAL", {"password": "hunter2"}
        )

        incidents = engine.detect("alice")

        assert incidents == ()

    def test_no_event_bus_relies_on_context_only(self):
        engine = _engine()

        incidents = engine.detect(
            "mallory", {"authentication_failures": 3}
        )

        assert len(incidents) == 1


# --- Compliance / risk trigger integration ------------------------------


class TestOtherTriggerIntegration:

    def test_critical_security_finding_via_scanner(self):
        from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
            DeploymentSecurityScanner,
            SecurityFinding,
        )

        class _Plugin:
            def scan(self, deployment_id, context):
                return (
                    SecurityFinding(
                        severity="CRITICAL", category="c",
                        description="d",
                    ),
                )

        scanner = DeploymentSecurityScanner(clock=_clock)
        scanner.register_scanner("plugin", plugin=_Plugin())
        scanner.scan("d1")

        engine = _engine(security_scanner=scanner)

        incidents = engine.detect("d1")

        assert any(
            i.source.startswith("critical_security_finding:")
            for i in incidents
        )

    def test_integrity_verification_failure_via_verifier(self):
        from backend.observability.deployment_governance_artifact_integrity import (  # noqa: E501
            DeploymentIntegrityVerifier,
        )

        verifier = DeploymentIntegrityVerifier(clock=_clock)
        verifier.register_rule("checksum", "SHA-256")
        verifier.verify("a1", "content", {"expected_sha256": "wrong"})

        engine = _engine(integrity_verifier=verifier)

        incidents = engine.detect("a1")

        assert any(
            i.source.startswith("integrity_verification_failure:")
            for i in incidents
        )

    def test_compliance_violation_via_compliance_engine(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        def _fails(policy, context):
            return False, "nope"

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security", evaluator=_fails)

        engine = _engine(compliance_engine=compliance)

        incidents = engine.detect("d1")

        assert any(
            i.source.startswith("compliance_violation:")
            for i in incidents
        )

    def test_critical_risk_score_via_risk_engine(self):
        from backend.observability.deployment_governance_risk import (
            DeploymentRiskEngine,
        )

        risk_engine = DeploymentRiskEngine(clock=_clock)
        risk_engine.register_rule(
            "prod", 80.0, factor="production_deployment"
        )
        risk_engine.assess("d1", {"environment": "production"})

        engine = _engine(risk_engine=risk_engine)

        incidents = engine.detect("d1")

        assert any(
            i.source.startswith("critical_risk_score:")
            for i in incidents
        )


# --- Summary generation --------------------------------------------------


class TestSummaryGeneration:

    def test_summary_of_empty_engine(self):
        engine = _engine()

        summary = engine.summary()

        assert summary.total_incidents == 0
        assert summary.open_incidents == 0
        assert summary.resolved_incidents == 0
        assert summary.critical_incidents == 0

    def test_summary_counts(self):
        engine = _engine()
        first = engine.create("s1", "LOW")
        engine.create("s2", "CRITICAL")
        engine.resolve(first.incident_id)

        summary = engine.summary()

        assert summary.total_incidents == 2
        assert summary.open_incidents == 1
        assert summary.resolved_incidents == 1
        assert summary.critical_incidents == 1


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_everything(self):
        engine = _engine()
        engine.create("s1", "LOW")

        engine.clear()

        assert engine.history() == ()
        assert engine.is_flagged("s1") is False


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_incident_response_engine_returns_same_instance(self):
        assert (
            get_incident_response_engine()
            is get_incident_response_engine()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceIncidentApi:

    def test_post_creates_incident(self, client):
        response = client.post(
            "/governance/security/incidents",
            params={"source": "api-s-1", "severity": "LOW"},
        )

        assert response.status_code == 200
        assert response.json()["source"] == "api-s-1"

    def test_post_duplicate_returns_409(self, client):
        client.post(
            "/governance/security/incidents",
            params={"source": "api-s-2", "severity": "HIGH"},
        )

        response = client.post(
            "/governance/security/incidents",
            params={"source": "api-s-2", "severity": "LOW"},
        )

        assert response.status_code == 409

    def test_get_list(self, client):
        client.post(
            "/governance/security/incidents",
            params={"source": "api-s-3", "severity": "LOW"},
        )

        response = client.get("/governance/security/incidents")

        assert response.status_code == 200
        assert any(
            i["source"] == "api-s-3" for i in response.json()
        )

    def test_get_by_id(self, client):
        create_response = client.post(
            "/governance/security/incidents",
            params={"source": "api-s-4", "severity": "LOW"},
        )

        incident_id = create_response.json()["incident_id"]

        response = client.get(
            f"/governance/security/incidents/{incident_id}"
        )

        assert response.status_code == 200
        assert response.json()["incident_id"] == incident_id

    def test_get_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/incidents/does-not-exist"
        )

        assert response.status_code == 404

    def test_post_resolve(self, client):
        create_response = client.post(
            "/governance/security/incidents",
            params={"source": "api-s-5", "severity": "LOW"},
        )

        incident_id = create_response.json()["incident_id"]

        response = client.post(
            f"/governance/security/incidents/{incident_id}/resolve",
        )

        assert response.status_code == 200
        assert response.json()["status"] == "RESOLVED"

    def test_post_resolve_unknown_returns_404(self, client):
        response = client.post(
            "/governance/security/incidents/does-not-exist/resolve",
        )

        assert response.status_code == 404
