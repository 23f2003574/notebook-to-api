from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_rollback import DeploymentRollbackEngine
    from .deployment_governance_rollout_health import (
        DeploymentRolloutHealthEngine,
    )
    from .deployment_governance_rollout_manager import (
        DeploymentRolloutManager,
    )
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
    )

_OUTCOMES: "tuple[str, ...]" = ("SUCCESS", "FAILURE")

_SOURCE = "rollout-analytics"


@dataclass(frozen=True)
class RolloutAnalyticsSnapshot:
    """
    A point-in-time global picture across every deployment's recorded
    rollout outcomes — unlike every other engine's per-deployment_id
    snapshot, this one aggregates across all of them.
    """

    generated_at: datetime

    successful_rollouts: int

    failed_rollouts: int

    average_duration_seconds: float

    rollback_rate: float

    def __post_init__(self) -> None:
        if self.successful_rollouts < 0:
            raise ValueError("successful_rollouts must be >= 0")

        if self.failed_rollouts < 0:
            raise ValueError("failed_rollouts must be >= 0")

        if self.average_duration_seconds < 0:
            raise ValueError("average_duration_seconds must be >= 0")

        if self.rollback_rate < 0:
            raise ValueError("rollback_rate must be >= 0")

        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "successful_rollouts": self.successful_rollouts,
            "failed_rollouts": self.failed_rollouts,
            "average_duration_seconds": self.average_duration_seconds,
            "rollback_rate": self.rollback_rate,
        }


@dataclass(frozen=True)
class RolloutTrend:
    """
    How one KPI has moved between its previously recorded value and
    its current one.
    """

    metric: str

    current: float

    previous: float

    change_percent: float

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("metric must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "current": self.current,
            "previous": self.previous,
            "change_percent": self.change_percent,
        }


@dataclass(frozen=True)
class _RolloutRecord:
    """
    Internal, immutable record of one rollout outcome — not part of
    the public model surface (only the aggregates derived from it,
    RolloutAnalyticsSnapshot and RolloutTrend, are); "analytics
    derived from immutable historical records" refers to this.
    """

    deployment_id: str

    outcome: str

    duration_seconds: float

    recorded_at: datetime


class DeploymentRolloutAnalytics:
    """
    Derives higher-level rollout insight (KPIs, trends, historical
    summaries) from the raw events every other governance rollout
    component already publishes, rather than tracking any deployment
    lifecycle of its own — unlike the Governance Metrics service
    (deployment_governance_metrics_*), which records raw request/
    delivery telemetry, this is purely a downstream aggregation layer.

    If an event_bus is wired in, this engine subscribes itself to
    "rollout_completed"/"rollout_failed" (recording a SUCCESS/FAILURE
    outcome, with duration resolved via rollout_manager), "rollback_
    completed" (counting toward rollback_rate), and "rollout_health_
    evaluated" (feeding health_score_trend) — the same event-driven
    shape DeploymentRollbackEngine and DeploymentRolloutHealthEngine
    already use, so this engine needs no direct reference to any of
    them to stay current. rollout_manager is still accepted directly
    (not just via events) because computing a rollout's duration
    needs its created_at, which the "rollout_completed"/"rollout_
    failed" event payloads don't carry.

    KPIs are computed over a rolling window (window_seconds; the
    default, None, uses the entire history) from three internal,
    append-only logs: outcome records, rollback timestamps, and health
    scores. register_kpi() adds a custom KPI (a zero-argument callable
    computing its own value from whatever data source the caller
    closes over) alongside the 8 built in; set_threshold() attaches a
    breach condition (built-in or custom) that publishes
    "rollout_kpi_threshold_exceeded" when crossed.

    Thread-safe: every mutation of the internal logs and KPI/snapshot
    history is guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        rollout_manager: "DeploymentRolloutManager | None" = None,
        health_engine: "DeploymentRolloutHealthEngine | None" = None,
        rollback_engine: "DeploymentRollbackEngine | None" = None,
        traffic_router: "DeploymentTrafficRouter | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
        window_seconds: "int | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._records: "list[_RolloutRecord]" = []

        self._rollback_timestamps: "list[datetime]" = []

        self._health_scores: "list[tuple[datetime, float]]" = []

        self._history: "list[RolloutAnalyticsSnapshot]" = []

        self._kpi_history: "dict[str, list[tuple[datetime, float]]]" = {}

        self._thresholds: "dict[str, tuple[float, str]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._rollout_manager = rollout_manager

        self._health_engine = health_engine

        self._rollback_engine = rollback_engine

        self._traffic_router = traffic_router

        self._metrics = metrics

        self._audit_service = audit_service

        self._window_seconds = window_seconds

        self._kpi_registry: "dict[str, Callable[[], float]]" = {
            "success_rate": self._kpi_success_rate,
            "failure_rate": self._kpi_failure_rate,
            "average_duration_seconds": self._kpi_average_duration,
            "rollback_rate": self._kpi_rollback_rate,
            "mttr_seconds": self._kpi_mttr,
            "mtbf_seconds": self._kpi_mtbf,
            "health_score_trend": self._kpi_health_score_trend,
            "deployment_frequency": self._kpi_deployment_frequency,
        }

        if self._event_bus is not None:
            self._event_bus.subscribe(
                "rollout_completed", self._on_rollout_completed
            )
            self._event_bus.subscribe(
                "rollout_failed", self._on_rollout_failed
            )
            self._event_bus.subscribe(
                "rollback_completed", self._on_rollback_completed
            )
            self._event_bus.subscribe(
                "rollout_health_evaluated", self._on_health_evaluated
            )

    def register_kpi(
        self, name: str, compute_fn: "Callable[[], float]"
    ) -> None:
        """
        Register (or override) a KPI. compute_fn takes no arguments —
        a custom KPI closes over whatever data source it needs itself,
        the same "strategy interface" shape as
        DeploymentTrafficRouter.register_strategy.
        """

        if not name:
            raise ValueError("name must not be empty")

        with self._lock:
            self._kpi_registry[name] = compute_fn

    def set_threshold(
        self, name: str, threshold: float, direction: str = "above"
    ) -> None:
        """
        Attach a breach condition to KPI name (built in or custom):
        publish "rollout_kpi_threshold_exceeded" whenever its value is
        above (direction="above") or below (direction="below")
        threshold, checked on every record() call.

        Raises ValueError if direction is not "above" or "below".
        """

        if direction not in ("above", "below"):
            raise ValueError(
                "direction must be 'above' or 'below'"
            )

        with self._lock:
            self._thresholds[name] = (threshold, direction)

    def record(
        self, deployment_id: str, outcome: str, duration_seconds: float
    ) -> RolloutAnalyticsSnapshot:
        """
        Record one rollout outcome and run the full analytics
        pipeline: recompute every KPI, store a fresh snapshot, and
        publish whatever events that pipeline run warrants.

        Raises ValueError if outcome is not "SUCCESS"/"FAILURE", or if
        duration_seconds is negative.
        """

        if outcome not in _OUTCOMES:
            raise ValueError(f"outcome must be one of {_OUTCOMES}")

        if duration_seconds < 0:
            raise ValueError("duration_seconds must not be negative")

        record = _RolloutRecord(
            deployment_id=deployment_id, outcome=outcome,
            duration_seconds=duration_seconds,
            recorded_at=self._clock(),
        )

        with self._lock:
            self._records.append(record)

        return self._run_pipeline()

    def snapshot(self) -> RolloutAnalyticsSnapshot:
        """
        Return a freshly computed global snapshot from the current
        windowed state (not the last stored one — see history() for
        that).
        """

        return self._compute_snapshot()

    def summary(self) -> "dict[str, float]":
        """
        Return every registered KPI's current value (built in and
        custom), freshly computed.
        """

        with self._lock:
            registry = dict(self._kpi_registry)

        return {name: compute_fn() for name, compute_fn in registry.items()}

    def trend(self, metric: str) -> RolloutTrend:
        """
        Compare metric's current value against the last value
        recorded for it by a prior record() call.

        Raises KeyError if metric is not a registered KPI. If no prior
        value has ever been recorded, previous equals current (0%
        change) — there is nothing to compare against yet.
        """

        with self._lock:
            compute_fn = self._kpi_registry.get(metric)

        if compute_fn is None:
            raise KeyError(f"metric '{metric}' is not a registered KPI")

        current = compute_fn()

        with self._lock:
            recorded = self._kpi_history.get(metric, ())

        # recorded[-1] is typically the value from the most recent
        # record() call — the same value `current` just recomputed,
        # assuming nothing has changed since. The point to compare
        # against is the one *before* that, recorded[-2]; with fewer
        # than two stored points, there is nothing meaningful before
        # "now" yet, so previous falls back to current (0% change).
        if len(recorded) >= 2:
            previous = recorded[-2][1]

        elif recorded:
            previous = recorded[-1][1]

        else:
            previous = current

        change_percent = (
            0.0 if previous == 0
            else round((current - previous) / abs(previous) * 100, 4)
        )

        return RolloutTrend(
            metric=metric, current=current, previous=previous,
            change_percent=change_percent,
        )

    def history(self) -> "tuple[RolloutAnalyticsSnapshot, ...]":
        """
        Return every snapshot ever stored by record()'s pipeline,
        oldest first.
        """

        with self._lock:
            return tuple(self._history)

    def export(self) -> "dict[str, object]":
        """
        Return a full dump: the current snapshot, every KPI's current
        value, and the complete snapshot history — plus, if a
        traffic_router is wired in, how many deployments currently
        have a routing configuration.
        """

        result: "dict[str, object]" = {
            "snapshot": self.snapshot().to_dict(),
            "kpis": self.summary(),
            "history": [
                snapshot.to_dict() for snapshot in self.history()
            ],
        }

        if self._traffic_router is not None:
            result["active_routing_configurations"] = len(
                self._traffic_router.list()
            )

        return result

    def reset(self) -> None:
        """
        Remove every recorded outcome, rollback timestamp, health
        score, and stored snapshot/KPI history. Registered KPIs and
        thresholds are left in place.
        """

        with self._lock:
            self._records.clear()
            self._rollback_timestamps.clear()
            self._health_scores.clear()
            self._history.clear()
            self._kpi_history.clear()

    def _windowed_records(self) -> "list[_RolloutRecord]":
        with self._lock:
            records = list(self._records)

        return self._within_window(records, key=lambda r: r.recorded_at)

    def _windowed_rollback_timestamps(self) -> "list[datetime]":
        with self._lock:
            timestamps = list(self._rollback_timestamps)

        return self._within_window(timestamps, key=lambda t: t)

    def _windowed_health_scores(self) -> "list[tuple[datetime, float]]":
        with self._lock:
            scores = list(self._health_scores)

        return self._within_window(scores, key=lambda entry: entry[0])

    def _within_window(
        self, items: "list[Any]", *, key: "Callable[[Any], datetime]"
    ) -> "list[Any]":
        if self._window_seconds is None:
            return items

        cutoff = self._clock() - timedelta(seconds=self._window_seconds)

        return [item for item in items if key(item) >= cutoff]

    def _kpi_success_rate(self) -> float:
        records = self._windowed_records()

        if not records:
            return 0.0

        return sum(
            1 for record in records if record.outcome == "SUCCESS"
        ) / len(records)

    def _kpi_failure_rate(self) -> float:
        records = self._windowed_records()

        if not records:
            return 0.0

        return sum(
            1 for record in records if record.outcome == "FAILURE"
        ) / len(records)

    def _kpi_average_duration(self) -> float:
        records = self._windowed_records()

        if not records:
            if self._metrics is not None:
                return self._metrics.summary().average_execution_ms / 1000.0

            return 0.0

        return sum(record.duration_seconds for record in records) / len(
            records
        )

    def _kpi_rollback_rate(self) -> float:
        records = self._windowed_records()

        if not records:
            return 0.0

        rollbacks = self._windowed_rollback_timestamps()

        return len(rollbacks) / len(records)

    def _kpi_mttr(self) -> float:
        records = sorted(
            self._windowed_records(), key=lambda record: record.recorded_at
        )

        by_deployment: "dict[str, list[_RolloutRecord]]" = {}

        for record in records:
            by_deployment.setdefault(record.deployment_id, []).append(
                record
            )

        recovery_times: "list[float]" = []

        for deployment_records in by_deployment.values():
            pending_failure_at: "datetime | None" = None

            for record in deployment_records:
                if record.outcome == "FAILURE":
                    pending_failure_at = record.recorded_at

                elif (
                    record.outcome == "SUCCESS"
                    and pending_failure_at is not None
                ):
                    recovery_times.append(
                        (
                            record.recorded_at - pending_failure_at
                        ).total_seconds()
                    )
                    pending_failure_at = None

        if not recovery_times:
            return 0.0

        return sum(recovery_times) / len(recovery_times)

    def _kpi_mtbf(self) -> float:
        failure_times = sorted(
            record.recorded_at
            for record in self._windowed_records()
            if record.outcome == "FAILURE"
        )

        if len(failure_times) < 2:
            return 0.0

        deltas = [
            (later - earlier).total_seconds()
            for earlier, later in zip(failure_times, failure_times[1:])
        ]

        return sum(deltas) / len(deltas)

    def _kpi_health_score_trend(self) -> float:
        scores = self._windowed_health_scores()

        if not scores:
            return 0.0

        return sum(score for _, score in scores) / len(scores)

    def _kpi_deployment_frequency(self) -> float:
        records = self._windowed_records()

        if not records:
            return 0.0

        if self._window_seconds is not None and self._window_seconds > 0:
            days = self._window_seconds / 86400.0

            return len(records) / days

        span_seconds = (
            records[-1].recorded_at - records[0].recorded_at
        ).total_seconds()

        if span_seconds <= 0:
            return float(len(records))

        return len(records) / (span_seconds / 86400.0)

    def _compute_snapshot(self) -> RolloutAnalyticsSnapshot:
        records = self._windowed_records()

        successful = sum(
            1 for record in records if record.outcome == "SUCCESS"
        )
        failed = sum(
            1 for record in records if record.outcome == "FAILURE"
        )

        return RolloutAnalyticsSnapshot(
            generated_at=self._clock(),
            successful_rollouts=successful,
            failed_rollouts=failed,
            average_duration_seconds=self._kpi_average_duration(),
            rollback_rate=self._kpi_rollback_rate(),
        )

    def _run_pipeline(self) -> RolloutAnalyticsSnapshot:
        kpis = self.summary()

        snapshot = self._compute_snapshot()

        now = self._clock()

        breaches: "list[tuple[str, float, float]]" = []

        with self._lock:
            previous_values = {
                name: (values[-1][1] if values else None)
                for name, values in self._kpi_history.items()
            }

            for name, (threshold, direction) in self._thresholds.items():
                value = kpis.get(name)

                if value is None:
                    continue

                if direction == "above" and value > threshold:
                    breaches.append((name, value, threshold))

                elif direction == "below" and value < threshold:
                    breaches.append((name, value, threshold))

            had_prior_history = bool(self._history)

            changed_metrics = [
                name
                for name, value in kpis.items()
                if previous_values.get(name) != value
            ]

            for name, value in kpis.items():
                self._kpi_history.setdefault(name, []).append(
                    (now, value)
                )

            self._history.append(snapshot)

        self._publish(
            "rollout_analytics_updated",
            {
                "successful_rollouts": snapshot.successful_rollouts,
                "failed_rollouts": snapshot.failed_rollouts,
            },
        )

        self._publish("rollout_snapshot_created", snapshot.to_dict())

        if had_prior_history and changed_metrics:
            self._publish(
                "rollout_trend_changed", {"metrics": changed_metrics}
            )

        for name, value, threshold in breaches:
            self._publish(
                "rollout_kpi_threshold_exceeded",
                {"metric": name, "value": value, "threshold": threshold},
            )

        if self._audit_service is not None:
            self._audit_service.record(
                action="rollout_analytics_recorded",
                actor="deployment_rollout_analytics",
                resource=_SOURCE,
                outcome="recorded",
                metadata=snapshot.to_dict(),
            )

        return snapshot

    def _on_rollout_completed(self, event: Any) -> None:
        self._on_rollout_outcome(event, outcome="SUCCESS")

    def _on_rollout_failed(self, event: Any) -> None:
        self._on_rollout_outcome(event, outcome="FAILURE")

    def _on_rollout_outcome(self, event: Any, *, outcome: str) -> None:
        deployment_id = event.payload.get("deployment_id")

        if not deployment_id or self._rollout_manager is None:
            return

        try:
            rollout = self._rollout_manager.get(event.source)

        except KeyError:
            return

        duration_seconds = (
            self._clock() - rollout.created_at
        ).total_seconds()

        try:
            self.record(
                deployment_id, outcome, max(duration_seconds, 0.0)
            )

        except ValueError:
            pass

    def _on_rollback_completed(self, event: Any) -> None:
        with self._lock:
            self._rollback_timestamps.append(self._clock())

    def _on_health_evaluated(self, event: Any) -> None:
        score = event.payload.get("score")

        if score is None:
            return

        with self._lock:
            self._health_scores.append((self._clock(), float(score)))

    def _publish(
        self, event_type: str, payload: "dict[str, Any] | None" = None
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=_SOURCE, payload=payload
        )


def build_default_governance_rollout_analytics() -> (
    DeploymentRolloutAnalytics
):
    """
    Build the process-wide rollout analytics engine, wired to the
    process-wide governance event bus, rollout manager, health engine,
    rollback engine, traffic router, scheduler metrics, and audit
    service.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_rollback import get_rollback_engine
    from .deployment_governance_rollout_health import (
        get_rollout_health_engine,
    )
    from .deployment_governance_rollout_manager import (
        get_rollout_manager,
    )
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )
    from .deployment_governance_traffic_router import get_traffic_router

    return DeploymentRolloutAnalytics(
        event_bus=get_event_bus(),
        rollout_manager=get_rollout_manager(),
        health_engine=get_rollout_health_engine(),
        rollback_engine=get_rollback_engine(),
        traffic_router=get_traffic_router(),
        metrics=get_scheduler_metrics(),
        audit_service=get_audit_service(),
    )


# Shared for the lifetime of the process: the recorded history every
# KPI/trend is derived from needs to be visible to every caller, which
# cannot be meaningfully rebuilt fresh per request.
_rollout_analytics = build_default_governance_rollout_analytics()


def get_rollout_analytics() -> DeploymentRolloutAnalytics:
    """
    Return the process-wide rollout analytics engine.
    """

    return _rollout_analytics
