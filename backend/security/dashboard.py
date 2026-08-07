from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter

from .audit_logs import AuditLogService, AuditQuery, get_audit_log_service
from .security_analytics import SecurityAnalyticsService, get_security_analytics_service


def _generated_at(timestamp: Optional[datetime]) -> str:
    return (timestamp or datetime.now(timezone.utc)).isoformat()


class SecurityDashboardAPI:
    """Read-only aggregation of authentication, authorization, session, and analytics data."""

    def __init__(
        self,
        audit_log: Optional[AuditLogService] = None,
        analytics_service: Optional[SecurityAnalyticsService] = None,
    ) -> None:
        self._audit_log = audit_log or get_audit_log_service()
        self._analytics_service = analytics_service or get_security_analytics_service()

    def authentication(self, *, timestamp: Optional[datetime] = None) -> dict:
        events = self._audit_log.query(AuditQuery(event_type="Authentication"))
        successes = sum(1 for event in events if event.outcome == "success")

        return {
            "total_events": len(events),
            "successes": successes,
            "failures": len(events) - successes,
            "success_rate": self._analytics_service.summary().authentication_success_rate,
            "recent_events": [
                event.to_dict() for event in self._analytics_service.recent_events("Authentication")
            ],
            "generated_at": _generated_at(timestamp),
        }

    def authorization(self, *, timestamp: Optional[datetime] = None) -> dict:
        events = self._audit_log.query(AuditQuery(event_type="Authorization"))
        denials = sum(1 for event in events if event.outcome != "success")

        return {
            "total_events": len(events),
            "denials": denials,
            "recent_events": [
                event.to_dict() for event in self._analytics_service.recent_events("Authorization")
            ],
            "generated_at": _generated_at(timestamp),
        }

    def sessions(self, *, timestamp: Optional[datetime] = None) -> dict:
        created = self._audit_log.count(AuditQuery(event_type="Session", action="create"))
        terminated = self._audit_log.count(AuditQuery(event_type="Session", action="terminate"))

        return {
            "total_created": created,
            "total_terminated": terminated,
            "active_sessions": max(created - terminated, 0),
            "recent_events": [
                event.to_dict() for event in self._analytics_service.recent_events("Session")
            ],
            "generated_at": _generated_at(timestamp),
        }

    def analytics(self, *, timestamp: Optional[datetime] = None) -> dict:
        return {**self._analytics_service.export(), "generated_at": _generated_at(timestamp)}

    def overview(self) -> dict:
        return {
            "authentication": self.authentication(),
            "authorization": self.authorization(),
            "sessions": self.sessions(),
            "analytics": self.analytics(),
        }

    def manifest(self, *, timestamp: Optional[datetime] = None) -> dict:
        return {
            "datasets": ["authentication", "authorization", "sessions", "analytics"],
            "generated_at": _generated_at(timestamp),
        }


_security_dashboard_api = SecurityDashboardAPI()


def get_security_dashboard_api() -> SecurityDashboardAPI:
    return _security_dashboard_api


router = APIRouter(prefix="/security", tags=["security-dashboard"])


@router.get("/dashboard")
def get_dashboard_overview() -> dict:
    return get_security_dashboard_api().overview()


@router.get("/dashboard/authentication")
def get_dashboard_authentication() -> dict:
    return get_security_dashboard_api().authentication()


@router.get("/dashboard/authorization")
def get_dashboard_authorization() -> dict:
    return get_security_dashboard_api().authorization()


@router.get("/dashboard/sessions")
def get_dashboard_sessions() -> dict:
    return get_security_dashboard_api().sessions()


@router.get("/dashboard/analytics")
def get_dashboard_analytics() -> dict:
    return get_security_dashboard_api().analytics()
