from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
)
from backend.observability.deployment_governance_metrics_aggregation import (
    GovernanceIntegrityMetricsAggregate,
    GovernanceIntegrityMetricsAggregationService,
)
from backend.observability.deployment_governance_metrics_history import (
    GovernanceIntegrityMetricsSnapshot,
    InMemoryGovernanceIntegrityMetricsHistoryRepository,
)

BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _metrics(
    total, successful, failed, retries, average_duration_ms
) -> GovernanceIntegrityMetrics:
    return GovernanceIntegrityMetrics(
        total_dispatches=total,
        successful_dispatches=successful,
        failed_dispatches=failed,
        retry_dispatches=retries,
        average_duration_ms=average_duration_ms,
    )


def _repository_with_snapshots(
    entries,
) -> InMemoryGovernanceIntegrityMetricsHistoryRepository:
    repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

    for offset_seconds, metrics in entries:
        repository.append(
            GovernanceIntegrityMetricsSnapshot(
                captured_at=(
                    BASE_TIME + timedelta(seconds=offset_seconds)
                ),
                metrics=metrics,
            )
        )

    return repository


class TestGovernanceIntegrityMetricsAggregate:

    def test_rejects_naive_datetimes(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsAggregate(
                start=datetime(2026, 1, 1),
                end=BASE_TIME,
                dispatches=0,
                successes=0,
                failures=0,
                retries=0,
                average_duration_ms=0.0,
            )

    def test_rejects_end_before_start(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsAggregate(
                start=BASE_TIME,
                end=BASE_TIME - timedelta(seconds=1),
                dispatches=0,
                successes=0,
                failures=0,
                retries=0,
                average_duration_ms=0.0,
            )

    def test_rejects_successes_failures_mismatch(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsAggregate(
                start=BASE_TIME,
                end=BASE_TIME,
                dispatches=5,
                successes=1,
                failures=1,
                retries=0,
                average_duration_ms=0.0,
            )


class TestGovernanceIntegrityMetricsAggregationServiceBetween:

    def test_empty_history_yields_zero_aggregate(self):
        repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        aggregate = service.between(
            BASE_TIME, BASE_TIME + timedelta(hours=1)
        )

        assert aggregate.dispatches == 0
        assert aggregate.successes == 0
        assert aggregate.failures == 0
        assert aggregate.retries == 0
        assert aggregate.average_duration_ms == 0.0

    def test_single_snapshot_from_zero_baseline(self):
        # Captured a moment after the window's start, so the window
        # start itself has no snapshot at or before it and correctly
        # falls back to a zero baseline.
        repository = _repository_with_snapshots(
            [
                (1, _metrics(3, 2, 1, 1, 90.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        aggregate = service.between(
            BASE_TIME, BASE_TIME + timedelta(hours=1)
        )

        assert aggregate.dispatches == 3
        assert aggregate.successes == 2
        assert aggregate.failures == 1
        assert aggregate.retries == 1
        assert aggregate.average_duration_ms == 90.0

    def test_weighted_average_between_two_snapshots(self):
        # First snapshot: 2 dispatches averaging 100ms (total 200ms).
        # Second snapshot: 5 dispatches total, averaging 160ms
        # overall (total 800ms) -> the 3 new dispatches in this
        # window contributed (800 - 200) / 3 = 200ms average.
        repository = _repository_with_snapshots(
            [
                (0, _metrics(2, 2, 0, 0, 100.0)),
                (60, _metrics(5, 5, 0, 0, 160.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        aggregate = service.between(
            BASE_TIME, BASE_TIME + timedelta(minutes=5)
        )

        assert aggregate.dispatches == 3
        assert aggregate.average_duration_ms == pytest.approx(200.0)

    def test_window_excludes_snapshots_outside_range(self):
        repository = _repository_with_snapshots(
            [
                (0, _metrics(1, 1, 0, 0, 50.0)),
                (3600, _metrics(4, 3, 1, 0, 70.0)),
                (7200, _metrics(9, 7, 2, 1, 80.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        aggregate = service.between(
            BASE_TIME + timedelta(seconds=3600),
            BASE_TIME + timedelta(seconds=7200),
        )

        assert aggregate.dispatches == 5
        assert aggregate.successes == 4
        assert aggregate.failures == 1
        assert aggregate.retries == 1

    def test_reset_discontinuity_treated_as_empty(self):
        repository = _repository_with_snapshots(
            [
                (0, _metrics(10, 8, 2, 1, 100.0)),
                (60, _metrics(2, 2, 0, 0, 50.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        aggregate = service.between(
            BASE_TIME, BASE_TIME + timedelta(minutes=5)
        )

        assert aggregate.dispatches == 0
        assert aggregate.average_duration_ms == 0.0


class TestGovernanceIntegrityMetricsAggregationServiceHourly:

    def test_hourly_buckets_are_chronological(self):
        repository = _repository_with_snapshots(
            [
                (0, _metrics(1, 1, 0, 0, 10.0)),
                (3600, _metrics(3, 3, 0, 0, 20.0)),
                (7200, _metrics(6, 6, 0, 0, 30.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        buckets = service.hourly()

        assert len(buckets) == 2
        assert buckets[0].start < buckets[1].start
        assert buckets[0].dispatches == 2
        assert buckets[1].dispatches == 3

    def test_hourly_ignores_empty_intervals(self):
        repository = _repository_with_snapshots(
            [
                (0, _metrics(1, 1, 0, 0, 10.0)),
                # Nothing new happens for hours, then a burst.
                (3 * 3600, _metrics(4, 4, 0, 0, 40.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        buckets = service.hourly()

        # The first snapshot sits exactly at the range start, so it
        # becomes the baseline rather than an in-window delta; only
        # the final hour (3 new dispatches) has any activity, so the
        # two empty hours in between are dropped.
        assert len(buckets) == 1
        assert buckets[0].dispatches == 3

    def test_hourly_respects_explicit_range(self):
        repository = _repository_with_snapshots(
            [
                (0, _metrics(1, 1, 0, 0, 10.0)),
                (3600, _metrics(3, 3, 0, 0, 20.0)),
                (7200, _metrics(6, 6, 0, 0, 30.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        buckets = service.hourly(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(seconds=3600),
        )

        assert len(buckets) == 1
        assert buckets[0].dispatches == 2

    def test_hourly_with_empty_history_returns_empty_tuple(self):
        repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        assert service.hourly() == ()


class TestGovernanceIntegrityMetricsAggregationServiceDaily:

    def test_daily_buckets_span_full_days(self):
        one_day = int(timedelta(days=1).total_seconds())

        repository = _repository_with_snapshots(
            [
                (0, _metrics(2, 2, 0, 0, 15.0)),
                (one_day, _metrics(5, 5, 0, 0, 25.0)),
                (2 * one_day, _metrics(9, 9, 0, 0, 35.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        buckets = service.daily()

        assert len(buckets) == 2
        assert buckets[0].dispatches == 3
        assert buckets[1].dispatches == 4
        assert (buckets[1].start - buckets[0].start) == timedelta(
            days=1
        )

    def test_daily_with_empty_history_returns_empty_tuple(self):
        repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        assert service.daily() == ()


class TestGovernanceIntegrityMetricsAggregationServiceAggregate:

    def test_aggregate_rejects_non_positive_interval(self):
        repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        with pytest.raises(ValueError):
            service.aggregate(timedelta(0))

    def test_aggregate_rejects_end_before_start(self):
        repository = _repository_with_snapshots(
            [(0, _metrics(1, 1, 0, 0, 10.0))]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        with pytest.raises(ValueError):
            service.aggregate(
                timedelta(hours=1),
                start=BASE_TIME,
                end=BASE_TIME - timedelta(hours=1),
            )

    def test_aggregate_with_custom_interval(self):
        repository = _repository_with_snapshots(
            [
                (0, _metrics(1, 1, 0, 0, 10.0)),
                (900, _metrics(3, 3, 0, 0, 20.0)),
                (1800, _metrics(6, 6, 0, 0, 30.0)),
            ]
        )

        service = GovernanceIntegrityMetricsAggregationService(
            repository
        )

        buckets = service.aggregate(timedelta(minutes=15))

        # The range spans exactly two 15-minute intervals (0-900,
        # 900-1800); the snapshot at t=0 becomes the baseline for
        # the first bucket rather than a reportable delta.
        assert len(buckets) == 2
        assert [bucket.dispatches for bucket in buckets] == [2, 3]
