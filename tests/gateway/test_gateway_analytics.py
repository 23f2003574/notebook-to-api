import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.gateway_analytics import (
    GatewayAnalytics,
    GatewayMetric,
    GatewayStatistics,
    UnknownRequestError,
    get_gateway_analytics,
    router as analytics_router,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def analytics(clock: FakeClock) -> GatewayAnalytics:
    return GatewayAnalytics(clock=clock)


@pytest.fixture
def client(analytics: GatewayAnalytics) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_router)
    app.dependency_overrides[get_gateway_analytics] = lambda: analytics
    return TestClient(app)


def test_record_request_returns_request_id(analytics: GatewayAnalytics):
    request_id = analytics.record_request("/notebooks", "GET")

    assert isinstance(request_id, str)
    assert request_id


def test_record_response_returns_metric(analytics: GatewayAnalytics, clock: FakeClock):
    request_id = analytics.record_request("/notebooks", "GET")
    clock.advance(0.05)

    metric = analytics.record_response(request_id, 200)

    assert isinstance(metric, GatewayMetric)
    assert metric.route == "/notebooks"
    assert metric.status_code == 200
    assert metric.latency_ms == pytest.approx(50.0)


def test_record_response_unknown_request_raises(analytics: GatewayAnalytics):
    with pytest.raises(UnknownRequestError):
        analytics.record_response("does-not-exist", 200)


def test_record_response_removes_from_pending(analytics: GatewayAnalytics):
    request_id = analytics.record_request("/notebooks", "GET")

    analytics.record_response(request_id, 200)

    with pytest.raises(UnknownRequestError):
        analytics.record_response(request_id, 200)


# --- metrics ---


def test_metrics_returns_recorded_entries(analytics: GatewayAnalytics):
    request_id = analytics.record_request("/notebooks", "GET")
    analytics.record_response(request_id, 200)

    metrics = analytics.metrics()

    assert len(metrics) == 1
    assert metrics[0].route == "/notebooks"


def test_metrics_empty_when_nothing_recorded(analytics: GatewayAnalytics):
    assert analytics.metrics() == []


# --- statistics ---


def test_compute_statistics_counts_requests_and_responses(analytics: GatewayAnalytics, clock: FakeClock):
    r1 = analytics.record_request("/notebooks", "GET")
    clock.advance(0.01)
    analytics.record_response(r1, 200)

    r2 = analytics.record_request("/notebooks", "POST")

    stats = analytics.compute_statistics()

    assert isinstance(stats, GatewayStatistics)
    assert stats.total_requests == 2
    assert stats.total_responses == 1
    assert stats.in_flight == 1


def test_compute_statistics_aggregates_status_codes(analytics: GatewayAnalytics):
    r1 = analytics.record_request("/notebooks", "GET")
    analytics.record_response(r1, 200)
    r2 = analytics.record_request("/notebooks", "GET")
    analytics.record_response(r2, 200)
    r3 = analytics.record_request("/notebooks", "GET")
    analytics.record_response(r3, 500)

    stats = analytics.compute_statistics()

    assert stats.status_code_counts == {"200": 2, "500": 1}


def test_compute_statistics_calculates_latency_bounds(analytics: GatewayAnalytics, clock: FakeClock):
    r1 = analytics.record_request("/notebooks", "GET")
    clock.advance(0.01)
    analytics.record_response(r1, 200)

    r2 = analytics.record_request("/notebooks", "GET")
    clock.advance(0.05)
    analytics.record_response(r2, 200)

    stats = analytics.compute_statistics()

    assert stats.min_latency_ms == pytest.approx(10.0)
    assert stats.max_latency_ms == pytest.approx(50.0)
    assert stats.average_latency_ms == pytest.approx(30.0)


def test_compute_statistics_reports_zero_latency_when_empty(analytics: GatewayAnalytics):
    stats = analytics.compute_statistics()

    assert stats.average_latency_ms == 0.0
    assert stats.min_latency_ms == 0.0
    assert stats.max_latency_ms == 0.0
    assert stats.throughput_per_second == 0.0


def test_compute_statistics_calculates_throughput(analytics: GatewayAnalytics, clock: FakeClock):
    r1 = analytics.record_request("/notebooks", "GET")
    analytics.record_response(r1, 200)

    clock.advance(1.0)

    r2 = analytics.record_request("/notebooks", "GET")
    analytics.record_response(r2, 200)

    stats = analytics.compute_statistics()

    assert stats.throughput_per_second == pytest.approx(2.0, rel=0.01)


# --- reset ---


def test_reset_clears_metrics_and_counters(analytics: GatewayAnalytics):
    request_id = analytics.record_request("/notebooks", "GET")
    analytics.record_response(request_id, 200)

    analytics.reset()

    assert analytics.metrics() == []
    stats = analytics.compute_statistics()
    assert stats.total_requests == 0
    assert stats.total_responses == 0
    assert stats.in_flight == 0


def test_reset_clears_pending_requests(analytics: GatewayAnalytics):
    analytics.record_request("/notebooks", "GET")

    analytics.reset()

    stats = analytics.compute_statistics()
    assert stats.in_flight == 0


# --- API ---


def test_api_analytics_returns_statistics(client: TestClient, analytics: GatewayAnalytics):
    request_id = analytics.record_request("/notebooks", "GET")
    analytics.record_response(request_id, 200)

    response = client.get("/gateway/analytics")

    assert response.status_code == 200
    body = response.json()
    assert body["total_responses"] == 1


def test_api_metrics_returns_recorded_entries(client: TestClient, analytics: GatewayAnalytics):
    request_id = analytics.record_request("/notebooks", "GET")
    analytics.record_response(request_id, 200)

    response = client.get("/gateway/analytics/metrics")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["route"] == "/notebooks"


def test_api_reset_clears_state(client: TestClient, analytics: GatewayAnalytics):
    request_id = analytics.record_request("/notebooks", "GET")
    analytics.record_response(request_id, 200)

    response = client.post("/gateway/analytics/reset")

    assert response.status_code == 200
    assert client.get("/gateway/analytics/metrics").json() == []
