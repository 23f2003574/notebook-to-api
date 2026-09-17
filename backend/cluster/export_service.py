from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from .dashboard import ClusterDashboardAPI, get_cluster_dashboard_api

SUPPORTED_FORMATS = ("json", "csv", "yaml")
_MEDIA_TYPES = {"csv": "text/csv", "yaml": "application/x-yaml"}


@dataclass(frozen=True)
class ExportManifest:
    """Metadata describing a single export: what it is, how big, and a checksum for integrity."""

    export_type: str
    format: str
    record_count: int
    generated_at: datetime
    checksum: str

    def to_dict(self) -> dict:
        return {
            "export_type": self.export_type,
            "format": self.format,
            "record_count": self.record_count,
            "generated_at": self.generated_at.isoformat(),
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class ClusterExport:
    """A generated export: the manifest, the native data, and its serialized form."""

    manifest: ExportManifest
    data: object
    content: str

    def to_dict(self) -> dict:
        return {"manifest": self.manifest.to_dict(), "data": self.data}


class ClusterExportService:
    """Serializes worker, execution, analytics, and full-cluster snapshots for backup/integration."""

    def __init__(self, dashboard: ClusterDashboardAPI) -> None:
        self._dashboard = dashboard

    def export_workers(self, *, format: str = "json") -> ClusterExport:
        return self._build("workers", self._dashboard.workers(), format=format)

    def export_executions(self, *, format: str = "json", state: Optional[str] = None) -> ClusterExport:
        return self._build("executions", self._dashboard.executions(state=state), format=format)

    def export_analytics(self, *, format: str = "json", capability: Optional[str] = None) -> ClusterExport:
        return self._build("analytics", self._dashboard.analytics(capability=capability), format=format)

    def export_cluster(self, *, format: str = "json") -> ClusterExport:
        return self._build("cluster", self._dashboard.snapshot(), format=format)

    def _build(self, export_type: str, data, *, format: str) -> ClusterExport:
        if format not in SUPPORTED_FORMATS:
            raise ValueError(f"unsupported export format '{format}'; expected one of {SUPPORTED_FORMATS}")

        content = self._serialize(data, format)
        record_count = len(data) if isinstance(data, list) else 1
        manifest = ExportManifest(
            export_type=export_type,
            format=format,
            record_count=record_count,
            generated_at=datetime.now(timezone.utc),
            checksum=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        return ClusterExport(manifest=manifest, data=data, content=content)

    @staticmethod
    def _serialize(data, format: str) -> str:
        if format == "json":
            return json.dumps(data, sort_keys=True, indent=2)
        if format == "yaml":
            return _yaml_dump(data)
        return _csv_dump(data)


def _csv_dump(data) -> str:
    rows = data if isinstance(data, list) else [data]
    if not rows:
        return ""
    fieldnames = list(rows[0].keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        flat = {key: (json.dumps(value) if isinstance(value, (dict, list)) else value) for key, value in row.items()}
        writer.writerow(flat)
    return buffer.getvalue()


# --- minimal self-contained YAML block-style dumper (dump-only; no PyYAML dependency) ---


def _yaml_dump(value) -> str:
    return "\n".join(_yaml_lines(value)) + "\n"


def _yaml_lines(value, indent: int = 0):
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            yield f"{pad}{{}}"
            return
        for key, val in value.items():
            if isinstance(val, dict) and val:
                yield f"{pad}{key}:"
                yield from _yaml_lines(val, indent + 1)
            elif isinstance(val, list) and val:
                yield f"{pad}{key}:"
                yield from _yaml_lines(val, indent)
            else:
                yield f"{pad}{key}: {_yaml_scalar(val)}"
    elif isinstance(value, list):
        if not value:
            yield f"{pad}[]"
            return
        for item in value:
            if isinstance(item, dict) and item:
                sub_lines = list(_yaml_lines(item, indent + 1))
                yield f"{pad}- {sub_lines[0].lstrip()}"
                yield from sub_lines[1:]
            elif isinstance(item, list) and item:
                yield f"{pad}-"
                yield from _yaml_lines(item, indent + 1)
            else:
                yield f"{pad}- {_yaml_scalar(item)}"
    else:
        yield f"{pad}{_yaml_scalar(value)}"


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if _needs_quoting(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _needs_quoting(text: str) -> bool:
    if text == "" or text != text.strip():
        return True
    if text.lower() in ("null", "true", "false", "~"):
        return True
    if any(char in text for char in ':#{}[]&*!|>\'"%@`,\n'):
        return True
    try:
        float(text)
        return True
    except ValueError:
        return False


_cluster_export_service = ClusterExportService(get_cluster_dashboard_api())


def get_cluster_export_service() -> ClusterExportService:
    return _cluster_export_service


router = APIRouter(prefix="/cluster/export", tags=["cluster-export"])


def _respond(export: ClusterExport):
    if export.manifest.format == "json":
        return export.to_dict()
    return Response(
        content=export.content,
        media_type=_MEDIA_TYPES[export.manifest.format],
        headers={"X-Export-Checksum": export.manifest.checksum},
    )


@router.get("/workers")
def export_workers_endpoint(
    format: str = "json",
    service: ClusterExportService = Depends(get_cluster_export_service),
):
    try:
        export = service.export_workers(format=format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)


@router.get("/executions")
def export_executions_endpoint(
    format: str = "json",
    state: Optional[str] = None,
    service: ClusterExportService = Depends(get_cluster_export_service),
):
    try:
        export = service.export_executions(format=format, state=state)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)


@router.get("/analytics")
def export_analytics_endpoint(
    format: str = "json",
    capability: Optional[str] = None,
    service: ClusterExportService = Depends(get_cluster_export_service),
):
    try:
        export = service.export_analytics(format=format, capability=capability)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)


@router.get("/all")
def export_cluster_endpoint(
    format: str = "json",
    service: ClusterExportService = Depends(get_cluster_export_service),
):
    try:
        export = service.export_cluster(format=format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)
