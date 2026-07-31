from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .gateway_dashboard_api import GatewayDashboardAPI, get_dashboard_api

EXPORT_FORMATS = frozenset({"json", "yaml", "csv"})


class UnsupportedExportFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ExportMetadata:
    """Provenance information attached to every export."""

    exported_at: datetime
    format: str
    source: str = "gateway-export-service"
    version: str = "1.0"

    def to_dict(self) -> dict:
        return {
            "exported_at": self.exported_at.isoformat(),
            "format": self.format,
            "source": self.source,
            "version": self.version,
        }


@dataclass(frozen=True)
class GatewayExport:
    """An exported payload plus the metadata describing how it was produced."""

    metadata: ExportMetadata
    data: Any

    def to_dict(self) -> dict:
        return {"metadata": self.metadata.to_dict(), "data": self.data}


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if value == "" or any(char in value for char in ":#\n\"'"):
            return json.dumps(value)
        return value
    return str(value)


def _to_yaml(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return f"{pad}{{}}\n"
        lines = []
        for key, val in value.items():
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{pad}{key}:")
                lines.append(_to_yaml(val, indent + 1).rstrip("\n"))
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(val)}")
        return "\n".join(lines) + "\n"
    if isinstance(value, list):
        if not value:
            return f"{pad}[]\n"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                nested_lines = _to_yaml(item, indent + 1).rstrip("\n").split("\n")
                lines.append(f"{pad}- {nested_lines[0].strip()}")
                lines.extend(nested_lines[1:])
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return "\n".join(lines) + "\n"
    return f"{pad}{_yaml_scalar(value)}\n"


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _to_csv(value: Any) -> str:
    buffer = io.StringIO()
    if isinstance(value, list):
        fieldnames: list = []
        for row in value:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for row in value:
            writer.writerow({key: _csv_cell(val) for key, val in row.items()})
    elif isinstance(value, dict):
        writer = csv.writer(buffer)
        writer.writerow(["key", "value"])
        for key, val in value.items():
            writer.writerow([key, _csv_cell(val)])
    else:
        writer = csv.writer(buffer)
        writer.writerow([value])
    return buffer.getvalue()


class GatewayExportService:
    """Exports gateway routes, configuration, and metrics as JSON, YAML, or CSV."""

    def __init__(self, dashboard: GatewayDashboardAPI) -> None:
        self._dashboard = dashboard

    def _build(self, payload: Any, export_format: str) -> GatewayExport:
        if export_format not in EXPORT_FORMATS:
            raise UnsupportedExportFormatError(f"unsupported export format: {export_format}")
        metadata = ExportMetadata(exported_at=datetime.now(timezone.utc), format=export_format)
        if export_format == "json":
            data: Any = payload
        elif export_format == "yaml":
            data = _to_yaml(payload)
        else:
            data = _to_csv(payload)
        return GatewayExport(metadata=metadata, data=data)

    def export_routes(self, export_format: str = "json") -> GatewayExport:
        return self._build(self._dashboard.routes(), export_format)

    def export_configuration(self, export_format: str = "json") -> GatewayExport:
        return self._build(self._dashboard.configuration(), export_format)

    def export_metrics(self, export_format: str = "json") -> GatewayExport:
        return self._build(self._dashboard.metrics(), export_format)

    def export_all(self, export_format: str = "json") -> GatewayExport:
        bundle = {
            "routes": self._dashboard.routes(),
            "configuration": self._dashboard.configuration(),
            "metrics": self._dashboard.metrics(),
        }
        return self._build(bundle, export_format)


_export_service = GatewayExportService(get_dashboard_api())


def get_export_service() -> GatewayExportService:
    return _export_service


router = APIRouter(prefix="/gateway/export", tags=["gateway-export"])


@router.get("/routes")
def export_routes_endpoint(
    format: str = Query(default="json"),
    service: GatewayExportService = Depends(get_export_service),
) -> dict:
    try:
        export = service.export_routes(format)
    except UnsupportedExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return export.to_dict()


@router.get("/configuration")
def export_configuration_endpoint(
    format: str = Query(default="json"),
    service: GatewayExportService = Depends(get_export_service),
) -> dict:
    try:
        export = service.export_configuration(format)
    except UnsupportedExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return export.to_dict()


@router.get("/metrics")
def export_metrics_endpoint(
    format: str = Query(default="json"),
    service: GatewayExportService = Depends(get_export_service),
) -> dict:
    try:
        export = service.export_metrics(format)
    except UnsupportedExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return export.to_dict()


@router.get("/all")
def export_all_endpoint(
    format: str = Query(default="json"),
    service: GatewayExportService = Depends(get_export_service),
) -> dict:
    try:
        export = service.export_all(format)
    except UnsupportedExportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return export.to_dict()
