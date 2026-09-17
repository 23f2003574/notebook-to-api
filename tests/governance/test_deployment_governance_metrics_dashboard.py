from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetricsService,
)
from backend.observability.deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricsAlertService,
)
from backend.observability.deployment_governance_metrics_dashboard import (
    GovernanceIntegrityMetricsDashboard,
    GovernanceIntegrityMetricsDashboardService,
)
from backend.observability.deployment_governance_metrics_repository import (
    InMemoryGovernanceIntegrityMetricsRepository,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestGovernanceIntegrityMetricsDashboard:

    def test_rejects_out_of_range_rate(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsDashboard(
                summary=GovernanceIntegrityMetricsService()
                .snapshot(),
                success_rate=101.0,
                failure_rate=0.0,
                retry_rate=0.0,
                active_alerts=0,
                last_updated=BASE_TIME,
            )

    def test_rejects_negative_active_alerts(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsDashboard(
                summary=GovernanceIntegrityMetricsService()
                .snapshot(),
                success_rate=0.0,
                failure_rate=0.0,
                retry_rate=0.0,
                active_alerts=-1,
                last_updated=BASE_TIME,
            )

    def test_rejects_naive_last_updated(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsDashboard(
                summary=GovernanceIntegrityMetricsService()
                .snapshot(),
                success_rate=0.0,
                failure_rate=0.0,
                retry_rate=0.0,
                active_alerts=0,
                last_updated=datetime(2026, 1, 1),
            )


class TestGovernanceIntegrityMetricsDashboardServiceOverview:

    def test_empty_dashboard(self):
        metrics_service = GovernanceIntegrityMetricsService()

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service, clock=lambda: BASE_TIME
        )

        dashboard = service.overview()

        assert dashboard.summary.total_dispatches == 0
        assert dashboard.success_rate == 0.0
        assert dashboard.failure_rate == 0.0
        assert dashboard.retry_rate == 0.0
        assert dashboard.active_alerts == 0
        assert dashboard.last_updated == BASE_TIME

    def test_populated_dashboard(self):
        metrics_service = GovernanceIntegrityMetricsService()

        metrics_service.record_success(100.0)
        metrics_service.record_success(200.0)
        metrics_service.record_failure(50.0)
        metrics_service.record_retry()

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service, clock=lambda: BASE_TIME
        )

        dashboard = service.overview()

        assert dashboard.summary.total_dispatches == 3
        assert dashboard.summary.successful_dispatches == 2
        assert dashboard.summary.failed_dispatches == 1

    def test_percentage_calculations(self):
        metrics_service = GovernanceIntegrityMetricsService()

        for _ in range(7):
            metrics_service.record_success(10.0)

        for _ in range(3):
            metrics_service.record_failure(10.0)

        for _ in range(2):
            metrics_service.record_retry()

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service, clock=lambda: BASE_TIME
        )

        dashboard = service.overview()

        assert dashboard.success_rate == 70.0
        assert dashboard.failure_rate == 30.0
        assert dashboard.retry_rate == 20.0

    def test_percentages_rounded_to_two_decimals(self):
        metrics_service = GovernanceIntegrityMetricsService()

        for _ in range(2):
            metrics_service.record_success(10.0)

        metrics_service.record_failure(10.0)

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service, clock=lambda: BASE_TIME
        )

        dashboard = service.overview()

        # 2/3 = 66.666...% -> rounded to 66.67
        assert dashboard.success_rate == 66.67
        # 1/3 = 33.333...% -> rounded to 33.33
        assert dashboard.failure_rate == 33.33

    def test_zero_dispatch_case_avoids_division_by_zero(self):
        metrics_service = GovernanceIntegrityMetricsService()

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service, clock=lambda: BASE_TIME
        )

        dashboard = service.overview()

        assert dashboard.success_rate == 0.0
        assert dashboard.failure_rate == 0.0
        assert dashboard.retry_rate == 0.0

    def test_active_alerts_count_reflected(self):
        metrics_service = GovernanceIntegrityMetricsService()
        alert_service = GovernanceIntegrityMetricsAlertService()

        for _ in range(9):
            metrics_service.record_failure(10.0)

        metrics_service.record_success(10.0)

        alert_service.evaluate(metrics_service.snapshot())

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service,
            alert_service=alert_service,
            clock=lambda: BASE_TIME,
        )

        dashboard = service.overview()

        assert dashboard.active_alerts >= 1

    def test_active_alerts_zero_without_alert_service(self):
        metrics_service = GovernanceIntegrityMetricsService()

        for _ in range(9):
            metrics_service.record_failure(10.0)

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service, clock=lambda: BASE_TIME
        )

        dashboard = service.overview()

        assert dashboard.active_alerts == 0


class TestGovernanceIntegrityMetricsDashboardServiceSummary:

    def test_summary_returns_raw_metrics(self):
        metrics_service = GovernanceIntegrityMetricsService()

        metrics_service.record_success(10.0)

        service = GovernanceIntegrityMetricsDashboardService(
            metrics_service
        )

        summary = service.summary()

        assert summary == metrics_service.snapshot()


class TestGovernanceIntegrityMetricsDashboardServiceRefresh:

    def test_refresh_resyncs_from_repository(self):
        repository = InMemoryGovernanceIntegrityMetricsRepository()

        writer_service = GovernanceIntegrityMetricsService(repository)
        writer_service.record_success(10.0)
        writer_service.record_failure(20.0)

        reader_service = GovernanceIntegrityMetricsService(
            repository, auto_flush_enabled=False
        )

        dashboard_service = GovernanceIntegrityMetricsDashboardService(
            reader_service, clock=lambda: BASE_TIME
        )

        # Before refresh, the reader's in-memory state is still
        # empty: it never loaded from the shared repository.
        assert dashboard_service.overview().summary.total_dispatches == 0

        dashboard = dashboard_service.refresh()

        assert dashboard.summary.total_dispatches == 2

    def test_refresh_reevaluates_alerts(self):
        repository = InMemoryGovernanceIntegrityMetricsRepository()

        writer_service = GovernanceIntegrityMetricsService(repository)

        for _ in range(9):
            writer_service.record_failure(10.0)

        writer_service.record_success(10.0)

        reader_service = GovernanceIntegrityMetricsService(
            repository, auto_flush_enabled=False
        )

        alert_service = GovernanceIntegrityMetricsAlertService()

        dashboard_service = GovernanceIntegrityMetricsDashboardService(
            reader_service,
            alert_service=alert_service,
            clock=lambda: BASE_TIME,
        )

        dashboard = dashboard_service.refresh()

        assert dashboard.active_alerts >= 1
