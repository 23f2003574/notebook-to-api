from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_approval import DeploymentApprovalEngine
    from .deployment_governance_artifact_integrity import (
        DeploymentIntegrityVerifier,
    )
    from .deployment_governance_audit_trail import DeploymentAuditService
    from .deployment_governance_authentication import (
        DeploymentAuthenticationManager,
    )
    from .deployment_governance_compliance import (
        DeploymentComplianceEngine,
    )
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_incident_response import (
        DeploymentIncidentResponseEngine,
    )
    from .deployment_governance_rbac import DeploymentRBACEngine
    from .deployment_governance_reporting import DeploymentReportingService
    from .deployment_governance_risk import DeploymentRiskEngine
    from .deployment_governance_secret_vault import DeploymentSecretVault
    from .deployment_governance_security_scanner import (
        DeploymentSecurityScanner,
    )

RISK_LEVELS: "tuple[str, ...]" = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

SECTION_STATUSES: "tuple[str, ...]" = ("OK", "DEGRADED", "UNAVAILABLE")

# The ten sections this dashboard aggregates, in the fixed order every
# section-producing method (sections(), security(), compliance(),
# risk(), audit(), incidents()) presents them in — "deterministic
# section ordering". Each maps to exactly one of this dashboard's
# eleven Data Sources except "Reporting Service", which — unlike the
# other ten — has no section of its own: it instead supplies
# overview()'s own headline numbers directly (see _build_overview),
# preferred over re-deriving them from each individual engine.
DASHBOARD_SECTION_NAMES: "tuple[str, ...]" = (
    "Authentication",
    "Authorization",
    "Secrets",
    "Approvals",
    "Audit",
    "Compliance",
    "Risk",
    "Security Scans",
    "Integrity",
    "Incidents",
)

_SECURITY_SECTION_NAMES: "frozenset[str]" = frozenset(
    {
        "Authentication", "Authorization", "Secrets", "Approvals",
        "Security Scans", "Integrity",
    }
)

_MEDIUM_COMPLIANCE_THRESHOLD = 1.0


@dataclass(frozen=True)
class SecurityDashboard:
    """
    An immutable, point-in-time headline snapshot across the whole
    deployment security subsystem.
    """

    generated_at: datetime

    active_incidents: int

    compliance_score: float

    risk_level: str

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")

        if self.active_incidents < 0:
            raise ValueError("active_incidents must not be negative")

        if not 0.0 <= self.compliance_score <= 1.0:
            raise ValueError(
                "compliance_score must be between 0.0 and 1.0"
            )

        if self.risk_level not in RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {RISK_LEVELS}")

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "active_incidents": self.active_incidents,
            "compliance_score": self.compliance_score,
            "risk_level": self.risk_level,
        }


@dataclass(frozen=True)
class DashboardSection:
    """
    One immutable, point-in-time status snapshot for one of
    DASHBOARD_SECTION_NAMES.
    """

    name: str

    status: str

    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if self.status not in SECTION_STATUSES:
            raise ValueError(f"status must be one of {SECTION_STATUSES}")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "updated_at": self.updated_at.isoformat(),
        }


class DeploymentSecurityDashboard:
    """
    A read-only aggregation service sitting above every other
    deployment security subsystem built so far (RBAC Engine,
    Authentication Manager, Secret Vault, Approval Engine, Audit
    Service, Compliance Engine, Risk Engine, Security Scanner,
    Integrity Verifier, Incident Response Engine, and the Reporting
    Service) — the same shape DeploymentRolloutDashboard already
    established for the rollout subsystem: every method here only
    ever calls an already-public, already-read-only accessor
    (list()/summary()/history()/...) on one of those and combines the
    results. It never registers, assesses, evaluates, scans, or
    otherwise mutates anything, and every constructor dependency is
    optional — a component that was not wired simply contributes its
    "UNAVAILABLE" section status (or, for overview()'s own headline
    numbers, its documented zero-value default) instead of raising —
    "graceful handling of unavailable services". This commit only
    builds the dashboard and its API; registering it with the runtime
    is the final bootstrap commit's job.

    Like DeploymentRolloutDashboard, overview() caches its result for
    cache_ttl_seconds (0, the default, disables caching); refresh()
    always rebuilds and re-caches. The ten section-producing methods
    (sections()/security()/compliance()/risk()/audit()/incidents())
    are always fresh, uncached reads — only overview()'s aggregation
    is ever cached.

    Thread-safe: the cached snapshot is guarded by an internal lock;
    nothing else here holds mutable state of its own beyond that.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        rbac_engine: "DeploymentRBACEngine | None" = None,
        authentication_manager: (
            "DeploymentAuthenticationManager | None"
        ) = None,
        secret_vault: "DeploymentSecretVault | None" = None,
        approval_engine: "DeploymentApprovalEngine | None" = None,
        audit_service: "DeploymentAuditService | None" = None,
        compliance_engine: "DeploymentComplianceEngine | None" = None,
        risk_engine: "DeploymentRiskEngine | None" = None,
        security_scanner: "DeploymentSecurityScanner | None" = None,
        integrity_verifier: "DeploymentIntegrityVerifier | None" = None,
        incident_response_engine: (
            "DeploymentIncidentResponseEngine | None"
        ) = None,
        reporting_service: "DeploymentReportingService | None" = None,
        cache_ttl_seconds: float = 0.0,
    ) -> None:
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must not be negative")

        self._lock = threading.Lock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._rbac_engine = rbac_engine

        self._authentication_manager = authentication_manager

        self._secret_vault = secret_vault

        self._approval_engine = approval_engine

        self._audit_service = audit_service

        self._compliance_engine = compliance_engine

        self._risk_engine = risk_engine

        self._security_scanner = security_scanner

        self._integrity_verifier = integrity_verifier

        self._incident_response_engine = incident_response_engine

        self._reporting_service = reporting_service

        self._cache_ttl_seconds = cache_ttl_seconds

        self._cached: "SecurityDashboard | None" = None

        self._cached_at: "datetime | None" = None

    def overview(self) -> SecurityDashboard:
        """
        Return the full cross-subsystem headline snapshot, serving a
        cached copy if one was built within cache_ttl_seconds — a
        cache hit publishes nothing (it is not a new "generation").
        """

        with self._lock:
            cached = self._cached
            cached_at = self._cached_at

        if (
            cached is not None
            and cached_at is not None
            and self._cache_ttl_seconds > 0
            and (self._clock() - cached_at).total_seconds()
            < self._cache_ttl_seconds
        ):
            return cached

        return self._build_overview(
            event_type="security_dashboard_generated"
        )

    def refresh(self) -> SecurityDashboard:
        """
        Rebuild the headline snapshot unconditionally, bypassing (and
        replacing) any cached copy, publishing "security_dashboard_
        refreshed" instead of "security_dashboard_generated".
        """

        return self._build_overview(
            event_type="security_dashboard_refreshed"
        )

    def sections(self) -> "tuple[DashboardSection, ...]":
        """
        Return every DASHBOARD_SECTION_NAMES section's current status,
        in that fixed order. Always a fresh read, independent of
        overview()'s cache.
        """

        now = self._clock()

        return tuple(
            DashboardSection(
                name=name, status=self._section_status(name),
                updated_at=now,
            )
            for name in DASHBOARD_SECTION_NAMES
        )

    def security(self) -> "tuple[DashboardSection, ...]":
        """
        Return the Authentication, Authorization, Secrets, Approvals,
        Security Scans, and Integrity sections.
        """

        return tuple(
            section
            for section in self.sections()
            if section.name in _SECURITY_SECTION_NAMES
        )

    def compliance(self) -> "tuple[DashboardSection, ...]":
        """
        Return the Compliance section.
        """

        return self._filter_sections("Compliance")

    def risk(self) -> "tuple[DashboardSection, ...]":
        """
        Return the Risk section.
        """

        return self._filter_sections("Risk")

    def audit(self) -> "tuple[DashboardSection, ...]":
        """
        Return the Audit section.
        """

        return self._filter_sections("Audit")

    def incidents(self) -> "tuple[DashboardSection, ...]":
        """
        Return the Incidents section.
        """

        return self._filter_sections("Incidents")

    def _filter_sections(
        self, name: str
    ) -> "tuple[DashboardSection, ...]":
        return tuple(
            section for section in self.sections() if section.name == name
        )

    def _build_overview(self, *, event_type: str) -> SecurityDashboard:
        now = self._clock()

        if self._reporting_service is not None:
            report_summary = self._reporting_service.summary()
            active_incidents = report_summary.incident_count
            compliance_score = report_summary.compliance_rate
            compliance_measured = report_summary.total_deployments > 0

        else:
            active_incidents = (
                self._incident_response_engine.summary().open_incidents
                if self._incident_response_engine is not None
                else 0
            )

            compliance_score = (
                self._compliance_engine.compliance_rate()
                if self._compliance_engine is not None
                else 0.0
            )

            compliance_measured = bool(
                self._compliance_engine is not None
                and self._compliance_engine.evaluated_deployments()
            )

        has_critical_incident = False

        if self._incident_response_engine is not None:
            has_critical_incident = any(
                incident.severity == "CRITICAL"
                and incident.status != "RESOLVED"
                for incident in self._incident_response_engine.history()
            )

        risk_level = self._compute_risk_level(
            active_incidents=active_incidents,
            has_critical_incident=has_critical_incident,
            compliance_score=compliance_score,
            compliance_measured=compliance_measured,
        )

        dashboard = SecurityDashboard(
            generated_at=now, active_incidents=active_incidents,
            compliance_score=compliance_score, risk_level=risk_level,
        )

        with self._lock:
            self._cached = dashboard
            self._cached_at = now

        self._publish(event_type, dashboard)

        return dashboard

    def _compute_risk_level(
        self,
        *,
        active_incidents: int,
        has_critical_incident: bool,
        compliance_score: float,
        compliance_measured: bool,
    ) -> str:
        if has_critical_incident:
            return "CRITICAL"

        if active_incidents > 0:
            return "HIGH"

        if (
            compliance_measured
            and compliance_score < _MEDIUM_COMPLIANCE_THRESHOLD
        ):
            return "MEDIUM"

        return "LOW"

    def _section_status(self, name: str) -> str:
        if name == "Authentication":
            return (
                "UNAVAILABLE"
                if self._authentication_manager is None
                else "OK"
            )

        if name == "Authorization":
            return "UNAVAILABLE" if self._rbac_engine is None else "OK"

        if name == "Secrets":
            return "UNAVAILABLE" if self._secret_vault is None else "OK"

        if name == "Approvals":
            if self._approval_engine is None:
                return "UNAVAILABLE"

            return (
                "DEGRADED"
                if self._approval_engine.list_pending()
                else "OK"
            )

        if name == "Audit":
            return "UNAVAILABLE" if self._audit_service is None else "OK"

        if name == "Compliance":
            if self._compliance_engine is None:
                return "UNAVAILABLE"

            evaluated = self._compliance_engine.evaluated_deployments()

            if evaluated and (
                self._compliance_engine.compliance_rate() < 1.0
            ):
                return "DEGRADED"

            return "OK"

        if name == "Risk":
            return "UNAVAILABLE" if self._risk_engine is None else "OK"

        if name == "Security Scans":
            if self._security_scanner is None:
                return "UNAVAILABLE"

            return (
                "DEGRADED"
                if self._security_scanner.summary().critical_findings > 0
                else "OK"
            )

        if name == "Integrity":
            if self._integrity_verifier is None:
                return "UNAVAILABLE"

            return (
                "DEGRADED"
                if (
                    self._integrity_verifier.summary()
                    .failed_verifications
                    > 0
                )
                else "OK"
            )

        if name == "Incidents":
            if self._incident_response_engine is None:
                return "UNAVAILABLE"

            return (
                "DEGRADED"
                if (
                    self._incident_response_engine.summary()
                    .open_incidents
                    > 0
                )
                else "OK"
            )

        return "UNAVAILABLE"

    def _publish(
        self, event_type: str, dashboard: SecurityDashboard
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="security-dashboard",
            payload=dashboard.to_dict(),
        )


def build_default_governance_security_dashboard() -> (
    DeploymentSecurityDashboard
):
    """
    Build the process-wide security dashboard, wired to the
    process-wide governance event bus and every one of its eleven
    Data Sources.

    Not wired into the running FastAPI app's routing beyond its own
    dedicated endpoints — general registration with the runtime is
    the final bootstrap commit's job, matching this commit's own
    stated scope.
    """

    from .deployment_governance_approval import get_approval_engine
    from .deployment_governance_artifact_integrity import (
        get_artifact_integrity_verifier,
    )
    from .deployment_governance_audit_trail import get_audit_trail_service
    from .deployment_governance_authentication import (
        get_authentication_manager,
    )
    from .deployment_governance_compliance import get_compliance_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_incident_response import (
        get_incident_response_engine,
    )
    from .deployment_governance_rbac import get_rbac_engine
    from .deployment_governance_reporting import get_reporting_service
    from .deployment_governance_risk import get_risk_engine
    from .deployment_governance_secret_vault import get_secret_vault
    from .deployment_governance_security_scanner import (
        get_security_scanner,
    )

    return DeploymentSecurityDashboard(
        event_bus=get_event_bus(),
        rbac_engine=get_rbac_engine(),
        authentication_manager=get_authentication_manager(),
        secret_vault=get_secret_vault(),
        approval_engine=get_approval_engine(),
        audit_service=get_audit_trail_service(),
        compliance_engine=get_compliance_engine(),
        risk_engine=get_risk_engine(),
        security_scanner=get_security_scanner(),
        integrity_verifier=get_artifact_integrity_verifier(),
        incident_response_engine=get_incident_response_engine(),
        reporting_service=get_reporting_service(),
    )


# Shared for the lifetime of the process, matching every other
# dashboard/aggregation singleton in this codebase — mainly so its
# cache (when cache_ttl_seconds > 0) is actually shared across
# requests instead of being pointlessly rebuilt fresh, empty, every
# time.
_security_dashboard = build_default_governance_security_dashboard()


def get_security_dashboard() -> DeploymentSecurityDashboard:
    """
    Return the process-wide security dashboard.
    """

    return _security_dashboard
