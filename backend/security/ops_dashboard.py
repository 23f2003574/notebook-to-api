from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .audit_logger import SecurityAuditLogger, get_security_audit_logger
from .identity_registry import IdentityRegistry, get_identity_registry
from .security_analytics import SecurityAnalyticsService as LegacySecurityAnalyticsService, get_security_analytics_service
from .security_metrics import SecurityAnalyticsService, get_security_analytics_service as get_security_metrics_service


class SecurityDashboardAPI:
    """Read-only, unified view over identities, audit events, and security analytics."""

    def __init__(
        self,
        identity_registry: Optional[IdentityRegistry] = None,
        audit_logger: Optional[SecurityAuditLogger] = None,
        analytics_service: Optional[SecurityAnalyticsService] = None,
        legacy_analytics_service: Optional[LegacySecurityAnalyticsService] = None,
    ) -> None:
        self._identity_registry = identity_registry or get_identity_registry()
        self._audit_logger = audit_logger or get_security_audit_logger()
        self._analytics_service = analytics_service or get_security_metrics_service()
        self._legacy_analytics_service = legacy_analytics_service or get_security_analytics_service()

    def identities(self) -> dict:
        identities = self._identity_registry.list_identities()
        by_identity_type: dict[str, int] = {}
        for identity in identities:
            identity_type = identity.metadata.identity_type
            by_identity_type[identity_type] = by_identity_type.get(identity_type, 0) + 1

        metrics = self._analytics_service.summary()
        return {
            "total": len(identities),
            "by_identity_type": by_identity_type,
            "login_success_rate": metrics.login_success_rate,
            "failed_auth_attempts": metrics.failed_auth_attempts,
        }

    def audits(self, *, limit: int = 20) -> dict:
        events = self._audit_logger.query()
        by_severity: dict[str, int] = {}
        for event in events:
            by_severity[event.severity] = by_severity.get(event.severity, 0) + 1
        recent = sorted(events, key=lambda event: event.timestamp, reverse=True)[:limit]

        return {
            "total": len(events),
            "by_severity": by_severity,
            "recent_events": [event.to_dict() for event in recent],
        }

    def analytics(self) -> dict:
        return {
            "metrics": self._analytics_service.summary().to_dict(),
            "risk_score": self._analytics_service.risk_score(),
            "legacy_risk_indicators": self._legacy_analytics_service.risk_indicators(),
        }

    def overview(self) -> dict:
        return {
            "identities": self.identities(),
            "audit": self.audits(),
            "analytics": self.analytics(),
        }


_security_dashboard_api = SecurityDashboardAPI()


def get_security_dashboard_api() -> SecurityDashboardAPI:
    return _security_dashboard_api


router = APIRouter(prefix="/security/dashboard", tags=["security-ops-dashboard"])


@router.get("")
def dashboard_overview_endpoint() -> dict:
    return get_security_dashboard_api().overview()


@router.get("/authentication")
def dashboard_authentication_endpoint() -> dict:
    return get_security_dashboard_api().identities()


@router.get("/audit")
def dashboard_audit_endpoint(limit: int = Query(default=20)) -> dict:
    return get_security_dashboard_api().audits(limit=limit)


@router.get("/analytics")
def dashboard_analytics_endpoint() -> dict:
    return get_security_dashboard_api().analytics()
