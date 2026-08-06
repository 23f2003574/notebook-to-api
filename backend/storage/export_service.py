from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response

from .dashboard import StorageDashboardAPI, get_storage_dashboard_api

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
class StorageExport:
    """A generated export: the manifest, the native data, and its serialized form."""

    manifest: ExportManifest
    data: object
    content: str

    def to_dict(self) -> dict:
        return {"manifest": self.manifest.to_dict(), "data": self.data}


class StorageExportService:
    """Serializes artifact, inventory, analytics, and full-storage snapshots for backup/integration."""

    def __init__(self, dashboard: StorageDashboardAPI) -> None:
        self._dashboard = dashboard

    def export_artifacts(self, *, format: str = "json") -> StorageExport:
        return self._build("artifacts", self._dashboard.artifacts()["artifacts"], format=format)

    def export_inventory(self, *, format: str = "json") -> StorageExport:
        return self._build("inventory", self._dashboard.capacity(), format=format)

    def export_analytics(self, *, format: str = "json") -> StorageExport:
        return self._build("analytics", self._dashboard.analytics(), format=format)

    def export_all(self, *, format: str = "json") -> StorageExport:
        return self._build("all", self._dashboard.snapshot(), format=format)

    def _build(self, export_type: str, data, *, format: str) -> StorageExport:
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
        return StorageExport(manifest=manifest, data=data, content=content)

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


_storage_export_service = StorageExportService(get_storage_dashboard_api())


def get_storage_export_service() -> StorageExportService:
    return _storage_export_service


router = APIRouter(prefix="/storage/export", tags=["storage-export"])


def _respond(export: StorageExport):
    if export.manifest.format == "json":
        return export.to_dict()
    return Response(
        content=export.content,
        media_type=_MEDIA_TYPES[export.manifest.format],
        headers={"X-Export-Checksum": export.manifest.checksum},
    )


@router.get("/artifacts")
def export_artifacts_endpoint(
    format: str = "json",
    service: StorageExportService = Depends(get_storage_export_service),
):
    try:
        export = service.export_artifacts(format=format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)


@router.get("/inventory")
def export_inventory_endpoint(
    format: str = "json",
    service: StorageExportService = Depends(get_storage_export_service),
):
    try:
        export = service.export_inventory(format=format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)


@router.get("/analytics")
def export_analytics_endpoint(
    format: str = "json",
    service: StorageExportService = Depends(get_storage_export_service),
):
    try:
        export = service.export_analytics(format=format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)


@router.get("/all")
def export_all_endpoint(
    format: str = "json",
    service: StorageExportService = Depends(get_storage_export_service),
):
    try:
        export = service.export_all(format=format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _respond(export)
