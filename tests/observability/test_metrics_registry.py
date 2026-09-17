import pytest

from backend.observability.metrics_registry import (
    MetricDefinition,
    MetricsRegistry,
)


@pytest.fixture
def registry():
    return MetricsRegistry()


class TestRegisterMetric:
    def test_register_counter(self, registry):
        definition = registry.register_metric(
            MetricDefinition(name="requests_total", metric_type="counter")
        )

        assert definition.name == "requests_total"
        assert definition in registry.list_metrics()

    def test_register_gauge_with_labels(self, registry):
        definition = registry.register_metric(
            MetricDefinition(
                name="queue_depth",
                metric_type="gauge",
                labels=["queue"],
            )
        )

        assert definition.labels == ["queue"]

    def test_register_histogram_requires_type(self, registry):
        registry.register_metric(
            MetricDefinition(
                name="request_latency_seconds",
                metric_type="histogram",
                histogram_buckets=[0.1, 0.5, 1.0],
            )
        )

        [definition] = registry.list_metrics()
        assert definition.histogram_buckets == [0.1, 0.5, 1.0]

    def test_register_rejects_invalid_type(self, registry):
        with pytest.raises(ValueError):
            registry.register_metric(
                MetricDefinition(name="bad_metric", metric_type="summary")
            )

    def test_register_duplicate_raises(self, registry):
        registry.register_metric(
            MetricDefinition(name="requests_total", metric_type="counter")
        )

        with pytest.raises(ValueError):
            registry.register_metric(
                MetricDefinition(name="requests_total", metric_type="counter")
            )


class TestRecord:
    def test_record_sample(self, registry):
        registry.register_metric(
            MetricDefinition(name="requests_total", metric_type="counter")
        )

        sample = registry.record("requests_total", 1)

        assert sample.metric_name == "requests_total"
        assert sample.value == 1

    def test_record_with_labels(self, registry):
        registry.register_metric(
            MetricDefinition(
                name="requests_total",
                metric_type="counter",
                labels=["method"],
            )
        )

        sample = registry.record("requests_total", 1, labels={"method": "GET"})

        assert sample.labels == {"method": "GET"}

    def test_record_rejects_unregistered_metric(self, registry):
        with pytest.raises(KeyError):
            registry.record("unknown_metric", 1)

    def test_record_rejects_unexpected_label(self, registry):
        registry.register_metric(
            MetricDefinition(name="requests_total", metric_type="counter")
        )

        with pytest.raises(ValueError):
            registry.record("requests_total", 1, labels={"method": "GET"})


class TestQuery:
    def test_query_returns_all_samples(self, registry):
        registry.register_metric(
            MetricDefinition(name="requests_total", metric_type="counter")
        )
        registry.record("requests_total", 1)
        registry.record("requests_total", 2)

        samples = registry.query("requests_total")

        assert [s.value for s in samples] == [1, 2]

    def test_query_filters_by_labels(self, registry):
        registry.register_metric(
            MetricDefinition(
                name="requests_total",
                metric_type="counter",
                labels=["method"],
            )
        )
        registry.record("requests_total", 1, labels={"method": "GET"})
        registry.record("requests_total", 1, labels={"method": "POST"})

        samples = registry.query("requests_total", labels={"method": "GET"})

        assert len(samples) == 1
        assert samples[0].labels == {"method": "GET"}

    def test_query_rejects_unregistered_metric(self, registry):
        with pytest.raises(KeyError):
            registry.query("unknown_metric")


class TestListMetrics:
    def test_list_metrics_empty(self, registry):
        assert registry.list_metrics() == []

    def test_list_metrics_returns_registered(self, registry):
        registry.register_metric(
            MetricDefinition(name="requests_total", metric_type="counter")
        )
        registry.register_metric(
            MetricDefinition(name="queue_depth", metric_type="gauge")
        )

        names = {definition.name for definition in registry.list_metrics()}

        assert names == {"requests_total", "queue_depth"}
