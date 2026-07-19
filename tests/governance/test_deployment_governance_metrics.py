import threading

import pytest

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
    GovernanceIntegrityMetricsService,
)


class TestGovernanceIntegrityMetrics:

    def test_valid_metrics(self):
        metrics = GovernanceIntegrityMetrics(
            total_dispatches=3,
            successful_dispatches=2,
            failed_dispatches=1,
            retry_dispatches=1,
            average_duration_ms=100.0,
        )

        assert metrics.total_dispatches == 3
        assert metrics.successful_dispatches == 2
        assert metrics.failed_dispatches == 1
        assert metrics.retry_dispatches == 1
        assert metrics.average_duration_ms == 100.0

    def test_negative_counts_raise_error(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetrics(
                total_dispatches=-1,
                successful_dispatches=-1,
                failed_dispatches=0,
                retry_dispatches=0,
                average_duration_ms=0.0,
            )

    def test_total_must_equal_success_plus_failure(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetrics(
                total_dispatches=5,
                successful_dispatches=2,
                failed_dispatches=1,
                retry_dispatches=0,
                average_duration_ms=0.0,
            )

    def test_negative_average_duration_raises_error(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetrics(
                total_dispatches=0,
                successful_dispatches=0,
                failed_dispatches=0,
                retry_dispatches=0,
                average_duration_ms=-1.0,
            )

    def test_to_dict(self):
        metrics = GovernanceIntegrityMetrics(
            total_dispatches=1,
            successful_dispatches=1,
            failed_dispatches=0,
            retry_dispatches=0,
            average_duration_ms=50.0,
        )

        assert metrics.to_dict() == {
            "total_dispatches": 1,
            "successful_dispatches": 1,
            "failed_dispatches": 0,
            "retry_dispatches": 0,
            "average_duration_ms": 50.0,
        }


class TestGovernanceIntegrityMetricsService:

    def test_initial_snapshot_is_empty(self):
        service = GovernanceIntegrityMetricsService()

        snapshot = service.snapshot()

        assert snapshot.total_dispatches == 0
        assert snapshot.successful_dispatches == 0
        assert snapshot.failed_dispatches == 0
        assert snapshot.retry_dispatches == 0
        assert snapshot.average_duration_ms == 0.0

    def test_record_success_increments_counters(self):
        service = GovernanceIntegrityMetricsService()

        service.record_success(100.0)

        snapshot = service.snapshot()

        assert snapshot.successful_dispatches == 1
        assert snapshot.failed_dispatches == 0
        assert snapshot.total_dispatches == 1
        assert snapshot.average_duration_ms == 100.0

    def test_record_failure_increments_counters(self):
        service = GovernanceIntegrityMetricsService()

        service.record_failure(50.0)

        snapshot = service.snapshot()

        assert snapshot.successful_dispatches == 0
        assert snapshot.failed_dispatches == 1
        assert snapshot.total_dispatches == 1
        assert snapshot.average_duration_ms == 50.0

    def test_record_retry_increments_counter(self):
        service = GovernanceIntegrityMetricsService()

        service.record_retry()
        service.record_retry()

        snapshot = service.snapshot()

        assert snapshot.retry_dispatches == 2
        assert snapshot.total_dispatches == 0

    def test_record_negative_duration_raises_error(self):
        service = GovernanceIntegrityMetricsService()

        with pytest.raises(ValueError):
            service.record_success(-1.0)

        with pytest.raises(ValueError):
            service.record_failure(-1.0)

    def test_average_duration_calculation(self):
        service = GovernanceIntegrityMetricsService()

        service.record_success(100.0)
        service.record_success(200.0)
        service.record_failure(300.0)

        snapshot = service.snapshot()

        assert snapshot.total_dispatches == 3
        assert snapshot.average_duration_ms == pytest.approx(200.0)

    def test_reset_clears_metrics(self):
        service = GovernanceIntegrityMetricsService()

        service.record_success(100.0)
        service.record_failure(50.0)
        service.record_retry()

        service.reset()

        snapshot = service.snapshot()

        assert snapshot.total_dispatches == 0
        assert snapshot.successful_dispatches == 0
        assert snapshot.failed_dispatches == 0
        assert snapshot.retry_dispatches == 0
        assert snapshot.average_duration_ms == 0.0

    def test_thread_safe_concurrent_recording(self):
        service = GovernanceIntegrityMetricsService()

        def record_many():
            for _ in range(200):
                service.record_success(10.0)

        threads = [
            threading.Thread(target=record_many) for _ in range(8)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        snapshot = service.snapshot()

        assert snapshot.successful_dispatches == 1600
        assert snapshot.total_dispatches == 1600
        assert snapshot.average_duration_ms == pytest.approx(10.0)
