import pytest

from backend.observability.metrics_storage import (
    MetricsStorageEngine,
    RetentionPolicy,
)
from backend.observability.telemetry_collector import TelemetryCollector


@pytest.fixture
def engine():
    return MetricsStorageEngine()


class TestMetricPersistence:
    def test_write_creates_series(self, engine):
        series = engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)

        assert series.metric_name == "cpu_usage"
        assert series.points == [{"timestamp": "2026-01-01T00:00:00+00:00", "value": 42}]

    def test_write_appends_to_existing_series(self, engine):
        engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 42)
        engine.write("cpu_usage", "2026-01-01T00:01:00+00:00", 50)

        points = engine.read("cpu_usage")

        assert [p["value"] for p in points] == [42, 50]

    def test_read_unknown_series_raises(self, engine):
        with pytest.raises(KeyError):
            engine.read("unknown_metric")

    def test_persist_metrics_from_telemetry_collector(self, engine):
        collector = TelemetryCollector()
        collector.collect(
            "metrics", "cpu_usage", {"value": 75}, timestamp="2026-01-01T00:00:00+00:00"
        )

        persisted = collector.persist_metrics(engine)

        assert len(persisted) == 1
        assert engine.read("cpu_usage") == [
            {"timestamp": "2026-01-01T00:00:00+00:00", "value": 75}
        ]


class TestRangeQueries:
    def test_read_filters_by_start(self, engine):
        engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 1)
        engine.write("cpu_usage", "2026-01-01T00:01:00+00:00", 2)

        points = engine.read("cpu_usage", start="2026-01-01T00:01:00+00:00")

        assert [p["value"] for p in points] == [2]

    def test_read_filters_by_end(self, engine):
        engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 1)
        engine.write("cpu_usage", "2026-01-01T00:01:00+00:00", 2)

        points = engine.read("cpu_usage", end="2026-01-01T00:00:00+00:00")

        assert [p["value"] for p in points] == [1]

    def test_read_filters_by_range(self, engine):
        engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 1)
        engine.write("cpu_usage", "2026-01-01T00:01:00+00:00", 2)
        engine.write("cpu_usage", "2026-01-01T00:02:00+00:00", 3)

        points = engine.read(
            "cpu_usage",
            start="2026-01-01T00:00:30+00:00",
            end="2026-01-01T00:01:30+00:00",
        )

        assert [p["value"] for p in points] == [2]


class TestCompaction:
    def test_compact_averages_points_into_buckets(self, engine):
        engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 10)
        engine.write("cpu_usage", "2026-01-01T00:01:00+00:00", 20)
        engine.write("cpu_usage", "2026-01-01T00:02:00+00:00", 30)
        engine.write("cpu_usage", "2026-01-01T00:03:00+00:00", 40)

        series = engine.compact("cpu_usage", bucket_size=2)

        assert [p["value"] for p in series.points] == [15, 35]
        assert series.storage_type == "aggregated"

    def test_compact_rejects_non_positive_bucket_size(self, engine):
        engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 10)

        with pytest.raises(ValueError):
            engine.compact("cpu_usage", bucket_size=0)

    def test_compact_unknown_series_raises(self, engine):
        with pytest.raises(KeyError):
            engine.compact("unknown_metric", bucket_size=2)


class TestRetentionCleanup:
    def test_retention_enforces_max_points(self):
        engine = MetricsStorageEngine(retention_policy=RetentionPolicy(max_points=2))

        engine.write("cpu_usage", "2026-01-01T00:00:00+00:00", 1)
        engine.write("cpu_usage", "2026-01-01T00:01:00+00:00", 2)
        engine.write("cpu_usage", "2026-01-01T00:02:00+00:00", 3)

        points = engine.read("cpu_usage")

        assert [p["value"] for p in points] == [2, 3]

    def test_retention_removes_points_older_than_max_age(self, engine):
        engine.write("cpu_usage", "2000-01-01T00:00:00+00:00", 1)
        engine.write("cpu_usage", "2099-01-01T00:00:00+00:00", 2)

        removed_counts = engine.retention(RetentionPolicy(max_age_seconds=3600))

        assert removed_counts["cpu_usage"] == 1
        assert [p["value"] for p in engine.read("cpu_usage")] == [2]

    def test_retention_returns_zero_when_nothing_removed(self, engine):
        engine.write("cpu_usage", "2099-01-01T00:00:00+00:00", 1)

        removed_counts = engine.retention(RetentionPolicy(max_age_seconds=3600))

        assert removed_counts["cpu_usage"] == 0
