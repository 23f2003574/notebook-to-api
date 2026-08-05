import csv
import io
import json

import pytest

from backend.observability.dashboard import ObservabilityDashboardAPI
from backend.observability.distributed_tracing import DistributedTracingEngine
from backend.observability.export_service import TelemetryExportService
from backend.observability.log_aggregation import LogAggregationService
from backend.observability.metrics_storage import MetricsStorageEngine
from backend.observability.alert_engine import AlertRuleEngine
from backend.observability.health_checks import HealthCheckFramework
from backend.observability.observability_analytics import ObservabilityAnalyticsService


@pytest.fixture
def storage():
    return MetricsStorageEngine()


@pytest.fixture
def tracing():
    return DistributedTracingEngine()


@pytest.fixture
def logs():
    return LogAggregationService()


@pytest.fixture
def export_service(storage, tracing, logs):
    return TelemetryExportService(storage, tracing, logs)


class TestMetricsExport:
    def test_export_metrics_returns_json_payload(self, export_service, storage):
        storage.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)

        export = export_service.export_metrics(["cpu_usage"])

        assert export.export_type == "metrics"
        payload = json.loads(export.payload)
        assert payload == [{"metric_name": "cpu_usage", "timestamp": "2026-01-01T00:00:00+00:00", "value": 42}]

    def test_export_metrics_supports_csv(self, export_service, storage):
        storage.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)

        export = export_service.export_metrics(["cpu_usage"], format="csv")

        rows = list(csv.DictReader(io.StringIO(export.payload)))
        assert rows[0]["metric_name"] == "cpu_usage"

    def test_export_metrics_rejects_unknown_format(self, export_service, storage):
        storage.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)

        with pytest.raises(ValueError):
            export_service.export_metrics(["cpu_usage"], format="xml")


class TestTraceExport:
    def test_export_traces_returns_span_records(self, export_service, tracing):
        context = tracing.start_span("handle request", "http")
        tracing.finish_span(context.span_id)

        export = export_service.export_traces(context.trace_id)

        payload = json.loads(export.payload)
        assert payload[0]["span_id"] == context.span_id

    def test_export_traces_opentelemetry_format(self, export_service, tracing):
        context = tracing.start_span("handle request", "http")

        export = export_service.export_traces(context.trace_id, format="opentelemetry")

        payload = json.loads(export.payload)
        assert "resourceSpans" in payload


class TestLogExport:
    def test_export_logs_returns_entries(self, export_service, logs):
        logs.ingest("api", "info", "request handled")

        export = export_service.export_logs()

        payload = json.loads(export.payload)
        assert payload[0]["message"] == "request handled"


class TestCompleteBundleExport:
    def test_export_all_bundles_metrics_traces_and_logs(
        self, export_service, storage, tracing, logs
    ):
        storage.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)
        context = tracing.start_span("handle request", "http")
        logs.ingest("api", "info", "request handled")

        manifest = export_service.export_all(["cpu_usage"], trace_id=context.trace_id)

        export_types = {export.export_type for export in manifest.exports}
        assert export_types == {"metrics", "traces", "logs"}
        assert manifest.manifest_id

    def test_export_all_without_trace_id_skips_traces(self, export_service, storage, logs):
        storage.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)
        logs.ingest("api", "info", "request handled")

        manifest = export_service.export_all(["cpu_usage"])

        export_types = {export.export_type for export in manifest.exports}
        assert export_types == {"metrics", "logs"}

    def test_dashboard_export_delegates_to_export_service(self, export_service, storage):
        storage.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)
        dashboard = ObservabilityDashboardAPI(
            ObservabilityAnalyticsService(),
            AlertRuleEngine(),
            HealthCheckFramework(),
            export_service=export_service,
        )

        manifest = dashboard.export(["cpu_usage"])

        assert manifest.manifest_id

    def test_dashboard_export_without_service_raises(self):
        dashboard = ObservabilityDashboardAPI(
            ObservabilityAnalyticsService(),
            AlertRuleEngine(),
            HealthCheckFramework(),
        )

        with pytest.raises(ValueError):
            dashboard.export(["cpu_usage"])
