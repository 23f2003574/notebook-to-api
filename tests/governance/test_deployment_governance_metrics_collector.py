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
from backend.observability.deployment_governance_metrics_retention import (
    GovernanceIntegrityMetricsRetentionService,
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


class TestGovernanceIntegrityMetricsCollectorRetentionIntegration:

    def test_retention_runs_after_successful_collection(self):
        metrics_service, history_repository = _service_with_history()

        for _ in range(5):
            metrics_service.record_success(10.0)
            metrics_service.capture_snapshot()

        retention_service = GovernanceIntegrityMetricsRetentionService(
            history_repository, max_age=None, max_entries=2
        )

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, retention_service=retention_service
        )

        metrics_service.record_success(20.0)

        collector.collect_once()

        # 5 pre-existing snapshots + 1 just captured = 6, pruned
        # down to the newest 2 by the retention service.
        assert len(metrics_service.history()) == 2

    def test_retention_not_run_when_collection_is_empty(self):
        metrics_service, history_repository = _service_with_history()

        retention_service = GovernanceIntegrityMetricsRetentionService(
            history_repository, max_age=None, max_entries=2
        )
        retention_service.prune = lambda: (_ for _ in ()).throw(
            AssertionError("prune() should not be called")
        )

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, retention_service=retention_service
        )

        result = collector.collect_once()

        assert result is None

    def test_collector_without_retention_service_does_not_prune(self):
        metrics_service, history_repository = _service_with_history()

        for _ in range(5):
            metrics_service.record_success(10.0)
            metrics_service.capture_snapshot()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        metrics_service.record_success(20.0)

        collector.collect_once()

        assert len(metrics_service.history()) == 6


class TestGovernanceIntegrityMetricsCollectorReconfigure:

    def test_reconfigure_interval_takes_effect(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=60.0
        )

        collector.reconfigure(interval_seconds=0.05)

        assert collector._interval_seconds == 0.05

    def test_reconfigure_rejects_non_positive_interval(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        with pytest.raises(ValueError):
            collector.reconfigure(interval_seconds=0)

        with pytest.raises(ValueError):
            collector.reconfigure(interval_seconds=-1)

    def test_reconfigure_replaces_retention_service(self):
        metrics_service, history_repository = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service
        )

        from backend.observability.deployment_governance_metrics_retention import (
            GovernanceIntegrityMetricsRetentionService,
        )

        retention_service = GovernanceIntegrityMetricsRetentionService(
            history_repository, max_age=None, max_entries=1
        )

        collector.reconfigure(retention_service=retention_service)

        for _ in range(3):
            metrics_service.record_success(10.0)
            metrics_service.capture_snapshot()

        metrics_service.record_success(20.0)
        collector.collect_once()

        assert len(metrics_service.history()) == 1

    def test_reconfigure_can_clear_retention_service(self):
        metrics_service, history_repository = _service_with_history()

        from backend.observability.deployment_governance_metrics_retention import (
            GovernanceIntegrityMetricsRetentionService,
        )

        retention_service = GovernanceIntegrityMetricsRetentionService(
            history_repository, max_age=None, max_entries=1
        )

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, retention_service=retention_service
        )

        collector.reconfigure(retention_service=None)

        for _ in range(3):
            metrics_service.record_success(10.0)
            metrics_service.capture_snapshot()

        metrics_service.record_success(20.0)
        collector.collect_once()

        assert len(metrics_service.history()) == 4

    def test_reconfigure_without_arguments_changes_nothing(self):
        metrics_service, _ = _service_with_history()

        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=42.0
        )

        collector.reconfigure()

        assert collector._interval_seconds == 42.0

    def test_reconfigure_while_running_is_picked_up(self):
        metrics_service, _ = _service_with_history()

        # Reconfigure only takes effect on the background thread's
        # *next* wait cycle, not by interrupting an in-progress one,
        # so the initial interval must be short enough for that next
        # cycle to arrive within this test's patience window.
        collector = GovernanceIntegrityMetricsCollector(
            metrics_service, interval_seconds=0.1
        )

        collector.start()

        try:
            collector.reconfigure(interval_seconds=0.05)

            metrics_service.record_success(10.0)

            deadline = time.monotonic() + 2.0

            while (
                len(metrics_service.history()) < 1
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)

            assert len(metrics_service.history()) >= 1

        finally:
            collector.stop()
