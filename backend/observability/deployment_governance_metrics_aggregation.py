from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .deployment_governance_metrics import GovernanceIntegrityMetrics

if TYPE_CHECKING:
    from .deployment_governance_metrics_history import (
        GovernanceIntegrityMetricsHistoryRepository,
        GovernanceIntegrityMetricsSnapshot,
    )

_ZERO_METRICS = GovernanceIntegrityMetrics(
    total_dispatches=0,
    successful_dispatches=0,
    failed_dispatches=0,
    retry_dispatches=0,
    average_duration_ms=0.0,
)


@dataclass(frozen=True)
class GovernanceIntegrityMetricsAggregate:
    """
    Governance audit notification delivery activity that occurred
    within one inclusive time window, derived from the change
    between the running metrics captured at the start and end of the
    window.
    """

    start: datetime

    end: datetime

    dispatches: int

    successes: int

    failures: int

    retries: int

    average_duration_ms: float

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError(
                "start and end must be timezone-aware"
            )

        if self.end < self.start:
            raise ValueError(
                "end must not be before start"
            )

        non_negative_fields = (
            self.dispatches,
            self.successes,
            self.failures,
            self.retries,
        )

        if any(value < 0 for value in non_negative_fields):
            raise ValueError(
                "governance metrics aggregate counts must not be "
                "negative"
            )

        if self.successes + self.failures != self.dispatches:
            raise ValueError(
                "successes + failures must equal dispatches"
            )

        if self.average_duration_ms < 0:
            raise ValueError(
                "average_duration_ms must not be negative"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "dispatches": self.dispatches,
            "successes": self.successes,
            "failures": self.failures,
            "retries": self.retries,
            "average_duration_ms": self.average_duration_ms,
        }


class GovernanceIntegrityMetricsAggregationService:
    """
    Computes time-window aggregations over captured governance
    metrics history, for reporting.

    Each history snapshot holds the running (cumulative) counters at
    one point in time, not a per-interval delta. To report what
    happened within a specific window, this service takes the change
    between the last snapshot at or before the window's start and the
    last snapshot at or before its end, weighting the average
    duration by dispatch count so windows can be compared fairly
    regardless of how many snapshots fall inside them.
    """

    def __init__(
        self,
        history_repository: (
            "GovernanceIntegrityMetricsHistoryRepository"
        ),
    ) -> None:
        self._history_repository = history_repository

    def between(
        self,
        start: datetime,
        end: datetime,
    ) -> GovernanceIntegrityMetricsAggregate:
        """
        Aggregate governance metrics activity over one explicit,
        inclusive [start, end] window.
        """

        snapshots = self._chronological_snapshots()

        return self._window_aggregate(snapshots, start, end)

    def hourly(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[GovernanceIntegrityMetricsAggregate, ...]:
        """
        Aggregate governance metrics activity into consecutive
        1-hour windows, chronological, skipping windows with no
        activity.
        """

        return self.aggregate(
            timedelta(hours=1), start=start, end=end
        )

    def daily(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[GovernanceIntegrityMetricsAggregate, ...]:
        """
        Aggregate governance metrics activity into consecutive
        1-day windows, chronological, skipping windows with no
        activity.
        """

        return self.aggregate(
            timedelta(days=1), start=start, end=end
        )

    def aggregate(
        self,
        interval: timedelta,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[GovernanceIntegrityMetricsAggregate, ...]:
        """
        Aggregate governance metrics activity into consecutive
        windows of the given interval, chronological, skipping
        windows with no activity.

        When start/end are omitted, the range spans the full
        captured history. Returns an empty tuple when there is no
        history to aggregate.
        """

        if interval <= timedelta(0):
            raise ValueError(
                "interval must be greater than zero"
            )

        snapshots = self._chronological_snapshots()

        if not snapshots:
            return ()

        range_start = (
            start if start is not None else snapshots[0].captured_at
        )

        range_end = (
            end if end is not None else snapshots[-1].captured_at
        )

        if range_end < range_start:
            raise ValueError(
                "end must not be before start"
            )

        aggregates: list[GovernanceIntegrityMetricsAggregate] = []

        window_start = range_start

        while window_start <= range_end:
            window_end = min(
                window_start + interval, range_end
            )

            window = self._window_aggregate(
                snapshots, window_start, window_end
            )

            if window.dispatches > 0:
                aggregates.append(window)

            if window_end >= range_end:
                break

            window_start = window_start + interval

        return tuple(aggregates)

    def _chronological_snapshots(
        self,
    ) -> tuple["GovernanceIntegrityMetricsSnapshot", ...]:
        return tuple(
            sorted(
                self._history_repository.list(),
                key=lambda snapshot: snapshot.captured_at,
            )
        )

    @classmethod
    def _window_aggregate(
        cls,
        snapshots: tuple["GovernanceIntegrityMetricsSnapshot", ...],
        start: datetime,
        end: datetime,
    ) -> GovernanceIntegrityMetricsAggregate:
        baseline = cls._last_metrics_at_or_before(snapshots, start)
        final = cls._last_metrics_at_or_before(snapshots, end)

        dispatches = final.total_dispatches - baseline.total_dispatches
        successes = (
            final.successful_dispatches
            - baseline.successful_dispatches
        )
        failures = (
            final.failed_dispatches - baseline.failed_dispatches
        )
        retries = (
            final.retry_dispatches - baseline.retry_dispatches
        )

        # A discontinuity (e.g. the live counters were reset between
        # baseline and final) can make the delta negative. That is
        # not meaningful activity for this window, so it is reported
        # as empty rather than raised as an error.
        if (
            dispatches < 0
            or successes < 0
            or failures < 0
            or retries < 0
        ):
            dispatches = successes = failures = retries = 0

        if dispatches > 0:
            duration_total_end = (
                final.average_duration_ms * final.total_dispatches
            )

            duration_total_start = (
                baseline.average_duration_ms
                * baseline.total_dispatches
            )

            average_duration_ms = (
                duration_total_end - duration_total_start
            ) / dispatches

        else:
            average_duration_ms = 0.0

        return GovernanceIntegrityMetricsAggregate(
            start=start,
            end=end,
            dispatches=dispatches,
            successes=successes,
            failures=failures,
            retries=retries,
            average_duration_ms=average_duration_ms,
        )

    @staticmethod
    def _last_metrics_at_or_before(
        snapshots: tuple["GovernanceIntegrityMetricsSnapshot", ...],
        moment: datetime,
    ) -> GovernanceIntegrityMetrics:
        latest: GovernanceIntegrityMetrics = _ZERO_METRICS

        for snapshot in snapshots:
            if snapshot.captured_at > moment:
                break

            latest = snapshot.metrics

        return latest
