from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Optional

from .inference_analytics import InferenceAnalyticsService, get_inference_analytics_service
from .model_benchmark import ModelBenchmarkService, get_model_benchmark_service
from .model_deployment import ModelDeploymentManager, get_model_deployment_manager
from .model_registry import ModelRegistry, get_model_registry


class ExportFormat(str, Enum):
    """The serialization used for an exported model snapshot."""

    JSON = "json"
    CSV = "csv"
    YAML = "yaml"


_CONTENT_TYPES = {
    ExportFormat.JSON: "application/json",
    ExportFormat.CSV: "text/csv",
    ExportFormat.YAML: "application/x-yaml",
}


@dataclass(frozen=True)
class ModelExport:
    """A single exported section, serialized to text."""

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
    export: ModelExport

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


class ModelExportService:
    """Exports model inventory, deployment snapshots, and benchmark history for backup/integration."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        deployments: ModelDeploymentManager,
        benchmarks: ModelBenchmarkService,
        analytics: InferenceAnalyticsService,
    ) -> None:
        self._registry = registry
        self._deployments = deployments
        self._benchmarks = benchmarks
        self._analytics = analytics
        self._lock = Lock()
        self._sequence = 0

    def _gather_models(self) -> list:
        return [model.to_dict() for model in self._registry.list_models()]

    def _gather_deployments(self) -> list:
        return [deployment.to_dict() for deployment in self._deployments.list_deployments()]

    def _gather_benchmarks(self) -> list:
        return [result.to_dict() for result in self._benchmarks.list_results()]

    def _build_export(self, section: str, data: Any, fmt: ExportFormat) -> ModelExport:
        content = _serialize(data, fmt)
        return ModelExport(
            section=section,
            format=fmt,
            generated_at=datetime.now(timezone.utc),
            content=content,
            content_type=_CONTENT_TYPES[fmt],
            size_bytes=len(content.encode("utf-8")),
        )

    def export_models(self, *, fmt: ExportFormat = ExportFormat.JSON) -> ModelExport:
        return self._build_export("models", self._gather_models(), fmt)

    def export_deployments(self, *, fmt: ExportFormat = ExportFormat.JSON) -> ModelExport:
        return self._build_export("deployments", self._gather_deployments(), fmt)

    def export_benchmarks(self, *, fmt: ExportFormat = ExportFormat.JSON) -> ModelExport:
        return self._build_export("benchmarks", self._gather_benchmarks(), fmt)

    def export_all(self, *, fmt: ExportFormat = ExportFormat.JSON) -> ExportManifest:
        combined = {
            "models": self._gather_models(),
            "deployments": self._gather_deployments(),
            "benchmarks": self._gather_benchmarks(),
            "analytics": self._analytics.summary(),
        }
        export = self._build_export("all", combined, fmt)
        checksum = hashlib.sha256(export.content.encode("utf-8")).hexdigest()
        with self._lock:
            self._sequence += 1
            export_id = f"export-{self._sequence}"
        return ExportManifest(
            export_id=export_id,
            sections=("models", "deployments", "benchmarks", "analytics"),
            format=fmt,
            generated_at=export.generated_at,
            size_bytes=export.size_bytes,
            checksum=checksum,
            export=export,
        )


_model_export_service: Optional[ModelExportService] = None


def get_model_export_service() -> ModelExportService:
    global _model_export_service
    if _model_export_service is None:
        _model_export_service = ModelExportService(
            registry=get_model_registry(),
            deployments=get_model_deployment_manager(),
            benchmarks=get_model_benchmark_service(),
            analytics=get_inference_analytics_service(),
        )
    return _model_export_service
