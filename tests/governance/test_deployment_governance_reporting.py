from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_reporting import (
    EXPORT_FORMATS,
    REPORT_TYPES,
    DeploymentReportingService,
    GovernanceReport,
    ReportSummary,
    get_reporting_service,
)

BASE_TIME = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _service(**kwargs) -> DeploymentReportingService:
    return DeploymentReportingService(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The reporting service is a process-wide singleton wired to every
    other governance singleton; most tests below construct their own
    fresh service instead (see _service), and only the singleton and
    API tests touch the shared instance, matching
    test_deployment_governance_incident_response.py's own fixture.
    """

    def _reset():
        get_reporting_service().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestGovernanceReport:

    def test_rejects_empty_report_id(self):
        with pytest.raises(ValueError, match="report_id must not be empty"):
            GovernanceReport(
                report_id="", generated_at=BASE_TIME,
                report_type="Security",
            )

    def test_rejects_naive_generated_at(self):
        with pytest.raises(
            ValueError, match="generated_at must be timezone-aware"
        ):
            GovernanceReport(
                report_id="r1",
                generated_at=datetime(2026, 7, 24, 12, 0, 0),
                report_type="Security",
            )

    def test_rejects_invalid_report_type(self):
        with pytest.raises(
            ValueError, match="report_type must be one of"
        ):
            GovernanceReport(
                report_id="r1", generated_at=BASE_TIME,
                report_type="Bogus",
            )

    def test_to_dict(self):
        report = GovernanceReport(
            report_id="r1", generated_at=BASE_TIME,
            report_type="Security",
        )

        assert report.to_dict() == {
            "report_id": "r1",
            "generated_at": BASE_TIME.isoformat(),
            "report_type": "Security",
        }


class TestReportSummary:

    def test_rejects_compliance_rate_out_of_range(self):
        with pytest.raises(
            ValueError, match="compliance_rate must be between"
        ):
            ReportSummary(
                total_deployments=1, compliance_rate=1.5,
                incident_count=0,
            )

    def test_to_dict(self):
        summary = ReportSummary(
            total_deployments=3, compliance_rate=0.5, incident_count=2,
        )

        assert summary.to_dict() == {
            "total_deployments": 3, "compliance_rate": 0.5,
            "incident_count": 2,
        }


class TestConstants:

    def test_report_types(self):
        assert set(REPORT_TYPES) == {
            "Security", "Compliance", "Audit", "Risk",
            "Deployment Summary",
        }

    def test_export_formats(self):
        assert set(EXPORT_FORMATS) == {"json", "csv"}


# --- Report generation ---------------------------------------------------


class TestReportGeneration:

    def test_generate_returns_report(self):
        service = _service()

        report = service.generate("Security")

        assert report.report_type == "Security"
        assert report.generated_at == BASE_TIME

    def test_generate_rejects_unknown_type(self):
        service = _service()

        with pytest.raises(ValueError, match="report_type must be one of"):
            service.generate("Bogus")

    def test_publishes_report_generated(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("report_generated", events.append)
        service = _service(event_bus=bus)

        service.generate("Security")

        assert len(events) == 1

    def test_section_omitted_when_engine_not_wired(self):
        service = _service()

        report = service.generate("Compliance")
        data = service.get(report.report_id)

        assert data["sections"]["compliance"] == {}

    def test_section_populated_when_engine_wired(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security")
        service = _service(compliance_engine=compliance)

        report = service.generate("Compliance")
        data = service.get(report.report_id)

        assert data["sections"]["compliance"]["total_policies"] == 1

    def test_deployment_summary_includes_every_section(self):
        service = _service()

        report = service.generate("Deployment Summary")
        data = service.get(report.report_id)

        assert set(data["sections"].keys()) == {
            "compliance", "risk", "incidents", "integrity", "audit",
        }

    def test_security_report_sections(self):
        service = _service()

        report = service.generate("Security")
        data = service.get(report.report_id)

        assert set(data["sections"].keys()) == {"incidents", "integrity"}

    def test_get_unknown_raises(self):
        service = _service()

        with pytest.raises(KeyError):
            service.get("does-not-exist")


# --- Report history --------------------------------------------------


class TestReportHistory:

    def test_history_includes_every_report(self):
        service = _service()
        service.generate("Security")
        service.generate("Risk")

        assert len(service.history()) == 2

    def test_history_ordered_by_generation(self):
        service = _service()
        first = service.generate("Security")
        second = service.generate("Risk")

        history = service.history()

        assert [r.report_id for r in history] == [
            first.report_id, second.report_id,
        ]

    def test_list_reports_filters_by_type(self):
        service = _service()
        service.generate("Security")
        risk_report = service.generate("Risk")

        filtered = service.list_reports("Risk")

        assert [r.report_id for r in filtered] == [risk_report.report_id]

    def test_list_reports_unfiltered_matches_history(self):
        service = _service()
        service.generate("Security")
        service.generate("Risk")

        assert service.list_reports() == service.history()


# --- JSON export -----------------------------------------------------


class TestJsonExport:

    def test_export_json_returns_valid_json(self):
        service = _service()
        report = service.generate("Security")

        exported = service.export_json(report.report_id)
        parsed = json.loads(exported)

        assert parsed["report_id"] == report.report_id
        assert parsed["report_type"] == "Security"
        assert "sections" in parsed

    def test_export_json_unknown_raises(self):
        service = _service()

        with pytest.raises(KeyError):
            service.export_json("does-not-exist")

    def test_publishes_report_exported_json(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("report_exported", events.append)
        service = _service(event_bus=bus)
        report = service.generate("Security")

        service.export_json(report.report_id)

        assert len(events) == 1
        assert events[0].payload["format"] == "json"


# --- CSV export -----------------------------------------------------


class TestCsvExport:

    def test_export_csv_has_header_row(self):
        service = _service()
        report = service.generate("Security")

        exported = service.export_csv(report.report_id)
        rows = list(csv.reader(io.StringIO(exported)))

        assert rows[0] == ["section", "key", "value"]

    def test_export_csv_includes_metadata_rows(self):
        service = _service()
        report = service.generate("Security")

        exported = service.export_csv(report.report_id)
        rows = list(csv.reader(io.StringIO(exported)))

        metadata_rows = [r for r in rows if r[0] == "metadata"]

        assert {r[1] for r in metadata_rows} == {
            "report_id", "generated_at", "report_type",
        }

    def test_export_csv_includes_section_rows(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security")
        service = _service(compliance_engine=compliance)
        report = service.generate("Compliance")

        exported = service.export_csv(report.report_id)
        rows = list(csv.reader(io.StringIO(exported)))

        compliance_rows = [r for r in rows if r[0] == "compliance"]

        assert any(r[1] == "total_policies" for r in compliance_rows)

    def test_export_csv_unknown_raises(self):
        service = _service()

        with pytest.raises(KeyError):
            service.export_csv("does-not-exist")

    def test_publishes_report_exported_csv(self):
        bus = GovernanceEventBus()
        events = []
        bus.subscribe("report_exported", events.append)
        service = _service(event_bus=bus)
        report = service.generate("Security")

        service.export_csv(report.report_id)

        assert len(events) == 1
        assert events[0].payload["format"] == "csv"


# --- Summary generation --------------------------------------------------


class TestSummaryGeneration:

    def test_summary_of_unwired_service(self):
        service = _service()

        summary = service.summary()

        assert summary.total_deployments == 0
        assert summary.compliance_rate == 0.0
        assert summary.incident_count == 0

    def test_summary_reflects_compliance_engine(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security")
        compliance.evaluate("d1")
        compliance.evaluate("d2")

        service = _service(compliance_engine=compliance)

        summary = service.summary()

        assert summary.total_deployments == 2
        assert summary.compliance_rate == 1.0

    def test_summary_reflects_partial_compliance(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        def _fails(policy, context):
            return False, "nope"

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security", evaluator=_fails)
        compliance.evaluate("d1")

        service = _service(compliance_engine=compliance)

        assert service.summary().compliance_rate == 0.0

    def test_summary_reflects_incident_engine(self):
        from backend.observability.deployment_governance_incident_response import (  # noqa: E501
            DeploymentIncidentResponseEngine,
        )

        incidents = DeploymentIncidentResponseEngine(clock=_clock)
        incidents.create("s1", "LOW")
        incidents.create("s2", "HIGH")

        service = _service(incident_response_engine=incidents)

        assert service.summary().incident_count == 2


# --- Compliance engine integration (this commit's Update file) ------------


class TestComplianceEngineIntegration:

    def test_evaluated_deployments(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security")
        compliance.evaluate("d2")
        compliance.evaluate("d1")

        assert compliance.evaluated_deployments() == ("d1", "d2")

    def test_compliance_rate_empty(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        compliance = DeploymentComplianceEngine(clock=_clock)

        assert compliance.compliance_rate() == 0.0

    def test_compliance_rate_mixed(self):
        from backend.observability.deployment_governance_compliance import (  # noqa: E501
            DeploymentComplianceEngine,
        )

        def _fails(policy, context):
            return False, "nope"

        compliance = DeploymentComplianceEngine(clock=_clock)
        compliance.register("p1", "Security", evaluator=_fails)
        compliance.evaluate("d1")

        compliance.remove("p1")
        compliance.register("p2", "Security")
        compliance.evaluate("d2")

        assert compliance.compliance_rate() == 0.5


# --- Audit trail service integration (this commit's Update file) ---------


class TestAuditTrailIntegration:

    def test_summary_counts_events(self):
        from backend.observability.deployment_governance_audit import (
            GovernanceAuditService,
        )
        from backend.observability.deployment_governance_audit_trail import (  # noqa: E501
            DeploymentAuditService,
        )

        underlying = GovernanceAuditService(clock=_clock)
        audit_service = DeploymentAuditService(audit_service=underlying)
        audit_service.record(actor="alice", action="deploy", resource="d1")

        assert audit_service.summary().total_events == 1


# --- Clear -------------------------------------------------------------


class TestClear:

    def test_clear_removes_every_report(self):
        service = _service()
        service.generate("Security")

        service.clear()

        assert service.history() == ()


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_reporting_service_returns_same_instance(self):
        assert get_reporting_service() is get_reporting_service()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceReportingApi:

    def test_post_generate(self, client):
        response = client.post(
            "/governance/security/reports/generate",
            params={"report_type": "Security"},
        )

        assert response.status_code == 200
        assert response.json()["report_type"] == "Security"

    def test_get_reports_list(self, client):
        create_response = client.post(
            "/governance/security/reports/generate",
            params={"report_type": "Risk"},
        )

        report_id = create_response.json()["report_id"]

        response = client.get("/governance/security/reports")

        assert response.status_code == 200
        assert any(r["report_id"] == report_id for r in response.json())

    def test_get_report_by_id(self, client):
        create_response = client.post(
            "/governance/security/reports/generate",
            params={"report_type": "Security"},
        )

        report_id = create_response.json()["report_id"]

        response = client.get(
            f"/governance/security/reports/{report_id}"
        )

        assert response.status_code == 200
        assert response.json()["report_id"] == report_id

    def test_get_report_unknown_returns_404(self, client):
        response = client.get(
            "/governance/security/reports/does-not-exist"
        )

        assert response.status_code == 404

    def test_post_export_json(self, client):
        create_response = client.post(
            "/governance/security/reports/generate",
            params={"report_type": "Security"},
        )

        report_id = create_response.json()["report_id"]

        response = client.post(
            f"/governance/security/reports/{report_id}/export",
            params={"format": "json"},
        )

        assert response.status_code == 200
        assert response.json()["format"] == "json"
        assert json.loads(response.json()["data"])["report_id"] == (
            report_id
        )

    def test_post_export_csv(self, client):
        create_response = client.post(
            "/governance/security/reports/generate",
            params={"report_type": "Security"},
        )

        report_id = create_response.json()["report_id"]

        response = client.post(
            f"/governance/security/reports/{report_id}/export",
            params={"format": "csv"},
        )

        assert response.status_code == 200
        assert response.json()["format"] == "csv"

    def test_post_export_unknown_returns_404(self, client):
        response = client.post(
            "/governance/security/reports/does-not-exist/export",
        )

        assert response.status_code == 404
