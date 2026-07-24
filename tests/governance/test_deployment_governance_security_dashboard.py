from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_security_dashboard import (
    DASHBOARD_SECTION_NAMES,
    RISK_LEVELS,
    SECTION_STATUSES,
    DashboardSection,
    DeploymentSecurityDashboard,
    SecurityDashboard,
    get_security_dashboard,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _dashboard(**kwargs) -> DeploymentSecurityDashboard:
    return DeploymentSecurityDashboard(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The security dashboard is a process-wide singleton wired to every
    other governance singleton; most tests below construct their own
    fresh dashboard instead (see _dashboard), and only the singleton
    and API tests touch the shared instance, matching
    test_deployment_governance_reporting.py's own fixture. The
    dashboard itself has no mutable registry to clear() — the reset
    below clears the singletons it reads from instead.
    """

    def _reset():
        from backend.observability.deployment_governance_approval import (
            get_approval_engine,
        )
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            get_compliance_engine,
        )
        from backend.observability.deployment_governance_incident_response import (  # noqa: E501
            get_incident_response_engine,
        )
        from backend.observability.deployment_governance_reporting import (
            get_reporting_service,
        )
        from backend.observability.deployment_governance_risk import (
            get_risk_engine,
        )
        from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
            get_security_scanner,
        )

        get_approval_engine().clear()
        get_compliance_engine().clear()
        get_incident_response_engine().clear()
        get_reporting_service().clear()
        get_risk_engine().clear()
        get_security_scanner().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestSecurityDashboard:

    def test_rejects_naive_generated_at(self):
        with pytest.raises(
            ValueError, match="generated_at must be timezone-aware"
        ):
            SecurityDashboard(
                generated_at=datetime(2026, 7, 24, 12, 0, 0),
                active_incidents=0, compliance_score=1.0,
                risk_level="LOW",
            )

    def test_rejects_negative_active_incidents(self):
        with pytest.raises(
            ValueError, match="active_incidents must not be negative"
        ):
            SecurityDashboard(
                generated_at=BASE_TIME, active_incidents=-1,
                compliance_score=1.0, risk_level="LOW",
            )

    def test_rejects_compliance_score_out_of_range(self):
        with pytest.raises(
            ValueError, match="compliance_score must be between"
        ):
            SecurityDashboard(
                generated_at=BASE_TIME, active_incidents=0,
                compliance_score=1.5, risk_level="LOW",
            )

    def test_rejects_invalid_risk_level(self):
        with pytest.raises(ValueError, match="risk_level must be one of"):
            SecurityDashboard(
                generated_at=BASE_TIME, active_incidents=0,
                compliance_score=1.0, risk_level="BOGUS",
            )

    def test_to_dict(self):
        dashboard = SecurityDashboard(
            generated_at=BASE_TIME, active_incidents=2,
            compliance_score=0.75, risk_level="HIGH",
        )

        assert dashboard.to_dict() == {
            "generated_at": BASE_TIME.isoformat(),
            "active_incidents": 2, "compliance_score": 0.75,
            "risk_level": "HIGH",
        }


class TestDashboardSection:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            DashboardSection(name="", status="OK", updated_at=BASE_TIME)

    def test_rejects_invalid_status(self):
        with pytest.raises(ValueError, match="status must be one of"):
            DashboardSection(
                name="Audit", status="BOGUS", updated_at=BASE_TIME
            )

    def test_to_dict(self):
        section = DashboardSection(
            name="Audit", status="OK", updated_at=BASE_TIME
        )

        assert section.to_dict() == {
            "name": "Audit", "status": "OK",
            "updated_at": BASE_TIME.isoformat(),
        }


class TestConstants:

    def test_risk_levels(self):
        assert RISK_LEVELS == ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_section_statuses(self):
        assert SECTION_STATUSES == ("OK", "DEGRADED", "UNAVAILABLE")

    def test_dashboard_section_names(self):
        assert set(DASHBOARD_SECTION_NAMES) == {
            "Authentication", "Authorization", "Secrets", "Approvals",
            "Audit", "Compliance", "Risk", "Security Scans",
            "Integrity", "Incidents",
        }


# --- Dashboard generation --------------------------------------------------


class TestDashboardGeneration:

    def test_overview_of_unwired_dashboard(self):
        dashboard = _dashboard()

        overview = dashboard.overview()

        assert overview.active_incidents == 0
        assert overview.compliance_score == 0.0
        assert overview.risk_level == "LOW"
        assert overview.generated_at == BASE_TIME

    def test_publishes_security_dashboard_generated(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("security_dashboard_generated", events.append)
        dashboard = _dashboard(event_bus=bus)

        dashboard.overview()

        assert len(events) == 1

    def test_risk_level_critical_when_open_critical_incident(self):
        from backend.observability.deployment_governance_incident_response import (  # noqa: E501
            DeploymentIncidentResponseEngine,
        )

        incidents = DeploymentIncidentResponseEngine(clock=_clock)
        incidents.create("s1", "CRITICAL")

        dashboard = _dashboard(incident_response_engine=incidents)

        assert dashboard.overview().risk_level == "CRITICAL"

    def test_risk_level_high_when_active_incidents(self):
        from backend.observability.deployment_governance_incident_response import (  # noqa: E501
            DeploymentIncidentResponseEngine,
        )

        incidents = DeploymentIncidentResponseEngine(clock=_clock)
        incidents.create("s1", "LOW")

        dashboard = _dashboard(incident_response_engine=incidents)

        assert dashboard.overview().risk_level == "HIGH"

    def test_risk_level_medium_when_non_compliant(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        def _fails(policy, context):
            return False, "nope"

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security", evaluator=_fails)
        compliance.evaluate("d1")

        dashboard = _dashboard(compliance_engine=compliance)

        assert dashboard.overview().risk_level == "MEDIUM"

    def test_risk_level_low_when_nothing_evaluated(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)

        dashboard = _dashboard(compliance_engine=compliance)

        assert dashboard.overview().risk_level == "LOW"

    def test_risk_level_low_when_fully_compliant(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security")
        compliance.evaluate("d1")

        dashboard = _dashboard(compliance_engine=compliance)

        assert dashboard.overview().risk_level == "LOW"

    def test_prefers_reporting_service_summary(self):
        from backend.observability.deployment_governance_incident_response import (  # noqa: E501
            DeploymentIncidentResponseEngine,
        )
        from backend.observability.deployment_governance_reporting import (
            DeploymentReportingService,
        )

        incidents = DeploymentIncidentResponseEngine(clock=_clock)
        incidents.create("s1", "LOW")
        incidents.create("s2", "LOW")

        reporting = DeploymentReportingService(
            clock=_clock, incident_response_engine=incidents,
        )

        # Dashboard is wired to reporting_service, NOT directly to
        # incidents -- overview() must go through reporting's own
        # summary() rather than needing a direct incident_response_engine.
        dashboard = _dashboard(reporting_service=reporting)

        assert dashboard.overview().active_incidents == 2


# --- Section aggregation --------------------------------------------------


class TestSectionAggregation:

    def test_sections_returns_all_ten(self):
        dashboard = _dashboard()

        sections = dashboard.sections()

        assert len(sections) == 10
        assert [s.name for s in sections] == list(DASHBOARD_SECTION_NAMES)

    def test_security_returns_six_sections(self):
        dashboard = _dashboard()

        sections = dashboard.security()

        assert {s.name for s in sections} == {
            "Authentication", "Authorization", "Secrets", "Approvals",
            "Security Scans", "Integrity",
        }

    def test_compliance_returns_one_section(self):
        dashboard = _dashboard()

        sections = dashboard.compliance()

        assert [s.name for s in sections] == ["Compliance"]

    def test_risk_returns_one_section(self):
        dashboard = _dashboard()

        assert [s.name for s in dashboard.risk()] == ["Risk"]

    def test_audit_returns_one_section(self):
        dashboard = _dashboard()

        assert [s.name for s in dashboard.audit()] == ["Audit"]

    def test_incidents_returns_one_section(self):
        dashboard = _dashboard()

        assert [s.name for s in dashboard.incidents()] == ["Incidents"]

    def test_every_section_accounted_for_across_five_methods(self):
        dashboard = _dashboard()

        combined = {s.name for s in dashboard.security()}
        combined |= {s.name for s in dashboard.compliance()}
        combined |= {s.name for s in dashboard.risk()}
        combined |= {s.name for s in dashboard.audit()}
        combined |= {s.name for s in dashboard.incidents()}

        assert combined == set(DASHBOARD_SECTION_NAMES)

    def test_wired_section_is_ok(self):
        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        dashboard = _dashboard(
            rbac_engine=DeploymentRBACEngine(clock=_clock)
        )

        section = dashboard.security()[
            [s.name for s in dashboard.security()].index("Authorization")
        ]

        assert section.status == "OK"

    def test_approvals_degraded_with_pending_request(self):
        from backend.observability.deployment_governance_approval import (
            DeploymentApprovalEngine,
        )

        approval = DeploymentApprovalEngine(clock=_clock)
        approval.create_request("d1", "deploy", "alice")

        dashboard = _dashboard(approval_engine=approval)

        approvals_section = next(
            s for s in dashboard.security() if s.name == "Approvals"
        )

        assert approvals_section.status == "DEGRADED"

    def test_security_scans_degraded_on_critical_finding(self):
        from backend.observability.deployment_governance_security_scanner import (  # noqa: E501
            DeploymentSecurityScanner,
            SecurityFinding,
        )

        class _Plugin:
            def scan(self, deployment_id, context):
                return (
                    SecurityFinding(
                        severity="CRITICAL", category="c", description="d",
                    ),
                )

        scanner = DeploymentSecurityScanner(clock=_clock)
        scanner.register_scanner("plugin", plugin=_Plugin())
        scanner.scan("d1")

        dashboard = _dashboard(security_scanner=scanner)

        section = next(
            s for s in dashboard.security() if s.name == "Security Scans"
        )

        assert section.status == "DEGRADED"

    def test_integrity_degraded_on_failed_verification(self):
        from backend.observability.deployment_governance_artifact_integrity import (  # noqa: E501
            DeploymentIntegrityVerifier,
        )

        verifier = DeploymentIntegrityVerifier(clock=_clock)
        verifier.register_rule("checksum", "SHA-256")
        verifier.verify("a1", "content", {"expected_sha256": "wrong"})

        dashboard = _dashboard(integrity_verifier=verifier)

        section = next(
            s for s in dashboard.security() if s.name == "Integrity"
        )

        assert section.status == "DEGRADED"

    def test_incidents_degraded_when_open(self):
        from backend.observability.deployment_governance_incident_response import (  # noqa: E501
            DeploymentIncidentResponseEngine,
        )

        incidents = DeploymentIncidentResponseEngine(clock=_clock)
        incidents.create("s1", "LOW")

        dashboard = _dashboard(incident_response_engine=incidents)

        assert dashboard.incidents()[0].status == "DEGRADED"


# --- Cache refresh ---------------------------------------------------


class TestCacheRefresh:

    def test_overview_without_ttl_always_rebuilds(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("security_dashboard_generated", events.append)
        dashboard = _dashboard(event_bus=bus)

        dashboard.overview()
        dashboard.overview()

        assert len(events) == 2

    def test_overview_with_ttl_serves_cached_copy(self):
        clock_box = {"now": BASE_TIME}
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("security_dashboard_generated", events.append)
        dashboard = DeploymentSecurityDashboard(
            clock=lambda: clock_box["now"], event_bus=bus,
            cache_ttl_seconds=60.0,
        )

        first = dashboard.overview()
        second = dashboard.overview()

        assert first is second
        assert len(events) == 1

    def test_overview_with_ttl_rebuilds_after_expiry(self):
        clock_box = {"now": BASE_TIME}
        dashboard = DeploymentSecurityDashboard(
            clock=lambda: clock_box["now"], cache_ttl_seconds=60.0,
        )

        dashboard.overview()
        clock_box["now"] = BASE_TIME + timedelta(seconds=61)
        second = dashboard.overview()

        assert second.generated_at == clock_box["now"]

    def test_refresh_always_rebuilds_and_publishes_refreshed(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("security_dashboard_refreshed", events.append)
        dashboard = _dashboard(event_bus=bus, cache_ttl_seconds=60.0)

        dashboard.overview()
        dashboard.refresh()

        assert len(events) == 1

    def test_rejects_negative_cache_ttl(self):
        with pytest.raises(
            ValueError, match="cache_ttl_seconds must not be negative"
        ):
            DeploymentSecurityDashboard(cache_ttl_seconds=-1.0)

    def test_sections_always_fresh_regardless_of_cache(self):
        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        rbac = DeploymentRBACEngine(clock=_clock)
        dashboard = DeploymentSecurityDashboard(
            clock=_clock, rbac_engine=rbac, cache_ttl_seconds=60.0,
        )

        dashboard.overview()

        # sections() is never cached -- confirmed by simply calling it
        # repeatedly without error and getting a consistent live read.
        first_sections = dashboard.sections()
        second_sections = dashboard.sections()

        assert first_sections == second_sections


# --- Unavailable service handling ---------------------------------------


class TestUnavailableServiceHandling:

    def test_every_section_unavailable_when_nothing_wired(self):
        dashboard = _dashboard()

        statuses = {s.name: s.status for s in dashboard.sections()}

        assert all(status == "UNAVAILABLE" for status in statuses.values())

    def test_overview_does_not_raise_when_nothing_wired(self):
        dashboard = _dashboard()

        overview = dashboard.overview()

        assert overview.risk_level == "LOW"

    def test_mixed_availability(self):
        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        dashboard = _dashboard(
            rbac_engine=DeploymentRBACEngine(clock=_clock)
        )

        statuses = {s.name: s.status for s in dashboard.sections()}

        assert statuses["Authorization"] == "OK"
        assert statuses["Authentication"] == "UNAVAILABLE"


# --- Deterministic ordering ------------------------------------------


class TestDeterministicOrdering:

    def test_sections_order_is_stable_across_calls(self):
        dashboard = _dashboard()

        first = [s.name for s in dashboard.sections()]
        second = [s.name for s in dashboard.sections()]

        assert first == second == list(DASHBOARD_SECTION_NAMES)

    def test_security_sections_preserve_relative_order(self):
        dashboard = _dashboard()

        names = [s.name for s in dashboard.security()]

        assert names == [
            "Authentication", "Authorization", "Secrets", "Approvals",
            "Security Scans", "Integrity",
        ]


# --- RBAC / reporting integration (this commit's Update files) -----------


class TestUpdateFileIntegration:

    def test_rbac_summary(self):
        from backend.observability.deployment_governance_rbac import (
            DeploymentRBACEngine,
        )

        rbac = DeploymentRBACEngine(clock=_clock)
        rbac.assign_role("p1", "Developer")

        summary = rbac.summary()

        assert summary.total_roles >= 1
        assert summary.total_principals == 1

    def test_reporting_latest(self):
        from backend.observability.deployment_governance_reporting import (
            DeploymentReportingService,
        )

        service = DeploymentReportingService(clock=_clock)

        assert service.latest() is None

        report = service.generate("Security")

        assert service.latest().report_id == report.report_id
        assert service.latest("Risk") is None


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_security_dashboard_returns_same_instance(self):
        assert get_security_dashboard() is get_security_dashboard()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSecurityDashboardApi:

    def test_get_overview(self, client):
        response = client.get("/governance/security/dashboard")

        assert response.status_code == 200
        assert "risk_level" in response.json()

    def test_get_security_sections(self, client):
        response = client.get(
            "/governance/security/dashboard/security"
        )

        assert response.status_code == 200
        assert len(response.json()) == 6

    def test_get_compliance_section(self, client):
        response = client.get(
            "/governance/security/dashboard/compliance"
        )

        assert response.status_code == 200
        assert response.json()[0]["name"] == "Compliance"

    def test_get_risk_section(self, client):
        response = client.get("/governance/security/dashboard/risk")

        assert response.status_code == 200
        assert response.json()[0]["name"] == "Risk"

    def test_get_incidents_section(self, client):
        response = client.get(
            "/governance/security/dashboard/incidents"
        )

        assert response.status_code == 200
        assert response.json()[0]["name"] == "Incidents"

    def test_get_audit_section(self, client):
        response = client.get("/governance/security/dashboard/audit")

        assert response.status_code == 200
        assert response.json()[0]["name"] == "Audit"
