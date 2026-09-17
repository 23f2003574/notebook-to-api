import pytest

from backend.observability.log_aggregation import LogAggregationService
from backend.observability.telemetry_collector import TelemetryCollector


@pytest.fixture
def service():
    return LogAggregationService()


class TestLogIngestion:
    def test_ingest_creates_entry(self, service):
        entry = service.ingest("api", "info", "request handled")

        assert entry.source == "api"
        assert entry.severity == "info"
        assert entry.timestamp

    def test_ingest_rejects_unknown_source(self, service):
        with pytest.raises(ValueError):
            service.ingest("unknown_source", "info", "oops")

    def test_ingest_rejects_unknown_severity(self, service):
        with pytest.raises(ValueError):
            service.ingest("api", "verbose", "oops")

    def test_ingest_respects_retention_limit(self):
        limited_service = LogAggregationService(retention_limit=2)

        limited_service.ingest("api", "info", "first")
        limited_service.ingest("api", "info", "second")
        limited_service.ingest("api", "info", "third")

        assert [e.message for e in limited_service.query()] == ["second", "third"]

    def test_collect_logs_correlates_with_telemetry(self, service):
        collector = TelemetryCollector()
        service.ingest("workers", "error", "job failed")

        records = collector.collect_logs(service)

        assert len(records) == 1
        assert records[0].source == "logs"
        assert records[0].payload["message"] == "job failed"


class TestIndexedQueries:
    def test_query_filters_by_source(self, service):
        service.ingest("api", "info", "a")
        service.ingest("workers", "info", "b")

        results = service.query(source="workers")

        assert [e.message for e in results] == ["b"]

    def test_query_filters_by_severity(self, service):
        service.ingest("api", "info", "a")
        service.ingest("api", "error", "b")

        results = service.query(severity="error")

        assert [e.message for e in results] == ["b"]

    def test_query_filters_by_min_severity(self, service):
        service.ingest("api", "debug", "a")
        service.ingest("api", "warning", "b")
        service.ingest("api", "critical", "c")

        results = service.query(min_severity="warning")

        assert [e.message for e in results] == ["b", "c"]

    def test_query_excludes_archived_by_default(self, service):
        service.ingest("api", "info", "old", timestamp="2020-01-01T00:00:00+00:00")
        service.archive(before_timestamp="2021-01-01T00:00:00+00:00")

        assert service.query() == []
        assert len(service.query(include_archived=True)) == 1


class TestLiveTail:
    def test_tail_returns_most_recent_entries(self, service):
        service.ingest("api", "info", "one")
        service.ingest("api", "info", "two")
        service.ingest("api", "info", "three")

        tail = service.tail(2)

        assert [e.message for e in tail] == ["two", "three"]

    def test_tail_with_zero_returns_empty(self, service):
        service.ingest("api", "info", "one")

        assert service.tail(0) == []


class TestArchiveWorkflow:
    def test_archive_marks_matching_entries(self, service):
        service.ingest("api", "info", "old", timestamp="2020-01-01T00:00:00+00:00")
        service.ingest("api", "info", "new", timestamp="2030-01-01T00:00:00+00:00")

        batch = service.archive(before_timestamp="2025-01-01T00:00:00+00:00")

        assert [e.message for e in batch.entries] == ["old"]
        assert [e.message for e in service.query()] == ["new"]

    def test_archive_is_idempotent_for_already_archived_entries(self, service):
        service.ingest("api", "info", "old", timestamp="2020-01-01T00:00:00+00:00")

        first_batch = service.archive(before_timestamp="2025-01-01T00:00:00+00:00")
        second_batch = service.archive(before_timestamp="2025-01-01T00:00:00+00:00")

        assert len(first_batch.entries) == 1
        assert len(second_batch.entries) == 0
