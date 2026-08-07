from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .dashboard import SecurityDashboardAPI as LegacySecurityDashboardAPI, get_security_dashboard_api
from .ops_dashboard import SecurityDashboardAPI, get_security_dashboard_api as get_security_ops_dashboard_api

EXPORT_FORMATS = ("JSON", "CSV", "YAML")


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


def _dict_to_csv(data: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["key", "value"])
    for key, value in _flatten(data):
        writer.writerow([key, value])
    return buffer.getvalue()


def _serialize(export_format: str, data: dict) -> str:
    if export_format not in EXPORT_FORMATS:
        raise InvalidExportFormatError(f"unknown export format '{export_format}'")
    if export_format == "JSON":
        return json.dumps(data, default=str, indent=2)
    if export_format == "YAML":
        return _to_yaml(data)
    return _dict_to_csv(data)


@dataclass(frozen=True)
class SecurityExport:
    """A timestamped, immutable snapshot of an exported security dataset."""

    export_id: str
    dataset: str
    format: str
    content: str
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "export_id": self.export_id,
            "dataset": self.dataset,
            "format": self.format,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class ExportManifest:
    """A record of what datasets a bundled export contains and when it was produced."""

    export_id: str
    datasets: tuple
    legacy_datasets: tuple
    generated_at: datetime

    def to_dict(self) -> dict:
        return {
            "export_id": self.export_id,
            "datasets": list(self.datasets),
            "legacy_datasets": list(self.legacy_datasets),
            "generated_at": self.generated_at.isoformat(),
        }


class SecurityExportService:
    """Exports identities, audit logs, and analytics for backup, compliance, and SIEM ingestion."""

    def __init__(
        self,
        dashboard_api: Optional[SecurityDashboardAPI] = None,
        legacy_dashboard_api: Optional[LegacySecurityDashboardAPI] = None,
    ) -> None:
        self._dashboard_api = dashboard_api or get_security_ops_dashboard_api()
        self._legacy_dashboard_api = legacy_dashboard_api or get_security_dashboard_api()
        self._exports: dict = {}
        self._lock = Lock()

    def _store(
        self, dataset: str, export_format: str, data: dict, *, timestamp: datetime
    ) -> SecurityExport:
        content = _serialize(export_format, data)
        export = SecurityExport(
            export_id=_new_id(),
            dataset=dataset,
            format=export_format,
            content=content,
            created_at=timestamp,
        )
        with self._lock:
            self._exports[export.export_id] = export
        return export

    def export_identities(
        self, *, export_format: str = "JSON", timestamp: Optional[datetime] = None
    ) -> SecurityExport:
        now = timestamp or datetime.now(timezone.utc)
        return self._store("identities", export_format, self._dashboard_api.identities(), timestamp=now)

    def export_audit_logs(
        self, *, export_format: str = "JSON", timestamp: Optional[datetime] = None
    ) -> SecurityExport:
        now = timestamp or datetime.now(timezone.utc)
        return self._store("audit", export_format, self._dashboard_api.audits(), timestamp=now)

    def export_analytics(
        self, *, export_format: str = "JSON", timestamp: Optional[datetime] = None
    ) -> SecurityExport:
        now = timestamp or datetime.now(timezone.utc)
        return self._store("analytics", export_format, self._dashboard_api.analytics(), timestamp=now)

    def export_all(self, *, export_format: str = "JSON", timestamp: Optional[datetime] = None) -> dict:
        now = timestamp or datetime.now(timezone.utc)
        identities = self.export_identities(export_format=export_format, timestamp=now)
        audit = self.export_audit_logs(export_format=export_format, timestamp=now)
        analytics = self.export_analytics(export_format=export_format, timestamp=now)
        legacy_manifest = self._legacy_dashboard_api.manifest(timestamp=now)
        manifest = ExportManifest(
            export_id=_new_id(),
            datasets=("identities", "audit", "analytics"),
            legacy_datasets=tuple(legacy_manifest["datasets"]),
            generated_at=now,
        )
        return {
            "manifest": manifest.to_dict(),
            "identities": identities.to_dict(),
            "audit": audit.to_dict(),
            "analytics": analytics.to_dict(),
        }

    def get(self, export_id: str) -> SecurityExport:
        with self._lock:
            export = self._exports.get(export_id)
        if export is None:
            raise UnknownExportError(export_id)
        return export


_security_export_service = SecurityExportService()


def get_security_export_service() -> SecurityExportService:
    return _security_export_service


router = APIRouter(prefix="/security/export", tags=["security-export-bundle"])


@router.get("/identities")
def export_identities_endpoint(format: str = Query(default="JSON")) -> dict:
    try:
        result = get_security_export_service().export_identities(export_format=format)
    except InvalidExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.get("/audit")
def export_audit_endpoint(format: str = Query(default="JSON")) -> dict:
    try:
        result = get_security_export_service().export_audit_logs(export_format=format)
    except InvalidExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.get("/analytics")
def export_analytics_endpoint(format: str = Query(default="JSON")) -> dict:
    try:
        result = get_security_export_service().export_analytics(export_format=format)
    except InvalidExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.get("/all")
def export_all_endpoint(format: str = Query(default="JSON")) -> dict:
    try:
        return get_security_export_service().export_all(export_format=format)
    except InvalidExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
