from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable

from .deployment_governance_metrics import GovernanceIntegrityMetrics

DEFAULT_FAILURE_RATE_THRESHOLD = 0.5

DEFAULT_RETRY_RATE_THRESHOLD = 0.5

DEFAULT_AVERAGE_LATENCY_THRESHOLD_MS = 5000.0


@dataclass(frozen=True)
class GovernanceIntegrityMetricAlert:
    """
    One registered alert's current state: whether the metric it
    watches currently crosses its configured threshold.
    """

    name: str

    triggered: bool

    value: float

    threshold: float

    triggered_at: datetime | None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if self.value < 0:
            raise ValueError(
                "value must not be negative"
            )

        if self.threshold < 0:
            raise ValueError(
                "threshold must not be negative"
            )

        if self.triggered:
            if self.triggered_at is None:
                raise ValueError(
                    "triggered_at must be set when triggered is True"
                )

            if self.triggered_at.tzinfo is None:
                raise ValueError(
                    "triggered_at must be timezone-aware"
                )

        else:
            if self.triggered_at is not None:
                raise ValueError(
                    "triggered_at must not be set when triggered "
                    "is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "triggered": self.triggered,
            "value": self.value,
            "threshold": self.threshold,
            "triggered_at": (
                None
                if self.triggered_at is None
                else self.triggered_at.isoformat()
            ),
        }


def _failure_rate(metrics: GovernanceIntegrityMetrics) -> float:
    if metrics.total_dispatches == 0:
        return 0.0

    return metrics.failed_dispatches / metrics.total_dispatches


def _retry_rate(metrics: GovernanceIntegrityMetrics) -> float:
    if metrics.total_dispatches == 0:
        return 0.0

    return metrics.retry_dispatches / metrics.total_dispatches


def _average_latency(metrics: GovernanceIntegrityMetrics) -> float:
    return metrics.average_duration_ms


class GovernanceIntegrityMetricsAlertService:
    """
    Watches governance audit notification delivery metrics against
    configurable thresholds and tracks which alerts are currently
    active.

    Registered alerts persist across evaluate() calls: an alert
    that is still triggered on a later evaluation keeps its original
    triggered_at rather than being reported as newly triggered every
    time, and an alert that stops being triggered is dropped from
    the active set.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        register_defaults: bool = True,
        failure_rate_threshold: float = (
            DEFAULT_FAILURE_RATE_THRESHOLD
        ),
        retry_rate_threshold: float = DEFAULT_RETRY_RATE_THRESHOLD,
        average_latency_threshold_ms: float = (
            DEFAULT_AVERAGE_LATENCY_THRESHOLD_MS
        ),
    ) -> None:
        self._lock = RLock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._definitions: dict[
            str, tuple[Callable[[GovernanceIntegrityMetrics], float], float]
        ] = {}

        self._active: dict[str, GovernanceIntegrityMetricAlert] = {}

        if register_defaults:
            self.register(
                "failure_rate", _failure_rate, failure_rate_threshold
            )

            self.register(
                "retry_rate", _retry_rate, retry_rate_threshold
            )

            self.register(
                "average_latency",
                _average_latency,
                average_latency_threshold_ms,
            )

    def register(
        self,
        name: str,
        metric: Callable[[GovernanceIntegrityMetrics], float],
        threshold: float,
    ) -> None:
        """
        Register a new alert: it triggers when metric(metrics)
        exceeds threshold. Raises ValueError if name is already
        registered.
        """

        if not name.strip():
            raise ValueError(
                "name must not be empty"
            )

        with self._lock:
            if name in self._definitions:
                raise ValueError(
                    f"alert '{name}' is already registered"
                )

            self._definitions[name] = (metric, threshold)

    def remove(self, name: str) -> None:
        """
        Unregister an alert and drop it from the active set. Raises
        KeyError if it is not registered.
        """

        with self._lock:
            if name not in self._definitions:
                raise KeyError(
                    f"alert '{name}' was not found"
                )

            del self._definitions[name]

            self._active.pop(name, None)

    def evaluate(
        self,
        metrics: GovernanceIntegrityMetrics,
    ) -> tuple[GovernanceIntegrityMetricAlert, ...]:
        """
        Re-check every registered alert against the latest metrics
        snapshot and update the active set accordingly.

        Returns every registered alert's freshly computed state,
        triggered or not (use active() for the "inactive alerts
        omitted" view). An alert that is still triggered keeps the
        triggered_at from when it first became active; a
        newly-triggered alert is stamped with the current time.
        """

        with self._lock:
            results = []

            for name, (metric, threshold) in self._definitions.items():
                value = float(metric(metrics))

                triggered = value > threshold

                if triggered:
                    previous = self._active.get(name)

                    triggered_at = (
                        previous.triggered_at
                        if previous is not None
                        else self._clock()
                    )

                    alert = GovernanceIntegrityMetricAlert(
                        name=name,
                        triggered=True,
                        value=value,
                        threshold=threshold,
                        triggered_at=triggered_at,
                    )

                    self._active[name] = alert

                else:
                    alert = GovernanceIntegrityMetricAlert(
                        name=name,
                        triggered=False,
                        value=value,
                        threshold=threshold,
                        triggered_at=None,
                    )

                    self._active.pop(name, None)

                results.append(alert)

            return tuple(results)

    def active(self) -> tuple[GovernanceIntegrityMetricAlert, ...]:
        """
        Return every currently active (triggered) alert. Alerts that
        are not currently triggered are omitted.
        """

        with self._lock:
            return tuple(self._active.values())

    def clear(self) -> None:
        """
        Dismiss every currently active alert. Registered alert
        definitions are unaffected: a condition that is still
        triggered will be reported as newly triggered on the next
        evaluate() call.
        """

        with self._lock:
            self._active.clear()
