from __future__ import annotations

import csv
import io
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_artifact_integrity import (
        DeploymentIntegrityVerifier,
    )
    from .deployment_governance_audit_trail import DeploymentAuditService
    from .deployment_governance_compliance import (
        DeploymentComplianceEngine,
    )
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_incident_response import (
        DeploymentIncidentResponseEngine,
    )
    from .deployment_governance_risk import DeploymentRiskEngine

# The report types this service can generate, and the fixed, ordered
# set of sections each one assembles — "deterministic section
# ordering": the tuple order below is the order sections are computed
# and serialized in, every time.
REPORT_TYPES: "tuple[str, ...]" = (
    "Security",
    "Compliance",
    "Audit",
    "Risk",
    "Deployment Summary",
)

_REPORT_SECTIONS: "dict[str, tuple[str, ...]]" = {
    "Security": ("incidents", "integrity"),
    "Compliance": ("compliance",),
    "Audit": ("audit",),
    "Risk": ("risk",),
    "Deployment Summary": (
        "compliance", "risk", "incidents", "integrity", "audit",
    ),
}

EXPORT_FORMATS: "tuple[str, ...]" = ("json", "csv")


@dataclass(frozen=True)
class GovernanceReport:
    """
    One immutable, point-in-time report's identity — report_id,
    when it was generated, and which REPORT_TYPES it is. The
    aggregated section data itself lives alongside this record
    (see DeploymentReportingService.get()/export_json()/export_csv()),
    not on this dataclass — "reports are immutable" is what makes
    that separate storage safe: nothing about a report, including its
    sections, is ever mutated after generate() returns.
    """

    report_id: str

    generated_at: datetime

    report_type: str

    def __post_init__(self) -> None:
        if not self.report_id:
            raise ValueError("report_id must not be empty")

        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")

        if self.report_type not in REPORT_TYPES:
            raise ValueError(f"report_type must be one of {REPORT_TYPES}")

    def to_dict(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at.isoformat(),
            "report_type": self.report_type,
        }


@dataclass(frozen=True)
class ReportSummary:
    """
    An immutable, cross-subsystem, point-in-time summary — not tied to
    any one generated report.
    """

    total_deployments: int

    compliance_rate: float

    incident_count: int

    def __post_init__(self) -> None:
        if self.total_deployments < 0:
            raise ValueError("total_deployments must not be negative")

        if not 0.0 <= self.compliance_rate <= 1.0:
            raise ValueError(
                "compliance_rate must be between 0.0 and 1.0"
            )

        if self.incident_count < 0:
            raise ValueError("incident_count must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "total_deployments": self.total_deployments,
            "compliance_rate": self.compliance_rate,
            "incident_count": self.incident_count,
        }


class DeploymentReportingService:
    """
    Generates consolidated governance reports by read-only aggregation
    over existing security subsystems (Audit Service, Compliance
    Engine, Risk Engine, Incident Response Engine, Integrity
    Verifier) — it never modifies deployment state, or anything in
    the subsystems it reads from; every method here only ever calls
    an already-public, already-read-only accessor
    (list()/summary()/evaluated_deployments()/...) on one of those,
    the same read-only-aggregation contract
    DeploymentRolloutDashboard's own docstring describes.

    generate() assembles a REPORT_TYPES report from a fixed, ordered
    set of sections (_REPORT_SECTIONS) — a section whose underlying
    engine is not wired is simply omitted, not an error. export_json()
    and export_csv() both render the exact same underlying report data
    (see _report_data), just formatted differently — the "reusable
    export interface" this class's own two exporters share, rather
    than each independently re-deriving the report's content.

    Thread-safe: the report and content registries are guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        audit_service: "DeploymentAuditService | None" = None,
        compliance_engine: "DeploymentComplianceEngine | None" = None,
        risk_engine: "DeploymentRiskEngine | None" = None,
        incident_response_engine: (
            "DeploymentIncidentResponseEngine | None"
        ) = None,
        integrity_verifier: "DeploymentIntegrityVerifier | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._reports: "dict[str, GovernanceReport]" = {}

        self._sections: "dict[str, dict[str, Any]]" = {}

        self._sequence: "dict[str, int]" = {}

        self._next_sequence = 1

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._audit_service = audit_service

        self._compliance_engine = compliance_engine

        self._risk_engine = risk_engine

        self._incident_response_engine = incident_response_engine

        self._integrity_verifier = integrity_verifier

    def generate(self, report_type: str) -> GovernanceReport:
        """
        Generate a new report of report_type, aggregating its fixed
        set of sections (_REPORT_SECTIONS) from whichever of this
        service's wired data sources are relevant.

        Raises ValueError if report_type is not one of REPORT_TYPES.
        """

        if report_type not in REPORT_TYPES:
            raise ValueError(f"report_type must be one of {REPORT_TYPES}")

        sections = {
            name: self._build_section(name)
            for name in _REPORT_SECTIONS[report_type]
        }

        report = GovernanceReport(
            report_id=str(uuid4()), generated_at=self._clock(),
            report_type=report_type,
        )

        with self._lock:
            self._reports[report.report_id] = report
            self._sections[report.report_id] = sections
            self._sequence[report.report_id] = self._next_sequence
            self._next_sequence += 1

        self._publish("report_generated", report.report_id, report.to_dict())

        return report

    def get(self, report_id: str) -> "dict[str, Any]":
        """
        Return report_id's full data: its GovernanceReport metadata
        plus every aggregated section — the same canonical shape
        export_json()/export_csv() render.

        Raises KeyError if report_id is not registered.
        """

        return self._report_data(report_id)

    def summary(self) -> ReportSummary:
        """
        Return a cross-subsystem summary, independent of any
        generated report: total_deployments (from
        DeploymentComplianceEngine.evaluated_deployments()),
        compliance_rate (from
        DeploymentComplianceEngine.compliance_rate()), and
        incident_count (from
        DeploymentIncidentResponseEngine.summary().total_incidents).
        Each defaults to 0/0.0 if its engine is not wired.
        """

        total_deployments = 0
        compliance_rate = 0.0

        if self._compliance_engine is not None:
            total_deployments = len(
                self._compliance_engine.evaluated_deployments()
            )
            compliance_rate = self._compliance_engine.compliance_rate()

        incident_count = 0

        if self._incident_response_engine is not None:
            incident_count = (
                self._incident_response_engine.summary().total_incidents
            )

        return ReportSummary(
            total_deployments=total_deployments,
            compliance_rate=compliance_rate,
            incident_count=incident_count,
        )

    def export_json(self, report_id: str) -> str:
        """
        Return report_id's full data (see get()) serialized as a JSON
        string, with keys sorted for deterministic output.

        Raises KeyError if report_id is not registered.
        """

        data = self._report_data(report_id)

        exported = json.dumps(data, sort_keys=True)

        self._publish(
            "report_exported", report_id,
            {"report_id": report_id, "format": "json"},
        )

        return exported

    def export_csv(self, report_id: str) -> str:
        """
        Return report_id's full data (see get()) serialized as CSV —
        one row per (section, key, value) triple, in the same
        deterministic section order generate() built it in.

        Raises KeyError if report_id is not registered.
        """

        data = self._report_data(report_id)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["section", "key", "value"])

        for key in ("report_id", "generated_at", "report_type"):
            writer.writerow(["metadata", key, data[key]])

        for section_name, section_data in data["sections"].items():
            for key, value in section_data.items():
                writer.writerow([section_name, key, value])

        exported = buffer.getvalue()

        self._publish(
            "report_exported", report_id,
            {"report_id": report_id, "format": "csv"},
        )

        return exported

    def history(self) -> "tuple[GovernanceReport, ...]":
        """
        Return every report ever generated, ordered by generation
        order.
        """

        with self._lock:
            reports = list(self._reports.values())

        return tuple(
            sorted(
                reports,
                key=lambda report: self._sequence[report.report_id],
            )
        )

    def list_reports(
        self, report_type: "str | None" = None
    ) -> "tuple[GovernanceReport, ...]":
        """
        Return every report ever generated, optionally filtered to
        report_type, ordered by generation order (same as history(),
        with an optional filter on top).
        """

        reports = self.history()

        if report_type is None:
            return reports

        return tuple(
            report for report in reports if report.report_type == report_type
        )

    def latest(
        self, report_type: "str | None" = None
    ) -> "GovernanceReport | None":
        """
        Return the most recently generated report (optionally
        filtered to report_type), or None if none has been generated
        yet. Introduced for DeploymentSecurityDashboard, which prefers
        this service's own already-aggregated summary() over
        re-deriving one from each underlying engine directly.
        """

        reports = self.list_reports(report_type)

        return reports[-1] if reports else None

    def clear(self) -> None:
        """
        Remove every generated report and its section data.
        """

        with self._lock:
            self._reports.clear()
            self._sections.clear()
            self._sequence.clear()
            self._next_sequence = 1

    def _report_data(self, report_id: str) -> "dict[str, Any]":
        with self._lock:
            report = self._reports.get(report_id)
            sections = self._sections.get(report_id)

            if report is None:
                raise KeyError(f"report '{report_id}' is not registered")

        data = dict(report.to_dict())
        data["sections"] = dict(sections or {})

        return data

    def _build_section(self, name: str) -> "dict[str, Any]":
        if name == "audit":
            if self._audit_service is None:
                return {}

            return self._audit_service.summary().to_dict()

        if name == "compliance":
            if self._compliance_engine is None:
                return {}

            return self._compliance_engine.summary().to_dict()

        if name == "risk":
            if self._risk_engine is None:
                return {}

            return self._risk_engine.summary().to_dict()

        if name == "incidents":
            if self._incident_response_engine is None:
                return {}

            return self._incident_response_engine.summary().to_dict()

        if name == "integrity":
            if self._integrity_verifier is None:
                return {}

            return self._integrity_verifier.summary().to_dict()

        return {}

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_reporting_service() -> (
    DeploymentReportingService
):
    """
    Build the process-wide deployment reporting service, wired to the
    process-wide governance event bus, audit trail service, compliance
    engine, risk engine, incident response engine, and integrity
    verifier.
    """

    from .deployment_governance_artifact_integrity import (
        get_artifact_integrity_verifier,
    )
    from .deployment_governance_audit_trail import get_audit_trail_service
    from .deployment_governance_compliance import get_compliance_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_incident_response import (
        get_incident_response_engine,
    )
    from .deployment_governance_risk import get_risk_engine

    return DeploymentReportingService(
        event_bus=get_event_bus(),
        audit_service=get_audit_trail_service(),
        compliance_engine=get_compliance_engine(),
        risk_engine=get_risk_engine(),
        incident_response_engine=get_incident_response_engine(),
        integrity_verifier=get_artifact_integrity_verifier(),
    )


# Shared for the lifetime of the process: reports generated through
# the API need to be retrievable/exportable identically by every
# caller, which a persistence runtime built fresh per request cannot
# provide on its own.
_reporting_service = build_default_governance_reporting_service()


def get_reporting_service() -> DeploymentReportingService:
    """
    Return the process-wide deployment reporting service.
    """

    return _reporting_service
