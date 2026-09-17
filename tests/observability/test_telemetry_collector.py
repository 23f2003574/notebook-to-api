import pytest

from backend.observability.metrics_registry import MetricDefinition, MetricsRegistry
from backend.observability.telemetry_collector import (
    TelemetryCollector,
    TelemetryRecord,
)


@pytest.fixture
def collector():
    return TelemetryCollector()


class TestRecordIngestion:
    def test_collect_appends_to_buffer(self, collector):
        record = collector.collect("events", "user_signup", {"user_id": "u1"})

        assert record.source == "events"
        assert record.event_type == "user_signup"
        assert record in collector.snapshot()

    def test_collect_normalizes_missing_timestamp(self, collector):
        record = collector.collect("logs", "app_started")

        assert record.timestamp

    def test_collect_rejects_unknown_source(self, collector):
        with pytest.raises(ValueError):
            collector.collect("unknown_source", "some_event")

    def test_ingest_batch_of_records(self, collector):
        records = [
            TelemetryRecord(source="traces", event_type="span_start"),
            TelemetryRecord(source="traces", event_type="span_end"),
        ]

        batch = collector.ingest(records)

        assert len(batch.records) == 2
        assert batch.batch_id


class TestBatchCollection:
    def test_collect_metrics_pulls_from_registry(self, collector):
        registry = MetricsRegistry()
        registry.register_metric(
            MetricDefinition(name="requests_total", metric_type="counter")
        )
        registry.record("requests_total", 1)
        registry.record("requests_total", 2)

        records = collector.collect_metrics(registry)

        assert len(records) == 2
        assert all(record.source == "metrics" for record in records)
        assert [record.payload["value"] for record in records] == [1, 2]


class TestFlush:
    def test_flush_returns_and_clears_buffer(self, collector):
        collector.collect("events", "user_signup")
        collector.collect("events", "user_login")

        batch = collector.flush()

        assert len(batch.records) == 2
        assert collector.snapshot() == []

    def test_flush_empty_buffer_returns_empty_batch(self, collector):
        batch = collector.flush()

        assert batch.records == []
        assert batch.batch_id


class TestSnapshot:
    def test_snapshot_does_not_clear_buffer(self, collector):
        collector.collect("logs", "app_started")

        first_snapshot = collector.snapshot()
        second_snapshot = collector.snapshot()

        assert first_snapshot == second_snapshot
        assert len(collector.snapshot()) == 1

    def test_snapshot_returns_copy(self, collector):
        collector.collect("logs", "app_started")

        snapshot = collector.snapshot()
        snapshot.clear()

        assert len(collector.snapshot()) == 1
