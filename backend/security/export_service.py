from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Iterator, Optional

from fastapi import APIRouter, Body, HTTPException

from .audit_logs import AuditLogService, AuditQuery, get_audit_log_service
from .dashboard import SecurityDashboardAPI, get_security_dashboard_api
from .security_analytics import SecurityAnalyticsService, get_security_analytics_service

EXPORT_FORMATS = ("JSON", "CSV", "YAML")
DATASETS = ("audit", "sessions", "analytics", "dashboard")


def _new_id() -> str:
    return uuid.uuid4().hex


class InvalidExportFormatError(ValueError):
    pass


class UnknownExportError(KeyError):
    pass


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _to_yaml(value, indent: int = 0) -> str:
    """A minimal, dependency-free YAML-like serializer for plain dict/list/scalar data."""
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return f"{pad}{{}}\n"
        lines = []
        for key, val in value.items():
            if isinstance(val, dict) and val:
                lines.append(f"{pad}{key}:")
                lines.append(_to_yaml(val, indent + 1).rstrip("\n"))
            elif isinstance(val, list) and val:
                lines.append(f"{pad}{key}:")
                lines.append(_to_yaml(val, indent).rstrip("\n"))
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(val)}")
        return "\n".join(lines) + "\n"
    if isinstance(value, list):
        if not value:
            return f"{pad}[]\n"
        lines = []
        for item in value:
            if isinstance(item, dict) and item:
                item_lines = _to_yaml(item, indent + 1).rstrip("\n").split("\n")
                lines.append(f"{pad}- {item_lines[0].strip()}")
                lines.extend(item_lines[1:])
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return "\n".join(lines) + "\n"
    return f"{pad}{_yaml_scalar(value)}\n"


def _flatten(data: dict, prefix: str = "") -> list:
    rows = []
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            rows.extend(_flatten(value, full_key))
        elif isinstance(value, list):
            rows.append((full_key, json.dumps(value, default=str)))
        else:
            rows.append((full_key, value))
    return rows


def _list_to_csv(records: list) -> str:
    if not records:
        return ""
    buffer = io.StringIO()
    fieldnames = list(records[0].keys())
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        writer.writerow({key: record.get(key) for key in fieldnames})
    return buffer.getvalue()


def _dict_to_csv(data: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["key", "value"])
    for key, value in _flatten(data):
        writer.writerow([key, value])
    return buffer.getvalue()


@dataclass(frozen=True)
class SecurityExportRequest:
    """The parameters that produced a security data export."""

    dataset: str
    format: str
    filters: dict
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "format": self.format,
            "filters": dict(self.filters),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class SecurityExportResult:
    """A timestamped, immutable snapshot of an exported dataset."""

    export_id: str
    request: SecurityExportRequest
    content: str
    record_count: int
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "export_id": self.export_id,
            "request": self.request.to_dict(),
            "content": self.content,
            "record_count": self.record_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SecurityExportService:
    """Produces multi-format exports of audit, session, analytics, and dashboard data."""

    def __init__(
        self,
        audit_log: Optional[AuditLogService] = None,
        analytics_service: Optional[SecurityAnalyticsService] = None,
        dashboard_api: Optional[SecurityDashboardAPI] = None,
    ) -> None:
        self._audit_log = audit_log or get_audit_log_service()
        self._analytics_service = analytics_service or get_security_analytics_service()
        self._dashboard_api = dashboard_api or get_security_dashboard_api()
        self._exports: dict = {}
        self._lock = Lock()

    def _serialize(self, export_format: str, data) -> str:
        if export_format not in EXPORT_FORMATS:
            raise InvalidExportFormatError(f"unknown export format '{export_format}'")
        if export_format == "JSON":
            return json.dumps(data, default=str, indent=2)
        if export_format == "YAML":
            return _to_yaml(data)
        return _list_to_csv(data) if isinstance(data, list) else _dict_to_csv(data)

    def _store(
        self, dataset: str, export_format: str, filters: dict, data, *, timestamp: datetime
    ) -> SecurityExportResult:
        content = self._serialize(export_format, data)
        record_count = len(data) if isinstance(data, list) else 1
        request = SecurityExportRequest(
            dataset=dataset, format=export_format, filters=dict(filters), created_at=timestamp
        )
        result = SecurityExportResult(
            export_id=_new_id(),
            request=request,
            content=content,
            record_count=record_count,
            created_at=timestamp,
        )
        with self._lock:
            self._exports[result.export_id] = result
        return result

    def export_audit(
        self,
        *,
        export_format: str = "JSON",
        event_type: Optional[str] = None,
        actor: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        timestamp: Optional[datetime] = None,
    ) -> SecurityExportResult:
        now = timestamp or datetime.now(timezone.utc)
        query = AuditQuery(event_type=event_type, actor=actor, since=since, until=until)
        events = [event.to_dict() for event in self._audit_log.query(query)]
        filters = {
            "event_type": event_type,
            "actor": actor,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        }
        return self._store("audit", export_format, filters, events, timestamp=now)

    def export_sessions(
        self, *, export_format: str = "JSON", timestamp: Optional[datetime] = None
    ) -> SecurityExportResult:
        now = timestamp or datetime.now(timezone.utc)
        events = self._dashboard_api.sessions(timestamp=now).get("recent_events", [])
        return self._store("sessions", export_format, {}, events, timestamp=now)

    def export_analytics(
        self, *, export_format: str = "JSON", timestamp: Optional[datetime] = None
    ) -> SecurityExportResult:
        now = timestamp or datetime.now(timezone.utc)
        data = self._analytics_service.export()
        return self._store("analytics", export_format, {}, data, timestamp=now)

    def export_dashboard(
        self, *, export_format: str = "JSON", timestamp: Optional[datetime] = None
    ) -> SecurityExportResult:
        now = timestamp or datetime.now(timezone.utc)
        data = self._dashboard_api.overview()
        return self._store("dashboard", export_format, {}, data, timestamp=now)

    def get(self, export_id: str) -> SecurityExportResult:
        with self._lock:
            result = self._exports.get(export_id)
        if result is None:
            raise UnknownExportError(export_id)
        return result

    def stream(self, export_id: str, *, chunk_size: int = 1) -> Iterator[str]:
        result = self.get(export_id)
        lines = result.content.splitlines(keepends=True)
        for index in range(0, len(lines), chunk_size):
            yield "".join(lines[index : index + chunk_size])


_security_export_service = SecurityExportService()


def get_security_export_service() -> SecurityExportService:
    return _security_export_service


router = APIRouter(prefix="/security", tags=["security-export"])


@router.post("/export")
def create_export_endpoint(payload: dict = Body(default={})) -> dict:
    dataset = payload.get("dataset", "")
    export_format = payload.get("format", "JSON")
    service = get_security_export_service()
    try:
        if dataset == "audit":
            result = service.export_audit(
                export_format=export_format,
                event_type=payload.get("event_type"),
                actor=payload.get("actor"),
            )
        elif dataset == "sessions":
            result = service.export_sessions(export_format=export_format)
        elif dataset == "analytics":
            result = service.export_analytics(export_format=export_format)
        elif dataset == "dashboard":
            result = service.export_dashboard(export_format=export_format)
        else:
            raise HTTPException(status_code=422, detail=f"unknown dataset '{dataset}'")
    except InvalidExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.post("/export/dashboard")
def export_dashboard_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        result = get_security_export_service().export_dashboard(
            export_format=payload.get("format", "JSON")
        )
    except InvalidExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.get("/export/{export_id}")
def get_export_endpoint(export_id: str) -> dict:
    try:
        result = get_security_export_service().get(export_id)
    except UnknownExportError:
        raise HTTPException(status_code=404, detail="unknown export")
    return result.to_dict()
