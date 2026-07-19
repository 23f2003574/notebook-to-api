import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_metrics_middleware import (
    GovernanceIntegrityMetricsMiddleware,
    GovernanceIntegrityRequestMetrics,
    GovernanceIntegrityRequestMetricsCollector,
)


class TestGovernanceIntegrityRequestMetrics:

    def test_rejects_mismatched_totals(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityRequestMetrics(
                total_requests=5,
                successful_requests=1,
                failed_requests=1,
                exceptions=0,
                average_latency_ms=0.0,
            )

    def test_rejects_negative_fields(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityRequestMetrics(
                total_requests=-1,
                successful_requests=-1,
                failed_requests=0,
                exceptions=0,
                average_latency_ms=0.0,
            )


class TestGovernanceIntegrityRequestMetricsCollector:

    def test_initial_snapshot_is_empty(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 0
        assert snapshot.average_latency_ms == 0.0

    def test_record_request_success(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        collector.record_request(200, 10.0)

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 1
        assert snapshot.successful_requests == 1
        assert snapshot.failed_requests == 0

    def test_record_request_failure(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        collector.record_request(500, 10.0)

        snapshot = collector.snapshot()

        assert snapshot.successful_requests == 0
        assert snapshot.failed_requests == 1

    def test_record_request_client_error_counts_as_failed(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        collector.record_request(404, 10.0)

        snapshot = collector.snapshot()

        assert snapshot.failed_requests == 1

    def test_record_exception(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        collector.record_exception(15.0)

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 1
        assert snapshot.failed_requests == 1
        assert snapshot.exceptions == 1

    def test_average_latency_recorded_across_requests(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        collector.record_request(200, 100.0)
        collector.record_request(200, 200.0)

        snapshot = collector.snapshot()

        assert snapshot.average_latency_ms == pytest.approx(150.0)

    def test_exception_latency_included_in_average(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        collector.record_request(200, 100.0)
        collector.record_exception(300.0)

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 2
        assert snapshot.average_latency_ms == pytest.approx(200.0)

    def test_negative_duration_rejected(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        with pytest.raises(ValueError):
            collector.record_request(200, -1.0)

        with pytest.raises(ValueError):
            collector.record_exception(-1.0)

    def test_reset_clears_counters(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        collector.record_request(200, 10.0)
        collector.record_exception(20.0)

        collector.reset()

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 0
        assert snapshot.average_latency_ms == 0.0


def _build_app(collector: GovernanceIntegrityRequestMetricsCollector) -> FastAPI:
    app = FastAPI()

    app.add_middleware(
        GovernanceIntegrityMetricsMiddleware, collector=collector
    )

    @app.get("/ok")
    async def ok():
        return {"status": "ok"}

    @app.get("/not-found-trigger")
    async def not_found_trigger():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="missing")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/health")
    async def api_health():
        return {"status": "healthy"}

    return app


class TestGovernanceIntegrityMetricsMiddleware:

    def test_successful_request_recorded(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        client = TestClient(_build_app(collector))

        response = client.get("/ok")

        assert response.status_code == 200

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 1
        assert snapshot.successful_requests == 1

    def test_failed_request_recorded(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        client = TestClient(_build_app(collector))

        response = client.get("/not-found-trigger")

        assert response.status_code == 404

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 1
        assert snapshot.failed_requests == 1
        assert snapshot.exceptions == 0

    def test_exception_handling_records_and_reraises(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        client = TestClient(
            _build_app(collector), raise_server_exceptions=False
        )

        response = client.get("/boom")

        assert response.status_code == 500

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 1
        assert snapshot.exceptions == 1
        assert snapshot.failed_requests == 1

    def test_latency_recorded_for_every_request(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        client = TestClient(_build_app(collector))

        client.get("/ok")
        client.get("/ok")

        snapshot = collector.snapshot()

        assert snapshot.average_latency_ms >= 0.0

    def test_excluded_health_endpoint_not_recorded(self):
        collector = GovernanceIntegrityRequestMetricsCollector()

        client = TestClient(_build_app(collector))

        client.get("/health")
        client.get("/api/health")

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 0

    def test_non_health_endpoints_still_recorded_alongside_excluded(
        self,
    ):
        collector = GovernanceIntegrityRequestMetricsCollector()

        client = TestClient(_build_app(collector))

        client.get("/health")
        client.get("/ok")

        snapshot = collector.snapshot()

        assert snapshot.total_requests == 1

    def test_default_collector_created_when_none_given(self):
        app = FastAPI()

        app.add_middleware(GovernanceIntegrityMetricsMiddleware)

        @app.get("/ok")
        async def ok():
            return {"status": "ok"}

        client = TestClient(app)

        response = client.get("/ok")

        assert response.status_code == 200
