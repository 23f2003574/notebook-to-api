from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.inference_analytics import (
    InferenceAnalyticsService,
    InferenceMetrics,
    get_inference_analytics_service,
    router as inference_analytics_router,
)


@pytest.fixture
def service() -> InferenceAnalyticsService:
    return InferenceAnalyticsService()


@pytest.fixture
def client(service: InferenceAnalyticsService) -> TestClient:
    app = FastAPI()
    app.include_router(inference_analytics_router)
    app.dependency_overrides[get_inference_analytics_service] = lambda: service
    return TestClient(app)


def test_record_creates_metric(service: InferenceAnalyticsService):
    record = service.record("gpt-a", "success", 100.0, 20)

    assert isinstance(record, InferenceMetrics)
    assert record.model_name == "gpt-a"
    assert record.token_count == 20


def test_list_records_filters_by_model(service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)
    service.record("gpt-b", "success", 50.0, 5)

    listed = service.list_records("gpt-a")

    assert len(listed) == 1
    assert listed[0].model_name == "gpt-a"


def test_summary_computes_request_count_and_success_rate(service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)
    service.record("gpt-a", "failure", 200.0, 0)

    summary = service.summary("gpt-a")

    assert summary["request_count"] == 2
    assert summary["success_rate"] == pytest.approx(0.5)
    assert summary["average_latency_ms"] == pytest.approx(150.0)


def test_summary_computes_token_throughput(service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 1000.0, 100)

    summary = service.summary("gpt-a")

    assert summary["token_throughput_per_second"] == pytest.approx(100.0)


def test_summary_computes_model_utilization_when_unfiltered(service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)
    service.record("gpt-a", "success", 100.0, 10)
    service.record("gpt-b", "success", 100.0, 10)

    summary = service.summary()

    assert summary["model_utilization"]["gpt-a"] == pytest.approx(2 / 3)
    assert summary["model_utilization"]["gpt-b"] == pytest.approx(1 / 3)


def test_summary_with_no_records_has_safe_defaults(service: InferenceAnalyticsService):
    summary = service.summary("does-not-exist")

    assert summary["request_count"] == 0
    assert summary["success_rate"] == 0.0
    assert summary["average_latency_ms"] is None
    assert summary["token_throughput_per_second"] is None


def test_trends_buckets_by_day(service: InferenceAnalyticsService):
    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day2 = day1 + timedelta(days=1)
    service.record("gpt-a", "success", 100.0, 10, recorded_at=day1)
    service.record("gpt-a", "success", 200.0, 10, recorded_at=day1)
    service.record("gpt-a", "failure", 300.0, 0, recorded_at=day2)

    trends = service.trends("gpt-a", bucket="day")

    assert len(trends) == 2
    assert trends[0].request_count == 2
    assert trends[0].success_rate == pytest.approx(1.0)
    assert trends[1].request_count == 1
    assert trends[1].success_rate == pytest.approx(0.0)


def test_trends_rejects_unsupported_bucket(service: InferenceAnalyticsService):
    with pytest.raises(ValueError):
        service.trends(bucket="week")


def test_export_json_includes_summary_trends_and_records(service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)

    exported = service.export("gpt-a")

    assert "summary" in exported
    assert "trends" in exported
    assert len(exported["records"]) == 1


def test_export_csv_returns_string_with_header(service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)

    exported = service.export("gpt-a", format="csv")

    assert isinstance(exported, str)
    assert "model_name" in exported.splitlines()[0]


def test_export_unsupported_format_raises(service: InferenceAnalyticsService):
    with pytest.raises(ValueError):
        service.export(format="xml")


def test_api_list_metrics(client: TestClient, service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)

    response = client.get("/ai/analytics")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_summary(client: TestClient, service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)

    response = client.get("/ai/analytics/summary", params={"model_name": "gpt-a"})

    assert response.status_code == 200
    assert response.json()["request_count"] == 1


def test_api_trends(client: TestClient, service: InferenceAnalyticsService):
    service.record("gpt-a", "success", 100.0, 10)

    response = client.get("/ai/analytics/trends", params={"model_name": "gpt-a"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_trends_invalid_bucket_returns_422(client: TestClient):
    response = client.get("/ai/analytics/trends", params={"bucket": "week"})

    assert response.status_code == 422
