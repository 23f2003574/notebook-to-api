from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .audit_logs import AuditEvent, AuditLogService, AuditQuery, get_audit_log_service

_DEFAULT_BUCKET_SECONDS = 86400.0

_AUTH_EVENT_TYPE = "Authentication"
_AUTHZ_EVENT_TYPE = "Authorization"
_SESSION_EVENT_TYPE = "Session"
_CONFIG_EVENT_TYPE = "Configuration"
_SESSION_CREATE_ACTION = "create"
_SESSION_TERMINATE_ACTION = "terminate"
_SECRET_ROTATE_ACTION = "rotate"


@dataclass(frozen=True)
class SecurityMetrics:
    """An aggregated snapshot of security posture over an optional time window."""

    authentication_success_rate: Optional[float] = None
    authentication_failures: int = 0
    permission_denials: int = 0
    active_sessions: int = 0
    secret_rotations: int = 0
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "authentication_success_rate": self.authentication_success_rate,
            "authentication_failures": self.authentication_failures,
            "permission_denials": self.permission_denials,
            "active_sessions": self.active_sessions,
            "secret_rotations": self.secret_rotations,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
        }


@dataclass(frozen=True)
class SecurityTrend:
    """One bucketed period of security activity within a trend series."""

    period_start: datetime
    period_end: datetime
    authentication_failures: int = 0
    permission_denials: int = 0
    secret_rotations: int = 0

    def to_dict(self) -> dict:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "authentication_failures": self.authentication_failures,
            "permission_denials": self.permission_denials,
            "secret_rotations": self.secret_rotations,
        }


class SecurityAnalyticsService:
    """Aggregates the audit trail into metrics, trends, and export-ready reports."""

    def __init__(self, audit_log: Optional[AuditLogService] = None) -> None:
        self._audit_log = audit_log or get_audit_log_service()

    def record(
        self,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        *,
        outcome: str = "success",
        details: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> AuditEvent:
        return self._audit_log.record(
            event_type, actor, resource, action, outcome=outcome, details=details, timestamp=timestamp
        )

    def summary(
        self,
        *,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> SecurityMetrics:
        auth_events = self._audit_log.query(
            AuditQuery(event_type=_AUTH_EVENT_TYPE, since=window_start, until=window_end)
        )
        auth_successes = sum(1 for event in auth_events if event.outcome == "success")
        auth_failures = len(auth_events) - auth_successes
        success_rate = auth_successes / len(auth_events) if auth_events else None

        authz_events = self._audit_log.query(
            AuditQuery(event_type=_AUTHZ_EVENT_TYPE, since=window_start, until=window_end)
        )
        permission_denials = sum(1 for event in authz_events if event.outcome != "success")

        sessions_created = self._audit_log.count(
            AuditQuery(event_type=_SESSION_EVENT_TYPE, action=_SESSION_CREATE_ACTION, until=window_end)
        )
        sessions_terminated = self._audit_log.count(
            AuditQuery(event_type=_SESSION_EVENT_TYPE, action=_SESSION_TERMINATE_ACTION, until=window_end)
        )
        active_sessions = max(sessions_created - sessions_terminated, 0)

        secret_rotations = self._audit_log.count(
            AuditQuery(
                event_type=_CONFIG_EVENT_TYPE,
                action=_SECRET_ROTATE_ACTION,
                since=window_start,
                until=window_end,
            )
        )

        return SecurityMetrics(
            authentication_success_rate=success_rate,
            authentication_failures=auth_failures,
            permission_denials=permission_denials,
            active_sessions=active_sessions,
            secret_rotations=secret_rotations,
            window_start=window_start,
            window_end=window_end,
        )

    def trends(self, *, bucket_seconds: float = _DEFAULT_BUCKET_SECONDS) -> list:
        if bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be positive")

        events = self._audit_log.query()
        if not events:
            return []

        start = min(event.timestamp for event in events)
        end = max(event.timestamp for event in events)
        delta = timedelta(seconds=bucket_seconds)

        trends = []
        bucket_start = start
        while bucket_start <= end:
            bucket_end = bucket_start + delta
            bucket_events = [event for event in events if bucket_start <= event.timestamp < bucket_end]
            trends.append(
                SecurityTrend(
                    period_start=bucket_start,
                    period_end=bucket_end,
                    authentication_failures=sum(
                        1
                        for event in bucket_events
                        if event.event_type == _AUTH_EVENT_TYPE and event.outcome != "success"
                    ),
                    permission_denials=sum(
                        1
                        for event in bucket_events
                        if event.event_type == _AUTHZ_EVENT_TYPE and event.outcome != "success"
                    ),
                    secret_rotations=sum(
                        1
                        for event in bucket_events
                        if event.event_type == _CONFIG_EVENT_TYPE
                        and event.action == _SECRET_ROTATE_ACTION
                    ),
                )
            )
            bucket_start = bucket_end
        return trends

    def recent_events(self, event_type: Optional[str] = None, *, limit: int = 10) -> list:
        events = self._audit_log.query(AuditQuery(event_type=event_type))
        return sorted(events, key=lambda event: event.timestamp, reverse=True)[:limit]

    def export(
        self,
        *,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> dict:
        return {
            "summary": self.summary(window_start=window_start, window_end=window_end).to_dict(),
            "trends": [trend.to_dict() for trend in self.trends()],
        }


_security_analytics_service = SecurityAnalyticsService()


def get_security_analytics_service() -> SecurityAnalyticsService:
    return _security_analytics_service


router = APIRouter(prefix="/security", tags=["security-analytics"])


def _parse_window(window_start: Optional[str], window_end: Optional[str]) -> tuple:
    try:
        parsed_start = datetime.fromisoformat(window_start) if window_start else None
        parsed_end = datetime.fromisoformat(window_end) if window_end else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return parsed_start, parsed_end


@router.get("/analytics")
def export_analytics_endpoint(
    window_start: Optional[str] = Query(default=None),
    window_end: Optional[str] = Query(default=None),
) -> dict:
    parsed_start, parsed_end = _parse_window(window_start, window_end)
    return get_security_analytics_service().export(window_start=parsed_start, window_end=parsed_end)


@router.get("/analytics/summary")
def analytics_summary_endpoint(
    window_start: Optional[str] = Query(default=None),
    window_end: Optional[str] = Query(default=None),
) -> dict:
    parsed_start, parsed_end = _parse_window(window_start, window_end)
    metrics = get_security_analytics_service().summary(window_start=parsed_start, window_end=parsed_end)
    return metrics.to_dict()


@router.get("/analytics/trends")
def analytics_trends_endpoint(bucket_seconds: float = Query(default=_DEFAULT_BUCKET_SECONDS)) -> list:
    try:
        trends = get_security_analytics_service().trends(bucket_seconds=bucket_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [trend.to_dict() for trend in trends]
