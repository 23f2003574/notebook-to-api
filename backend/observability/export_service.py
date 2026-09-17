import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from backend.observability.distributed_tracing import DistributedTracingEngine
from backend.observability.log_aggregation import LogAggregationService
from backend.observability.metrics_storage import MetricsStorageEngine


VALID_FORMATS = ("json", "csv", "opentelemetry")


@dataclass
class TelemetryExport:
    export_id: str
    export_type: str
    format: str
    generated_at: str
    payload: str


@dataclass
class ExportManifest:
    manifest_id: str
    generated_at: str
    exports: List[TelemetryExport] = field(default_factory=list)


class TelemetryExportService:
    def __init__(
        self,
        storage_engine: MetricsStorageEngine,
        tracing_engine: DistributedTracingEngine,
        log_service: LogAggregationService,
    ):
        self._storage_engine = storage_engine
        self._tracing_engine = tracing_engine
        self._log_service = log_service

    def export_metrics(self, metric_names: List[str], format: str = "json") -> TelemetryExport:
        _validate_format(format)
        rows = []
        for name in metric_names:
            for point in self._storage_engine.read(name):
                rows.append({"metric_name": name, **point})
        return _build_export("metrics", format, rows)

    def export_traces(self, trace_id: str, format: str = "json") -> TelemetryExport:
        _validate_format(format)
        rows = [
            {
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "name": span.name,
                "span_type": span.span_type,
                "parent_span_id": span.parent_span_id,
                "start_time": span.start_time,
                "end_time": span.end_time,
                "duration_ms": span.duration_ms,
            }
            for span in self._tracing_engine.trace(trace_id)
        ]
        return _build_export("traces", format, rows)

    def export_logs(self, format: str = "json") -> TelemetryExport:
        _validate_format(format)
        rows = [
            {
                "source": entry.source,
                "severity": entry.severity,
                "message": entry.message,
                "timestamp": entry.timestamp,
            }
            for entry in self._log_service.query()
        ]
        return _build_export("logs", format, rows)

    def export_all(
        self,
        metric_names: List[str],
        trace_id: Optional[str] = None,
        format: str = "json",
    ) -> ExportManifest:
        exports = [self.export_metrics(metric_names, format=format)]
        if trace_id is not None:
            exports.append(self.export_traces(trace_id, format=format))
        exports.append(self.export_logs(format=format))

        return ExportManifest(
            manifest_id=str(uuid4()),
            generated_at=_utc_now_iso(),
            exports=exports,
        )


def _validate_format(format: str) -> None:
    if format not in VALID_FORMATS:
        raise ValueError(f"Unsupported format '{format}'. Expected one of {VALID_FORMATS}.")


def _build_export(export_type: str, format: str, rows: List[Dict]) -> TelemetryExport:
    if format == "json":
        payload = json.dumps(rows)
    elif format == "csv":
        payload = _rows_to_csv(rows)
    else:
        otlp_key = {
            "metrics": "resourceMetrics",
            "traces": "resourceSpans",
            "logs": "resourceLogs",
        }[export_type]
        payload = json.dumps({otlp_key: rows})

    return TelemetryExport(
        export_id=str(uuid4()),
        export_type=export_type,
        format=format,
        generated_at=_utc_now_iso(),
        payload=payload,
    )


def _rows_to_csv(rows: List[Dict]) -> str:
    if not rows:
        return ""

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
