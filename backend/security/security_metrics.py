from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .audit_logger import AuditEvent, AuditFilter, SecurityAuditLogger, get_security_audit_logger

_DEFAULT_BUCKET_SECONDS = 86400.0

_AUTH_EVENT_TYPE = "Authentication"
_AUTHZ_EVENT_TYPE = "Authorization"
_API_KEY_EVENT_TYPE = "API Key"

_CRITICAL_WEIGHT = 10
_WARNING_WEIGHT = 1
_FAILED_AUTH_WEIGHT = 2
_PERMISSION_DENIAL_WEIGHT = 3
_MAX_RISK_SCORE = 100.0


@dataclass(frozen=True)
class SecurityMetrics:
    """An aggregated snapshot of security posture over an optional time window."""

    login_success_rate: Optional[float] = None
    failed_auth_attempts: int = 0
    permission_denials: int = 0
    api_key_usage: int = 0
    audit_event_count: int = 0
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "login_success_rate": self.login_success_rate,
            "failed_auth_attempts": self.failed_auth_attempts,
            "permission_denials": self.permission_denials,
            "api_key_usage": self.api_key_usage,
            "audit_event_count": self.audit_event_count,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
        }


@dataclass(frozen=True)
class SecuritySnapshot:
    """One bucketed period of security activity within a trend series."""

    period_start: datetime
    period_end: datetime
    failed_auth_attempts: int = 0
    permission_denials: int = 0
    api_key_usage: int = 0
    audit_event_count: int = 0

    def to_dict(self) -> dict:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "failed_auth_attempts": self.failed_auth_attempts,
            "permission_denials": self.permission_denials,
            "api_key_usage": self.api_key_usage,
            "audit_event_count": self.audit_event_count,
        }


class SecurityAnalyticsService:
    """Aggregates the security audit trail into KPIs, trends, and a risk score."""

    def __init__(self, audit_logger: Optional[SecurityAuditLogger] = None) -> None:
        self._audit_logger = audit_logger or get_security_audit_logger()

    def record(
        self,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        *,
        outcome: str = "success",
        severity: str = "Info",
        details: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> AuditEvent:
        return self._audit_logger.record(
            event_type,
            actor,
            resource,
            action,
            outcome=outcome,
            severity=severity,
            details=details,
            timestamp=timestamp,
        )

    def summary(
        self, *, window_start: Optional[datetime] = None, window_end: Optional[datetime] = None
    ) -> SecurityMetrics:
        auth_events = self._audit_logger.query(
            AuditFilter(event_type=_AUTH_EVENT_TYPE, since=window_start, until=window_end)
        )
        auth_successes = sum(1 for event in auth_events if event.outcome == "success")
        failed_auth_attempts = len(auth_events) - auth_successes
        login_success_rate = auth_successes / len(auth_events) if auth_events else None

        authz_events = self._audit_logger.query(
            AuditFilter(event_type=_AUTHZ_EVENT_TYPE, since=window_start, until=window_end)
        )
        permission_denials = sum(1 for event in authz_events if event.outcome != "success")

        api_key_usage = self._audit_logger.count(
            AuditFilter(event_type=_API_KEY_EVENT_TYPE, since=window_start, until=window_end)
        )

        audit_event_count = self._audit_logger.count(
            AuditFilter(since=window_start, until=window_end)
        )

        return SecurityMetrics(
            login_success_rate=login_success_rate,
            failed_auth_attempts=failed_auth_attempts,
            permission_denials=permission_denials,
            api_key_usage=api_key_usage,
            audit_event_count=audit_event_count,
            window_start=window_start,
            window_end=window_end,
        )

    def trends(self, *, bucket_seconds: float = _DEFAULT_BUCKET_SECONDS) -> list:
        if bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be positive")

        events = self._audit_logger.query()
        if not events:
            return []

        start = min(event.timestamp for event in events)
        end = max(event.timestamp for event in events)
        delta = timedelta(seconds=bucket_seconds)

        snapshots = []
        bucket_start = start
        while bucket_start <= end:
            bucket_end = bucket_start + delta
            bucket_events = [event for event in events if bucket_start <= event.timestamp < bucket_end]
            snapshots.append(
                SecuritySnapshot(
                    period_start=bucket_start,
                    period_end=bucket_end,
                    failed_auth_attempts=sum(
                        1
                        for event in bucket_events
                        if event.event_type == _AUTH_EVENT_TYPE and event.outcome != "success"
                    ),
                    permission_denials=sum(
                        1
                        for event in bucket_events
                        if event.event_type == _AUTHZ_EVENT_TYPE and event.outcome != "success"
                    ),
                    api_key_usage=sum(
                        1 for event in bucket_events if event.event_type == _API_KEY_EVENT_TYPE
                    ),
                    audit_event_count=len(bucket_events),
                )
            )
            bucket_start = bucket_end
        return snapshots

    def risk_score(
        self, *, window_start: Optional[datetime] = None, window_end: Optional[datetime] = None
    ) -> float:
        metrics = self.summary(window_start=window_start, window_end=window_end)
        critical_events = self._audit_logger.count(
            AuditFilter(severity="Critical", since=window_start, until=window_end)
        )
        warning_events = self._audit_logger.count(
            AuditFilter(severity="Warning", since=window_start, until=window_end)
        )
        score = (
            metrics.failed_auth_attempts * _FAILED_AUTH_WEIGHT
            + metrics.permission_denials * _PERMISSION_DENIAL_WEIGHT
            + warning_events * _WARNING_WEIGHT
            + critical_events * _CRITICAL_WEIGHT
        )
        return float(min(score, _MAX_RISK_SCORE))

    def export(
        self, *, window_start: Optional[datetime] = None, window_end: Optional[datetime] = None
    ) -> dict:
        return {
            "summary": self.summary(window_start=window_start, window_end=window_end).to_dict(),
            "trends": [snapshot.to_dict() for snapshot in self.trends()],
            "risk_score": self.risk_score(window_start=window_start, window_end=window_end),
        }


_security_analytics_service = SecurityAnalyticsService()


def get_security_analytics_service() -> SecurityAnalyticsService:
    return _security_analytics_service


router = APIRouter(prefix="/security/analytics", tags=["security-metrics"])


def _parse_window(window_start: Optional[str], window_end: Optional[str]) -> tuple:
    try:
        parsed_start = datetime.fromisoformat(window_start) if window_start else None
        parsed_end = datetime.fromisoformat(window_end) if window_end else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return parsed_start, parsed_end


@router.get("")
def analytics_export_endpoint(
    window_start: Optional[str] = Query(default=None),
    window_end: Optional[str] = Query(default=None),
) -> dict:
    parsed_start, parsed_end = _parse_window(window_start, window_end)
    return get_security_analytics_service().export(window_start=parsed_start, window_end=parsed_end)


@router.get("/summary")
def analytics_summary_endpoint(
    window_start: Optional[str] = Query(default=None),
    window_end: Optional[str] = Query(default=None),
) -> dict:
    parsed_start, parsed_end = _parse_window(window_start, window_end)
    metrics = get_security_analytics_service().summary(window_start=parsed_start, window_end=parsed_end)
    return metrics.to_dict()


@router.get("/trends")
def analytics_trends_endpoint(bucket_seconds: float = Query(default=_DEFAULT_BUCKET_SECONDS)) -> list:
    try:
        snapshots = get_security_analytics_service().trends(bucket_seconds=bucket_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [snapshot.to_dict() for snapshot in snapshots]


@router.get("/risk")
def analytics_risk_endpoint(
    window_start: Optional[str] = Query(default=None),
    window_end: Optional[str] = Query(default=None),
) -> dict:
    parsed_start, parsed_end = _parse_window(window_start, window_end)
    score = get_security_analytics_service().risk_score(window_start=parsed_start, window_end=parsed_end)
    return {"risk_score": score}
