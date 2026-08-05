import pytest

from backend.observability.metrics_storage import MetricsStorageEngine
from backend.observability.observability_analytics import ObservabilityAnalyticsService


@pytest.fixture
def service():
    return ObservabilityAnalyticsService()


class TestMetricAggregation:
    def test_record_creates_entry(self, service):
        entry = service.record("request_rate", 120)

        assert entry.metric_name == "request_rate"
        assert entry.value == 120

    def test_record_rejects_unknown_metric(self, service):
        with pytest.raises(ValueError):
            service.record("unknown_metric", 1)

    def test_record_writes_through_to_storage(self):
        storage = MetricsStorageEngine()
        service = ObservabilityAnalyticsService(storage_engine=storage)

        service.record("error_rate", 5, timestamp="2026-01-01T00:00:00+00:00")

        assert storage.values("error_rate") == [5]


class TestSummaryGeneration:
    def test_summary_averages_recorded_values(self, service):
        service.record("request_rate", 100)
        service.record("request_rate", 200)

        snapshot = service.summary()

        assert snapshot.metrics["request_rate"] == 150
        assert snapshot.generated_at

    def test_summary_omits_metrics_with_no_records(self, service):
        service.record("request_rate", 100)

        snapshot = service.summary()

        assert "error_rate" not in snapshot.metrics


class TestTrendComputation:
    def test_trends_returns_time_ordered_points(self, service):
        service.record("trace_latency", 10, timestamp="2026-01-01T00:00:00+00:00")
        service.record("trace_latency", 20, timestamp="2026-01-01T00:01:00+00:00")

        points = service.trends("trace_latency")

        assert [p["value"] for p in points] == [10, 20]

    def test_trends_rejects_unknown_metric(self, service):
        with pytest.raises(ValueError):
            service.trends("unknown_metric")

    def test_trends_without_records_raises(self, service):
        with pytest.raises(KeyError):
            service.trends("alert_count")


class TestAnalyticsExport:
    def test_export_includes_all_recorded_metrics(self, service):
        service.record("request_rate", 100, timestamp="2026-01-01T00:00:00+00:00")
        service.record("service_availability", 99.9, timestamp="2026-01-01T00:00:00+00:00")

        exported = service.export()

        assert exported["request_rate"] == [{"value": 100, "timestamp": "2026-01-01T00:00:00+00:00"}]
        assert "error_rate" not in exported

    def test_export_returns_empty_dict_when_nothing_recorded(self, service):
        assert service.export() == {}
