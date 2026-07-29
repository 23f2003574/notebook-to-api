import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_analytics import (
    MetricType,
    PluginAnalyticsService,
    PluginMetrics,
    PluginTrend,
    get_plugin_analytics_service,
    router as plugin_analytics_router,
)

DAY_1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
DAY_2 = DAY_1 + timedelta(days=1)


@pytest.fixture
def service() -> PluginAnalyticsService:
    return PluginAnalyticsService()


@pytest.fixture
def client(service: PluginAnalyticsService) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_analytics_router)
    app.dependency_overrides[get_plugin_analytics_service] = lambda: service
    return TestClient(app)


def test_record_creates_metric(service: PluginAnalyticsService):
    metric = service.record("csv-exporter", MetricType.INSTALL, timestamp=DAY_1)

    assert isinstance(metric, PluginMetrics)
    assert metric.plugin == "csv-exporter"
    assert metric.metric_type == MetricType.INSTALL


def test_record_accepts_plain_string_metric_type(service: PluginAnalyticsService):
    metric = service.record("csv-exporter", "load", value=0.2)

    assert metric.metric_type == MetricType.LOAD


def test_record_rejects_unknown_metric_type(service: PluginAnalyticsService):
    with pytest.raises(ValueError):
        service.record("csv-exporter", "not-a-real-metric")


def test_list_records_filters_by_plugin(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL)
    service.record("json-exporter", MetricType.INSTALL)

    records = service.list_records(plugin="csv-exporter")

    assert [record.plugin for record in records] == ["csv-exporter"]


def test_summary_counts_installs(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL)
    service.record("csv-exporter", MetricType.INSTALL)
    service.record("json-exporter", MetricType.INSTALL)

    summary = service.summary()

    assert summary["install_count"] == 3


def test_summary_counts_active_plugins_by_distinct_activation(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.ACTIVATION)
    service.record("csv-exporter", MetricType.ACTIVATION)
    service.record("json-exporter", MetricType.ACTIVATION)

    summary = service.summary()

    assert summary["active_plugins"] == 2


def test_summary_computes_average_load_time(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.LOAD, value=0.1)
    service.record("csv-exporter", MetricType.LOAD, value=0.3)

    summary = service.summary()

    assert summary["average_load_time"] == pytest.approx(0.2)


def test_summary_average_load_time_is_none_when_no_load_metrics(service: PluginAnalyticsService):
    summary = service.summary()

    assert summary["average_load_time"] is None


def test_summary_computes_execution_count_and_failure_rate(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.EXECUTION, success=True)
    service.record("csv-exporter", MetricType.EXECUTION, success=True)
    service.record("csv-exporter", MetricType.EXECUTION, success=False)

    summary = service.summary()

    assert summary["execution_count"] == 3
    assert summary["failure_rate"] == pytest.approx(1 / 3)


def test_summary_failure_rate_is_zero_with_no_executions(service: PluginAnalyticsService):
    summary = service.summary()

    assert summary["failure_rate"] == 0.0


def test_summary_filters_by_plugin(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL)
    service.record("json-exporter", MetricType.INSTALL)
    service.record("json-exporter", MetricType.INSTALL)

    summary = service.summary(plugin="json-exporter")

    assert summary["install_count"] == 2


def test_trends_buckets_by_day(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.EXECUTION, timestamp=DAY_1)
    service.record("csv-exporter", MetricType.EXECUTION, timestamp=DAY_1 + timedelta(hours=2))
    service.record("csv-exporter", MetricType.EXECUTION, timestamp=DAY_2)

    trends = service.trends(metric_type=MetricType.EXECUTION, bucket="day")

    assert isinstance(trends[0], PluginTrend)
    counts = {trend.bucket: trend.count for trend in trends}
    assert counts == {"2026-07-01": 2, "2026-07-02": 1}


def test_trends_computes_average_value_per_bucket(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.LOAD, value=0.1, timestamp=DAY_1)
    service.record("csv-exporter", MetricType.LOAD, value=0.3, timestamp=DAY_1 + timedelta(hours=1))

    trends = service.trends(metric_type=MetricType.LOAD, bucket="day")

    assert trends[0].average_value == pytest.approx(0.2)


def test_trends_rejects_unsupported_bucket(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL, timestamp=DAY_1)

    with pytest.raises(ValueError):
        service.trends(bucket="fortnight")


def test_trends_filters_by_plugin(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL, timestamp=DAY_1)
    service.record("json-exporter", MetricType.INSTALL, timestamp=DAY_1)

    trends = service.trends(plugin="csv-exporter")

    assert all(trend.plugin == "csv-exporter" for trend in trends)
    assert sum(trend.count for trend in trends) == 1


def test_export_json_includes_summary_trends_and_records(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL, timestamp=DAY_1)

    report = service.export()

    assert set(report.keys()) == {"summary", "trends", "records"}
    assert report["summary"]["install_count"] == 1
    assert len(report["records"]) == 1


def test_export_csv_produces_parseable_csv(service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.EXECUTION, value=1.5, success=True, timestamp=DAY_1)

    csv_text = service.export(format="csv")

    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 1
    assert rows[0]["plugin"] == "csv-exporter"
    assert rows[0]["metric_type"] == "execution"


def test_export_rejects_unknown_format(service: PluginAnalyticsService):
    with pytest.raises(ValueError):
        service.export(format="xml")


def test_api_list_metrics(client: TestClient, service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL, timestamp=DAY_1)

    response = client.get("/plugins/analytics")

    assert response.status_code == 200
    assert response.json()[0]["plugin"] == "csv-exporter"


def test_api_list_metrics_invalid_metric_type_returns_422(client: TestClient):
    response = client.get("/plugins/analytics", params={"metric_type": "bogus"})

    assert response.status_code == 422


def test_api_summary(client: TestClient, service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.INSTALL, timestamp=DAY_1)

    response = client.get("/plugins/analytics/summary")

    assert response.status_code == 200
    assert response.json()["install_count"] == 1


def test_api_trends(client: TestClient, service: PluginAnalyticsService):
    service.record("csv-exporter", MetricType.EXECUTION, timestamp=DAY_1)

    response = client.get("/plugins/analytics/trends", params={"metric_type": "execution"})

    assert response.status_code == 200
    assert response.json()[0]["count"] == 1


def test_api_trends_invalid_bucket_returns_422(client: TestClient):
    response = client.get("/plugins/analytics/trends", params={"bucket": "fortnight"})

    assert response.status_code == 422
