from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

EVENT_TYPES = ("Authentication", "Authorization", "API Key", "Secret Access", "Session")
SEVERITY_LEVELS = ("Info", "Warning", "Critical")

_GENESIS_HASH = "0" * 64


def _new_id() -> str:
    return uuid.uuid4().hex


def _compute_hash(
    sequence: int,
    event_id: str,
    event_type: str,
    actor: str,
    resource: str,
    action: str,
    outcome: str,
    severity: str,
    timestamp: datetime,
    details: dict,
    previous_hash: str,
) -> str:
    payload = {
        "sequence": sequence,
        "event_id": event_id,
        "event_type": event_type,
        "actor": actor,
        "resource": resource,
        "action": action,
        "outcome": outcome,
        "severity": severity,
        "timestamp": timestamp.isoformat(),
        "details": details,
        "previous_hash": previous_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class InvalidEventTypeError(ValueError):
    pass


class InvalidSeverityError(ValueError):
    pass


class UnknownEventError(KeyError):
    pass


@dataclass(frozen=True)
class AuditEvent:
    """An immutable, hash-chained record of a security-sensitive event."""

    event_id: str
    sequence: int
    event_type: str
    actor: str
    resource: str
    action: str
    outcome: str
    severity: str
    details: dict
    timestamp: datetime
    previous_hash: str
    record_hash: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "actor": self.actor,
            "resource": self.resource,
            "action": self.action,
            "outcome": self.outcome,
            "severity": self.severity,
            "details": dict(self.details),
            "timestamp": self.timestamp.isoformat(),
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
        }


@dataclass(frozen=True)
class AuditFilter:
    """A set of filter criteria for narrowing down recorded audit events."""

    event_type: Optional[str] = None
    actor: Optional[str] = None
    resource: Optional[str] = None
    severity: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None

    def matches(self, event: AuditEvent) -> bool:
        if self.event_type is not None and event.event_type != self.event_type:
            return False
        if self.actor is not None and event.actor != self.actor:
            return False
        if self.resource is not None and event.resource != self.resource:
            return False
        if self.severity is not None and event.severity != self.severity:
            return False
        if self.since is not None and event.timestamp < self.since:
            return False
        if self.until is not None and event.timestamp > self.until:
            return False
        return True


class SecurityAuditLogger:
    """Records immutable, hash-chained security events with actor/resource tracking."""

    def __init__(self) -> None:
        self._events: list = []
        self._by_id: dict = {}
        self._lock = Lock()

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
        if event_type not in EVENT_TYPES:
            raise InvalidEventTypeError(f"unknown event type '{event_type}'")
        if severity not in SEVERITY_LEVELS:
            raise InvalidSeverityError(f"unknown severity '{severity}'")
        now = timestamp or datetime.now(timezone.utc)
        details = dict(details or {})
        with self._lock:
            sequence = len(self._events)
            previous_hash = self._events[-1].record_hash if self._events else _GENESIS_HASH
            event_id = _new_id()
            record_hash = _compute_hash(
                sequence,
                event_id,
                event_type,
                actor,
                resource,
                action,
                outcome,
                severity,
                now,
                details,
                previous_hash,
            )
            event = AuditEvent(
                event_id=event_id,
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                resource=resource,
                action=action,
                outcome=outcome,
                severity=severity,
                details=details,
                timestamp=now,
                previous_hash=previous_hash,
                record_hash=record_hash,
            )
            self._events.append(event)
            self._by_id[event_id] = event
        return event

    def get(self, event_id: str) -> AuditEvent:
        with self._lock:
            event = self._by_id.get(event_id)
        if event is None:
            raise UnknownEventError(event_id)
        return event

    def query(self, filter_: Optional[AuditFilter] = None) -> list:
        filter_ = filter_ or AuditFilter()
        with self._lock:
            events = list(self._events)
        return [event for event in events if filter_.matches(event)]

    def count(self, filter_: Optional[AuditFilter] = None) -> int:
        return len(self.query(filter_))

    def export(self, filter_: Optional[AuditFilter] = None) -> list:
        return [event.to_dict() for event in self.query(filter_)]

    def purge(self, *, before: datetime) -> int:
        with self._lock:
            purged_ids = {event.event_id for event in self._events if event.timestamp < before}
            self._events = [event for event in self._events if event.timestamp >= before]
            for event_id in purged_ids:
                del self._by_id[event_id]
        return len(purged_ids)


_security_audit_logger = SecurityAuditLogger()


def get_security_audit_logger() -> SecurityAuditLogger:
    return _security_audit_logger


router = APIRouter(prefix="/security/audit", tags=["security-audit-logger"])


@router.post("")
def record_event_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        event = get_security_audit_logger().record(
            payload.get("event_type", ""),
            payload.get("actor", ""),
            payload.get("resource", ""),
            payload.get("action", ""),
            outcome=payload.get("outcome", "success"),
            severity=payload.get("severity", "Info"),
            details=payload.get("details"),
        )
    except InvalidEventTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except InvalidSeverityError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return event.to_dict()


@router.get("")
def query_events_endpoint(
    event_type: Optional[str] = Query(default=None),
    actor: Optional[str] = Query(default=None),
    resource: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
) -> list:
    filter_ = AuditFilter(event_type=event_type, actor=actor, resource=resource, severity=severity)
    return get_security_audit_logger().export(filter_)


@router.get("/{event_id}")
def get_event_endpoint(event_id: str) -> dict:
    try:
        event = get_security_audit_logger().get(event_id)
    except UnknownEventError:
        raise HTTPException(status_code=404, detail="unknown audit event")
    return event.to_dict()


@router.post("/export")
def export_events_endpoint(payload: dict = Body(default={})) -> list:
    filter_ = AuditFilter(
        event_type=payload.get("event_type"),
        actor=payload.get("actor"),
        resource=payload.get("resource"),
        severity=payload.get("severity"),
    )
    return get_security_audit_logger().export(filter_)
