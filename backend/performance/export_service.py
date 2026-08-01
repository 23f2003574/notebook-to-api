from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .dashboard import PerformanceDashboardAPI
    from .profiler import PerformanceProfiler


class ExportFormat(str, Enum):
    """The serialization used for an exported performance snapshot."""

    JSON = "json"
    CSV = "csv"
    YAML = "yaml"


_CONTENT_TYPES = {
    ExportFormat.JSON: "application/json",
    ExportFormat.CSV: "text/csv",
    ExportFormat.YAML: "application/x-yaml",
}


@dataclass(frozen=True)
class PerformanceExport:
    """A single exported performance section, serialized to text."""

    section: str
    format: ExportFormat
    generated_at: datetime
    content: str
    content_type: str
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "format": self.format.value,
            "generated_at": self.generated_at.isoformat(),
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "content": self.content,
        }


@dataclass(frozen=True)
class ExportManifest:
    """Describes a multi-section export bundle, with integrity metadata."""

    export_id: str
    sections: tuple
    format: ExportFormat
    generated_at: datetime
    size_bytes: int
    checksum: str
    export: PerformanceExport

    def to_dict(self) -> dict:
        return {
            "export_id": self.export_id,
            "sections": list(self.sections),
            "format": self.format.value,
            "generated_at": self.generated_at.isoformat(),
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "export": self.export.to_dict(),
        }


def _flatten(data: dict, prefix: str = "") -> dict:
    flat: dict = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, full_key))
        elif isinstance(value, list):
            flat[full_key] = json.dumps(value, default=str)
        else:
            flat[full_key] = value
    return flat


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or text.strip() != text or any(ch in text for ch in (":", "#", "\n")):
        return json.dumps(text)
    return text


def _yaml_lines(data: Any, indent: int = 0) -> list:
    pad = "  " * indent
    lines: list = []
    if isinstance(data, dict):
        if not data:
            lines.append(f"{pad}{{}}")
            return lines
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_lines(value, indent + 1))
            elif isinstance(value, dict):
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(value, list):
                lines.append(f"{pad}{key}: []")
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(value)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item:
                item_lines = _yaml_lines(item, indent + 1)
                lines.append(f"{pad}- {item_lines[0].strip()}")
                lines.extend(item_lines[1:])
            elif isinstance(item, list) and item:
                lines.append(f"{pad}-")
                lines.extend(_yaml_lines(item, indent + 1))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    else:
        lines.append(f"{pad}{_yaml_scalar(data)}")
    return lines


def _to_yaml(data: Any) -> str:
    return "\n".join(_yaml_lines(data)) + "\n"


def _to_csv(data: Any) -> str:
    buffer = io.StringIO()
    if isinstance(data, list):
        rows = [_flatten(item) if isinstance(item, dict) else {"value": item} for item in data]
        fieldnames = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    elif isinstance(data, dict):
        writer = csv.writer(buffer)
        writer.writerow(["key", "value"])
        for key, value in _flatten(data).items():
            writer.writerow([key, value])
    else:
        buffer.write(str(data))
    return buffer.getvalue()


def _serialize(data: Any, fmt: ExportFormat) -> str:
    if fmt == ExportFormat.JSON:
        return json.dumps(data, indent=2, default=str)
    if fmt == ExportFormat.CSV:
        return _to_csv(data)
    if fmt == ExportFormat.YAML:
        return _to_yaml(data)
    raise ValueError(f"unsupported export format: {fmt}")


class PerformanceExportService:
    """Exports performance metrics, cache state, and profiler reports for external use."""

    def __init__(self, *, dashboard: "PerformanceDashboardAPI", profiler: "PerformanceProfiler") -> None:
        self._dashboard = dashboard
        self._profiler = profiler
        self._lock = Lock()
        self._sequence = 0

    def _gather_metrics(self, sections: Optional[list] = None) -> dict:
        overview = self._dashboard.overview()
        available = {
            "resources": overview["resources"],
            "connections": overview["connections"],
            "compression": overview["compression"],
            "profiler": overview["profiler"],
        }
        if sections is None:
            return available
        return {name: value for name, value in available.items() if name in sections}

    def _gather_cache(self) -> dict:
        return self._dashboard.cache()

    def _gather_profiles(self, session_ids: Optional[list] = None) -> list:
        sessions = self._profiler.list_sessions()
        if session_ids is not None:
            sessions = [session for session in sessions if session.session_id in session_ids]
        return [self._profiler.report(session.session_id).to_dict() for session in sessions]

    def _build_export(self, section: str, data: Any, fmt: ExportFormat) -> PerformanceExport:
        content = _serialize(data, fmt)
        return PerformanceExport(
            section=section,
            format=fmt,
            generated_at=datetime.now(timezone.utc),
            content=content,
            content_type=_CONTENT_TYPES[fmt],
            size_bytes=len(content.encode("utf-8")),
        )

    def export_metrics(
        self, *, fmt: ExportFormat = ExportFormat.JSON, sections: Optional[list] = None
    ) -> PerformanceExport:
        return self._build_export("metrics", self._gather_metrics(sections), fmt)

    def export_cache(self, *, fmt: ExportFormat = ExportFormat.JSON) -> PerformanceExport:
        return self._build_export("cache", self._gather_cache(), fmt)

    def export_profiles(
        self, *, fmt: ExportFormat = ExportFormat.JSON, session_ids: Optional[list] = None
    ) -> PerformanceExport:
        return self._build_export("profiles", self._gather_profiles(session_ids), fmt)

    def export_all(self, *, fmt: ExportFormat = ExportFormat.JSON) -> ExportManifest:
        combined = {
            "metrics": self._gather_metrics(),
            "cache": self._gather_cache(),
            "profiles": self._gather_profiles(),
        }
        export = self._build_export("all", combined, fmt)
        checksum = hashlib.sha256(export.content.encode("utf-8")).hexdigest()
        with self._lock:
            self._sequence += 1
            export_id = f"export-{self._sequence}"
        return ExportManifest(
            export_id=export_id,
            sections=("metrics", "cache", "profiles"),
            format=fmt,
            generated_at=export.generated_at,
            size_bytes=export.size_bytes,
            checksum=checksum,
            export=export,
        )


_performance_export_service: Optional[PerformanceExportService] = None


def get_performance_export_service() -> PerformanceExportService:
    global _performance_export_service
    if _performance_export_service is None:
        from .dashboard import get_performance_dashboard_api
        from .profiler import get_performance_profiler

        _performance_export_service = PerformanceExportService(
            dashboard=get_performance_dashboard_api(),
            profiler=get_performance_profiler(),
        )
    return _performance_export_service
