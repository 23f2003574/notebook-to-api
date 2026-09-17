from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_rollback import DeploymentRollbackEngine
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )

HEALTH_STATES: "tuple[str, ...]" = (
    "HEALTHY",
    "DEGRADED",
    "UNHEALTHY",
    "CRITICAL",
)

# What a rollout engine should do in response to a given health
# state — the "Rollout Decision" leg of this engine's evaluation flow.
# Exposed via decision_for() for Canary/Rolling/Progressive Delivery
# (and anything else) to consult without hard-coding the mapping
# themselves.
_DECISIONS: "dict[str, str]" = {
    "HEALTHY": "CONTINUE",
    "DEGRADED": "CONTINUE",
    "UNHEALTHY": "PAUSE",
    "CRITICAL": "ROLLBACK",
}


@dataclass(frozen=True)
class HealthIndicator:
    """
    One indicator's evaluated value against its configured threshold.
    """

    name: str

    value: float

    threshold: float

    healthy: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "threshold": self.threshold,
            "healthy": self.healthy,
        }


@dataclass(frozen=True)
class RolloutHealthSnapshot:
    """
    One deployment's overall health verdict at a point in time.
    """

    deployment_id: str

    status: str

    score: float

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.status not in HEALTH_STATES:
            raise ValueError(f"status must be one of {HEALTH_STATES}")

        if not 0.0 <= self.score <= 100.0:
            raise ValueError("score must be between 0 and 100")

        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "status": self.status,
            "score": self.score,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass(frozen=True)
class _RegisteredIndicator:
    """
    Internal registration record: name plus everything evaluate()
    needs to turn one live value into a HealthIndicator and a
    contribution to the overall weighted score. Not part of the
    public model surface (only HealthIndicator, the per-evaluation
    result, is) — this is configuration, not a result.
    """

    name: str

    evaluator: "Callable[[str], float]"

    threshold: float

    weight: float

    priority: int

    higher_is_better: bool


class DeploymentRolloutHealthEngine:
    """
    Continuously assesses rollout quality and produces one weighted
    health verdict per deployment, the shared decision point Canary,
    Rolling, and Progressive Delivery consult (in place of each
    inventing its own ad hoc health check) and the automatic Rollback
    Engine reacts to.

    Every registered indicator contributes value/threshold/healthy via
    its evaluator (called with deployment_id); healthy indicators
    contribute their full weight toward the overall score, unhealthy
    ones contribute none — score = 100 * sum(weight for healthy
    indicators) / sum(all weights). Built-in indicators source their
    values from whatever is wired in (Metrics Service, Rollback
    Engine); with nothing wired, each defaults to a value that reads
    as healthy, matching this codebase's convention of "unset
    integration means unable to detect a problem," not "assume the
    worst."

    decision_for(status) maps a health state to what a rollout should
    do about it (CONTINUE/PAUSE/ROLLBACK) — Canary/Rolling/Progressive
    Delivery use this (via a lazily-resolved reference to the
    process-wide singleton, to avoid a circular singleton dependency:
    this engine's own singleton wires the Rollback Engine, which in
    turn wires those three) rather than each hard-coding the mapping.
    The Rollback Engine and Rollout Manager instead subscribe directly
    to this engine's "rollout_health_critical" event and react
    autonomously, the same event-driven shape already used for
    "rollout_failed" — see DeploymentRollbackEngine.

    Thread-safe: every mutation of the indicator registry and
    evaluation history is guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        rollback_engine: "DeploymentRollbackEngine | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        healthy_threshold: float = 90.0,
        degraded_threshold: float = 70.0,
        unhealthy_threshold: float = 40.0,
        sweep_interval_seconds: int = 60,
    ) -> None:
        if not (
            0.0
            <= unhealthy_threshold
            <= degraded_threshold
            <= healthy_threshold
            <= 100.0
        ):
            raise ValueError(
                "thresholds must satisfy 0 <= unhealthy_threshold <= "
                "degraded_threshold <= healthy_threshold <= 100"
            )

        self._lock = threading.Lock()

        self._indicators: "dict[str, _RegisteredIndicator]" = {}

        self._known_deployment_ids: "set[str]" = set()

        self._history: "dict[str, list[RolloutHealthSnapshot]]" = {}

        self._previous_status: "dict[str, str]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._metrics = metrics

        self._rollback_engine = rollback_engine

        self._healthy_threshold = healthy_threshold

        self._degraded_threshold = degraded_threshold

        self._unhealthy_threshold = unhealthy_threshold

        self._scheduler = scheduler

        self._sweep_interval_seconds = sweep_interval_seconds

        self._sweep_job_id: "str | None" = None

        self._register_builtin_indicators()

    def register_sweep_job(self) -> str:
        """
        Register a recurring job on this engine's scheduler for
        periodic re-evaluation of every tracked deployment, returning
        its job_id. Idempotent: returns the existing job_id if already
        registered.

        Deliberately not called automatically at construction time
        (unlike CanaryDeploymentEngine/RollingDeploymentEngine's
        per-deployment jobs, which are tied to a clear deploy()/
        terminal-state lifecycle): a caller opts into this
        long-lived, not-tied-to-any-single-deployment job explicitly,
        rather than every engine instance permanently registering one
        as a side effect of merely being constructed — including the
        process-wide singleton, which would otherwise register a job
        that outlives everything and interacts with the governance
        scheduler bootstrap/persistence cycle in ways nothing else in
        this codebase does.

        Raises ValueError if no scheduler is wired in.
        """

        if self._scheduler is None:
            raise ValueError(
                "register_sweep_job requires a scheduler to be wired "
                "in"
            )

        if self._sweep_job_id is not None:
            return self._sweep_job_id

        job = self._scheduler.register(
            "rollout-health-evaluation-sweep",
            interval_seconds=self._sweep_interval_seconds,
            namespace="rollout-health",
            description=(
                "Periodic re-evaluation of every tracked deployment's "
                "rollout health"
            ),
        )

        self._sweep_job_id = job.job_id

        return self._sweep_job_id

    def _register_builtin_indicators(self) -> None:
        def _job_counts() -> "tuple[int, int, int]":
            if self._metrics is None:
                return (0, 0, 0)

            snapshot = self._metrics.snapshot()

            return (
                snapshot.jobs_completed,
                snapshot.jobs_failed,
                snapshot.jobs_cancelled,
            )

        def _success_rate(_deployment_id: str) -> float:
            completed, failed, cancelled = _job_counts()
            total = completed + failed + cancelled

            return 1.0 if total == 0 else completed / total

        def _error_rate(_deployment_id: str) -> float:
            completed, failed, cancelled = _job_counts()
            total = completed + failed + cancelled

            return 0.0 if total == 0 else failed / total

        def _request_latency(_deployment_id: str) -> float:
            if self._metrics is None:
                return 0.0

            return self._metrics.summary().average_execution_ms

        def _restart_count(_deployment_id: str) -> float:
            if self._metrics is None:
                return 0.0

            return self._metrics.summary().retry_rate

        def _rollback_count(deployment_id: str) -> float:
            if self._rollback_engine is None:
                return 0.0

            return float(len(self._rollback_engine.history(deployment_id)))

        def _instance_availability(_deployment_id: str) -> float:
            # No wired data source is in scope for this indicator
            # (Traffic Router / instance telemetry are not part of
            # this engine's integration set) — reads as fully
            # available until a caller overrides it with a real
            # evaluator via register_indicator().
            return 1.0

        def _traffic_distribution(_deployment_id: str) -> float:
            return 1.0

        self._indicators = {
            "success_rate": _RegisteredIndicator(
                name="success_rate", evaluator=_success_rate,
                threshold=0.9, weight=2.0, priority=0,
                higher_is_better=True,
            ),
            "error_rate": _RegisteredIndicator(
                name="error_rate", evaluator=_error_rate,
                threshold=0.1, weight=2.0, priority=1,
                higher_is_better=False,
            ),
            "request_latency": _RegisteredIndicator(
                name="request_latency", evaluator=_request_latency,
                threshold=1000.0, weight=1.0, priority=2,
                higher_is_better=False,
            ),
            "instance_availability": _RegisteredIndicator(
                name="instance_availability",
                evaluator=_instance_availability, threshold=0.8,
                weight=1.0, priority=3, higher_is_better=True,
            ),
            "traffic_distribution": _RegisteredIndicator(
                name="traffic_distribution",
                evaluator=_traffic_distribution, threshold=0.5,
                weight=1.0, priority=4, higher_is_better=True,
            ),
            "restart_count": _RegisteredIndicator(
                name="restart_count", evaluator=_restart_count,
                threshold=0.2, weight=1.0, priority=5,
                higher_is_better=False,
            ),
            "rollback_count": _RegisteredIndicator(
                name="rollback_count", evaluator=_rollback_count,
                threshold=2.0, weight=1.0, priority=6,
                higher_is_better=False,
            ),
        }

    def register_indicator(
        self,
        name: str,
        evaluator: "Callable[[str], float]",
        threshold: float,
        *,
        weight: float = 1.0,
        priority: int = 0,
        higher_is_better: bool = True,
    ) -> None:
        """
        Register (or override) a health indicator. evaluator is
        called with a deployment_id and must return its current
        value; healthy is value >= threshold if higher_is_better,
        else value <= threshold.

        Raises ValueError if name is empty or weight is not positive.
        """

        if not name:
            raise ValueError("name must not be empty")

        if weight <= 0:
            raise ValueError("weight must be greater than 0")

        with self._lock:
            self._indicators[name] = _RegisteredIndicator(
                name=name, evaluator=evaluator, threshold=threshold,
                weight=weight, priority=priority,
                higher_is_better=higher_is_better,
            )

    def remove_indicator(self, name: str) -> None:
        """
        Remove a registered indicator.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._indicators:
                raise KeyError(f"indicator '{name}' is not registered")

            del self._indicators[name]

    def evaluate(self, deployment_id: str) -> RolloutHealthSnapshot:
        """
        Evaluate every registered indicator for deployment_id (in
        deterministic priority-then-name order) and produce one
        overall RolloutHealthSnapshot.
        """

        with self._lock:
            registered = sorted(
                self._indicators.values(),
                key=lambda indicator: (
                    indicator.priority, indicator.name
                ),
            )

        indicators = [
            self._evaluate_one(indicator, deployment_id)
            for indicator in registered
        ]

        score = self._weighted_score(registered, indicators)
        status = self._status_for_score(score)

        now = self._clock()

        snapshot = RolloutHealthSnapshot(
            deployment_id=deployment_id, status=status, score=score,
            evaluated_at=now,
        )

        with self._lock:
            self._known_deployment_ids.add(deployment_id)
            self._history.setdefault(deployment_id, []).append(
                snapshot
            )
            previous_status = self._previous_status.get(deployment_id)
            self._previous_status[deployment_id] = status

        self._publish_for_status(
            deployment_id, status, previous_status, score
        )

        return snapshot

    def evaluate_all(self) -> "tuple[RolloutHealthSnapshot, ...]":
        """
        Re-evaluate every deployment_id ever passed to evaluate(),
        ordered by deployment_id.
        """

        with self._lock:
            deployment_ids = sorted(self._known_deployment_ids)

        return tuple(
            self.evaluate(deployment_id)
            for deployment_id in deployment_ids
        )

    def history(
        self, deployment_id: str
    ) -> "tuple[RolloutHealthSnapshot, ...]":
        """
        Return every health snapshot ever recorded for deployment_id,
        oldest first. Returns an empty tuple if deployment_id has
        never been evaluated.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def latest(self, deployment_id: str) -> RolloutHealthSnapshot:
        """
        Return deployment_id's most recent health snapshot.

        Raises KeyError if deployment_id has never been evaluated.
        """

        with self._lock:
            entries = self._history.get(deployment_id)

            if not entries:
                raise KeyError(
                    f"deployment '{deployment_id}' has never been "
                    "evaluated"
                )

            return entries[-1]

    def summary(self) -> "dict[str, object]":
        """
        Return an aggregate summary across every tracked deployment's
        most recent evaluation: how many are currently in each health
        state.
        """

        with self._lock:
            latest_statuses = [
                entries[-1].status
                for entries in self._history.values()
                if entries
            ]

        counts = {state: 0 for state in HEALTH_STATES}

        for status in latest_statuses:
            counts[status] += 1

        return {
            "total_evaluated": len(latest_statuses),
            "healthy": counts["HEALTHY"],
            "degraded": counts["DEGRADED"],
            "unhealthy": counts["UNHEALTHY"],
            "critical": counts["CRITICAL"],
        }

    def clear_history(self) -> None:
        """
        Remove every tracked deployment's evaluation history, without
        removing any registered indicators.
        """

        with self._lock:
            self._known_deployment_ids.clear()
            self._history.clear()
            self._previous_status.clear()

    def list(self) -> "tuple[RolloutHealthSnapshot, ...]":
        """
        Return every tracked deployment's most recent health snapshot,
        ordered by deployment_id.
        """

        with self._lock:
            latest_snapshots = [
                entries[-1]
                for entries in self._history.values()
                if entries
            ]

        return tuple(
            sorted(
                latest_snapshots,
                key=lambda snapshot: snapshot.deployment_id,
            )
        )

    def decision_for(self, status: str) -> str:
        """
        Return what a rollout should do in response to status:
        "CONTINUE", "PAUSE", or "ROLLBACK".

        Raises ValueError if status is not a recognized health state.
        """

        if status not in _DECISIONS:
            raise ValueError(f"status must be one of {HEALTH_STATES}")

        return _DECISIONS[status]

    def _evaluate_one(
        self, indicator: _RegisteredIndicator, deployment_id: str
    ) -> HealthIndicator:
        value = indicator.evaluator(deployment_id)

        healthy = (
            value >= indicator.threshold
            if indicator.higher_is_better
            else value <= indicator.threshold
        )

        return HealthIndicator(
            name=indicator.name, value=value,
            threshold=indicator.threshold, healthy=healthy,
        )

    def _weighted_score(
        self,
        registered: "list[_RegisteredIndicator]",
        indicators: "list[HealthIndicator]",
    ) -> float:
        if not registered:
            return 100.0

        total_weight = sum(
            indicator.weight for indicator in registered
        )

        healthy_weight = sum(
            registered_indicator.weight
            for registered_indicator, result in zip(
                registered, indicators
            )
            if result.healthy
        )

        return 100.0 * healthy_weight / total_weight

    def _status_for_score(self, score: float) -> str:
        if score >= self._healthy_threshold:
            return "HEALTHY"

        if score >= self._degraded_threshold:
            return "DEGRADED"

        if score >= self._unhealthy_threshold:
            return "UNHEALTHY"

        return "CRITICAL"

    def _publish_for_status(
        self,
        deployment_id: str,
        status: str,
        previous_status: "str | None",
        score: float,
    ) -> None:
        self._publish(
            "rollout_health_evaluated", deployment_id,
            {"status": status, "score": score},
        )

        if status == "DEGRADED":
            self._publish("rollout_health_degraded", deployment_id, {})

        elif status == "UNHEALTHY":
            self._publish(
                "rollout_health_unhealthy", deployment_id, {}
            )

        elif status == "CRITICAL":
            self._publish("rollout_health_critical", deployment_id, {})

        elif status == "HEALTHY" and previous_status not in (
            None, "HEALTHY",
        ):
            self._publish(
                "rollout_health_restored", deployment_id, {}
            )

    def _publish(
        self,
        event_type: str,
        deployment_id: str,
        payload: "dict[str, Any] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        merged = {"deployment_id": deployment_id}
        merged.update(payload or {})

        self._event_bus.publish(
            event_type, source=deployment_id, payload=merged
        )


def build_default_governance_rollout_health_engine() -> (
    DeploymentRolloutHealthEngine
):
    """
    Build the process-wide rollout health engine, wired to the
    process-wide governance event bus, scheduler metrics, rollback
    engine, and scheduler.

    Also wires itself into the process-wide canary, rolling, and
    progressive delivery engines via their set_health_engine() —
    those three cannot wire this engine back via constructor
    injection (this engine already depends, transitively through the
    rollback engine, on all three), so this is done here instead,
    once every singleton in the chain already exists. See
    CanaryDeploymentEngine.set_health_engine.
    """

    from .deployment_governance_canary import get_canary_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_progressive_delivery import (
        get_progressive_delivery_engine,
    )
    from .deployment_governance_rolling import get_rolling_engine
    from .deployment_governance_rollback import get_rollback_engine
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )

    rollback_engine = get_rollback_engine()

    engine = DeploymentRolloutHealthEngine(
        event_bus=get_event_bus(),
        metrics=get_scheduler_metrics(),
        rollback_engine=rollback_engine,
        scheduler=get_scheduler(),
    )

    get_canary_engine().set_health_engine(engine)
    get_rolling_engine().set_health_engine(engine)
    get_progressive_delivery_engine().set_health_engine(engine)

    return engine


# Shared for the lifetime of the process: every deployment's
# evaluation history needs to be visible to every caller (and to the
# Rollback Engine reacting to its events), which cannot be
# meaningfully rebuilt fresh per request.
_rollout_health_engine = build_default_governance_rollout_health_engine()


def get_rollout_health_engine() -> DeploymentRolloutHealthEngine:
    """
    Return the process-wide rollout health engine.
    """

    return _rollout_health_engine
