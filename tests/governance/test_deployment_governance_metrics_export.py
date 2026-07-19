import csv
import io
import json
from datetime import datetime, timezone

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetricsService,
)
from backend.observability.deployment_governance_metrics_export import (
    GovernanceIntegrityMetricsExportService,
)
from backend.observability.deployment_governance_metrics_history import (
    InMemoryGovernanceIntegrityMetricsHistoryRepository,
)


def _service_with_history():
    history_repository = (
        InMemoryGovernanceIntegrityMetricsHistoryRepository()
    )

    metrics_service = GovernanceIntegrityMetricsService(
        auto_flush_enabled=False,
        history_repository=history_repository,
    )

    return metrics_service, history_repository


class TestGovernanceIntegrityMetricsExportServiceDict:

    def test_export_dict_includes_current_metrics(self):
        metrics_service = GovernanceIntegrityMetricsService()

        metrics_service.record_success(100.0)
        metrics_service.record_failure(50.0)

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = export_service.export_dict()

        assert payload["metrics"]["total_dispatches"] == 2
        assert payload["metrics"]["successful_dispatches"] == 1
        assert payload["metrics"]["failed_dispatches"] == 1
        assert "history" not in payload

    def test_export_dict_field_order_is_deterministic(self):
        metrics_service = GovernanceIntegrityMetricsService()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = export_service.export_dict(include_history=True)

        assert list(payload.keys()) == [
            "exported_at",
            "metrics",
            "history",
        ]

    def test_export_dict_timestamp_is_utc_iso8601(self):
        metrics_service = GovernanceIntegrityMetricsService()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = export_service.export_dict()

        parsed = datetime.fromisoformat(payload["exported_at"])

        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)

    def test_export_dict_includes_history_when_requested(self):
        metrics_service, _ = _service_with_history()

        metrics_service.record_success(10.0)
        metrics_service.capture_snapshot()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = export_service.export_dict(include_history=True)

        assert len(payload["history"]) == 1
        assert (
            payload["history"][0]["metrics"]["successful_dispatches"]
            == 1
        )

    def test_export_dict_history_omitted_by_default(self):
        metrics_service, _ = _service_with_history()

        metrics_service.record_success(10.0)
        metrics_service.capture_snapshot()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = export_service.export_dict()

        assert "history" not in payload

    def test_export_dict_empty_history_is_empty_list(self):
        metrics_service, _ = _service_with_history()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = export_service.export_dict(include_history=True)

        assert payload["history"] == []

    def test_export_dict_respects_history_limit(self):
        metrics_service, _ = _service_with_history()

        for _ in range(5):
            metrics_service.capture_snapshot()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = export_service.export_dict(
            include_history=True, history_limit=2
        )

        assert len(payload["history"]) == 2


class TestGovernanceIntegrityMetricsExportServiceJson:

    def test_export_json_round_trips(self):
        metrics_service = GovernanceIntegrityMetricsService()

        metrics_service.record_success(100.0)

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        payload = json.loads(export_service.export_json())

        assert payload["metrics"]["successful_dispatches"] == 1

    def test_export_json_preserves_field_order(self):
        metrics_service = GovernanceIntegrityMetricsService()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        rendered = export_service.export_json(include_history=True)

        exported_at_index = rendered.index('"exported_at"')
        metrics_index = rendered.index('"metrics"')
        history_index = rendered.index('"history"')

        assert exported_at_index < metrics_index < history_index


class TestGovernanceIntegrityMetricsExportServiceCsv:

    def test_export_csv_has_current_row(self):
        metrics_service = GovernanceIntegrityMetricsService()

        metrics_service.record_success(100.0)
        metrics_service.record_failure(50.0)

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        rows = list(
            csv.DictReader(io.StringIO(export_service.export_csv()))
        )

        assert len(rows) == 1
        assert rows[0]["row_type"] == "current"
        assert rows[0]["total_dispatches"] == "2"

    def test_export_csv_includes_history_rows_when_requested(self):
        metrics_service, _ = _service_with_history()

        metrics_service.record_success(10.0)
        metrics_service.capture_snapshot()

        metrics_service.record_success(20.0)
        metrics_service.capture_snapshot()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        rows = list(
            csv.DictReader(
                io.StringIO(
                    export_service.export_csv(include_history=True)
                )
            )
        )

        assert len(rows) == 3
        assert rows[0]["row_type"] == "current"
        assert rows[1]["row_type"] == "history"
        assert rows[2]["row_type"] == "history"

    def test_export_csv_history_omitted_by_default(self):
        metrics_service, _ = _service_with_history()

        metrics_service.capture_snapshot()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        rows = list(
            csv.DictReader(io.StringIO(export_service.export_csv()))
        )

        assert len(rows) == 1

    def test_export_csv_column_order_is_fixed(self):
        metrics_service = GovernanceIntegrityMetricsService()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        rendered = export_service.export_csv()

        header = rendered.splitlines()[0]

        assert header == (
            "row_type,captured_at,total_dispatches,"
            "successful_dispatches,failed_dispatches,"
            "retry_dispatches,average_duration_ms"
        )

    def test_export_csv_empty_history_yields_only_current_row(self):
        metrics_service, _ = _service_with_history()

        export_service = GovernanceIntegrityMetricsExportService(
            metrics_service
        )

        rows = list(
            csv.DictReader(
                io.StringIO(
                    export_service.export_csv(include_history=True)
                )
            )
        )

        assert len(rows) == 1
        assert rows[0]["row_type"] == "current"


class TestGovernanceIntegrityMetricsServiceExportServiceFactory:

    def test_export_service_returns_bound_export_service(self):
        metrics_service = GovernanceIntegrityMetricsService()

        metrics_service.record_success(10.0)

        export_service = metrics_service.export_service()

        assert isinstance(
            export_service, GovernanceIntegrityMetricsExportService
        )

        payload = export_service.export_dict()

        assert payload["metrics"]["successful_dispatches"] == 1
