import time

import pytest

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetricsService,
)
from backend.observability.deployment_governance_metrics_collector import (
    GovernanceIntegrityMetricsCollector,
)
from backend.observability.deployment_governance_metrics_history import (
    InMemoryGovernanceIntegrityMetricsHistoryRepository,
)


def _service_with_history():
    history_repository = (
        InMemoryGovernanceIntegrityMetricsHistoryRepository()
    )

    metrics_service = GovernanceIntegrityMetricsService(
        auto_flush_enabled=False,
        history_repository=history_repository,
    )

    return metrics_service, history_repository


class TestGovernanceIntegrityMetricsCollectorConstruction:

    def test_rejects_non_positive_interval(self):
        metrics_service = GovernanceIntegrityMetricsService()

        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsCollector(
                metrics_service, interval_seconds=0
            )

        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsCollector(
                metrics_service, interval_seconds=-1
            )

    def test_not_running_initially(self):
        metrics_service = GovernanceIntegrityMetricsService()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        assert collector.is_running() is False


class TestGovernanceIntegrityMetricsCollectorManualCollection:

    def test_collect_once_ignores_empty_metrics(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        result = collector.collect_once()

        assert result is None
        assert metrics_service.history() == ()

    def test_collect_once_captures_when_activity_exists(self):
        metrics_service, _ = _service_with_history()

        metrics_service.record_success(10.0)

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        result = collector.collect_once()

        assert result is not None
        assert result.metrics.successful_dispatches == 1
        assert len(metrics_service.history()) == 1

    def test_collect_once_can_be_called_repeatedly(self):
        metrics_service, _ = _service_with_history()

        metrics_service.record_success(10.0)

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        collector.collect_once()

        metrics_service.record_success(20.0)

        collector.collect_once()

        assert len(metrics_service.history()) == 2


class TestGovernanceIntegrityMetricsCollectorLifecycle:

    def test_start_and_stop(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.05
        )

        collector.start()

        assert collector.is_running() is True

        collector.stop()

        assert collector.is_running() is False

    def test_duplicate_start_raises_error(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=1.0
        )

        collector.start()

        try:
            with pytest.raises(RuntimeError, match="already running"):
                collector.start()

        finally:
            collector.stop()

    def test_stop_without_start_is_a_no_op(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        collector.stop()

        assert collector.is_running() is False

    def test_stop_is_idempotent(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.05
        )

        collector.start()
        collector.stop()
        collector.stop()

        assert collector.is_running() is False

    def test_stop_joins_the_background_thread(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.05
        )

        collector.start()
        collector.stop(timeout=2.0)

        assert collector._thread is None

    def test_restart_after_stop_succeeds(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.05
        )

        collector.start()
        collector.stop()

        collector.start()

        assert collector.is_running() is True

        collector.stop()


class TestGovernanceIntegrityMetricsCollectorPeriodicCollection:

    def test_periodic_collection_captures_snapshots_over_time(self):
        metrics_service, _ = _service_with_history()

        metrics_service.record_success(10.0)

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.05
        )

        collector.start()

        try:
            deadline = time.monotonic() + 2.0

            while (
                len(metrics_service.history()) < 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)

            assert len(metrics_service.history()) >= 2

        finally:
            collector.stop()

    def test_periodic_collection_skips_empty_intervals(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.05
        )

        collector.start()

        time.sleep(0.3)

        collector.stop()

        assert metrics_service.history() == ()

    def test_transient_collection_error_does_not_kill_thread(self):
        metrics_service, _ = _service_with_history()

        metrics_service.record_success(10.0)

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.05
        )

        call_count = {"n": 0}
        original_collect_once = collector.collect_once

        def flaky_collect_once():
            call_count["n"] += 1

            if call_count["n"] == 1:
                raise RuntimeError("transient failure")

            return original_collect_once()

        collector.collect_once = flaky_collect_once

        collector.start()

        try:
            deadline = time.monotonic() + 2.0

            while (
                call_count["n"] < 2 and time.monotonic() < deadline
            ):
                time.sleep(0.02)

            assert call_count["n"] >= 2
            assert collector.is_running() is True

        finally:
            collector.stop()
