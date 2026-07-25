from __future__ import annotations

import operator
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Mapping, Optional

from fastapi import APIRouter, HTTPException

from .deployment_metrics import (
    AVAILABILITY,
    DEPLOY_COUNT,
    DEPLOY_DURATION_MS,
    FAILURE_COUNT,
    ROLLBACK_COUNT,
    SUCCESS_COUNT,
    MetricsSnapshot,
    get_deployment_metrics_collector,
)

SLO_STATUSES = ("HEALTHY", "AT_RISK", "BREACHED")

_COMPARATORS: Mapping[str, Callable[[float, float], bool]] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
}


class UnknownObjectiveError(KeyError):
    pass


def _success_rate(snapshot: MetricsSnapshot) -> Optional[float]:
    success = snapshot.counters.get(SUCCESS_COUNT, 0.0)
    failure = snapshot.counters.get(FAILURE_COUNT, 0.0)
    total = success + failure
    if total == 0:
        return None
    return success / total


def _rollback_rate(snapshot: MetricsSnapshot) -> Optional[float]:
    deploys = snapshot.counters.get(DEPLOY_COUNT, 0.0)
    rollbacks = snapshot.counters.get(ROLLBACK_COUNT, 0.0)
    if deploys == 0:
        return None
    return rollbacks / deploys


@dataclass(frozen=True)
class SLOObjective:
    """A reliability objective evaluated against a rolling window of values."""

    name: str
    target: float
    comparator: str = "gte"
    metric: Optional[str] = None
    histogram_field: str = "avg"
    window_size: int = 10
    value_fn: Optional[Callable[[MetricsSnapshot], Optional[float]]] = None

    def __post_init__(self) -> None:
        if self.comparator not in _COMPARATORS:
            raise ValueError(
                f"invalid comparator {self.comparator!r}; "
                f"expected one of {tuple(_COMPARATORS)}"
            )
        if self.metric is None and self.value_fn is None:
            raise ValueError("an objective needs either 'metric' or 'value_fn'")
        if self.window_size < 1:
            raise ValueError("window_size must be at least 1")

    def compute(self, snapshot: MetricsSnapshot) -> Optional[float]:
        if self.value_fn is not None:
            return self.value_fn(snapshot)
        return snapshot.get(self.metric, histogram_field=self.histogram_field)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "target": self.target,
            "comparator": self.comparator,
            "metric": self.metric,
            "window_size": self.window_size,
        }


DEFAULT_OBJECTIVES: tuple[SLOObjective, ...] = (
    SLOObjective(
        name="deployment_success_rate",
        target=0.95,
        comparator="gte",
        value_fn=_success_rate,
    ),
    SLOObjective(
        name="deployment_latency",
        target=20000.0,
        comparator="lte",
        metric=DEPLOY_DURATION_MS,
        histogram_field="avg",
    ),
    SLOObjective(
        name="rollback_rate",
        target=0.1,
        comparator="lte",
        value_fn=_rollback_rate,
    ),
    SLOObjective(
        name="service_availability",
        target=0.995,
        comparator="gte",
        metric=AVAILABILITY,
    ),
)


@dataclass(frozen=True)
class SLOEvaluationResult:
    """One immutable outcome of evaluating an objective's rolling window."""

    objective_name: str
    value: float
    rolling_average: float
    target: float
    comparator: str
    status: str
    window_size: int
    evaluated_at: datetime

    def to_dict(self) -> dict:
        return {
            "objective_name": self.objective_name,
            "value": self.value,
            "rolling_average": self.rolling_average,
            "target": self.target,
            "comparator": self.comparator,
            "status": self.status,
            "window_size": self.window_size,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class DeploymentSLOManager:
    """Defines and evaluates deployment reliability objectives."""

    def __init__(self, objectives: Optional[list[SLOObjective]] = None) -> None:
        self._objectives: dict[str, SLOObjective] = {}
        self._windows: dict[str, deque] = {}
        self._latest: dict[str, SLOEvaluationResult] = {}
        self._lock = Lock()
        for objective in DEFAULT_OBJECTIVES if objectives is None else objectives:
            self.register(objective)

    def register(self, objective: SLOObjective) -> None:
        with self._lock:
            self._objectives[objective.name] = objective
            self._windows[objective.name] = deque(maxlen=objective.window_size)

    def remove(self, name: str) -> None:
        with self._lock:
            if name not in self._objectives:
                raise UnknownObjectiveError(name)
            del self._objectives[name]
            self._windows.pop(name, None)
            self._latest.pop(name, None)

    def evaluate(
        self,
        snapshot: MetricsSnapshot,
        *,
        timestamp: Optional[datetime] = None,
    ) -> list[SLOEvaluationResult]:
        with self._lock:
            objectives = list(self._objectives.values())

        results = []
        for objective in objectives:
            value = objective.compute(snapshot)
            if value is None:
                continue
            results.append(self._record(objective, value, timestamp))
        return results

    def _record(
        self,
        objective: SLOObjective,
        value: float,
        timestamp: Optional[datetime],
    ) -> SLOEvaluationResult:
        comparator = _COMPARATORS[objective.comparator]
        with self._lock:
            window = self._windows.setdefault(
                objective.name, deque(maxlen=objective.window_size)
            )
            window.append(value)
            rolling_average = sum(window) / len(window)
            meets_latest = comparator(value, objective.target)
            meets_rolling = comparator(rolling_average, objective.target)
            if meets_latest and meets_rolling:
                status = "HEALTHY"
            elif meets_rolling:
                status = "AT_RISK"
            else:
                status = "BREACHED"
            result = SLOEvaluationResult(
                objective_name=objective.name,
                value=value,
                rolling_average=rolling_average,
                target=objective.target,
                comparator=objective.comparator,
                status=status,
                window_size=len(window),
                evaluated_at=timestamp or datetime.now(timezone.utc),
            )
            self._latest[objective.name] = result
        return result

    def status(
        self, name: Optional[str] = None
    ) -> "SLOEvaluationResult | list[SLOEvaluationResult]":
        with self._lock:
            if name is not None:
                result = self._latest.get(name)
                if result is None:
                    raise UnknownObjectiveError(name)
                return result
            return list(self._latest.values())

    def list_objectives(self) -> list[SLOObjective]:
        with self._lock:
            return list(self._objectives.values())


_manager = DeploymentSLOManager()


def get_deployment_slo_manager() -> DeploymentSLOManager:
    return _manager


router = APIRouter(prefix="/governance", tags=["governance-slo"])


@router.get("/slo")
def list_objectives() -> list[dict]:
    return [obj.to_dict() for obj in get_deployment_slo_manager().list_objectives()]


@router.get("/slo/status")
def slo_status() -> list[dict]:
    return [result.to_dict() for result in get_deployment_slo_manager().status()]


@router.post("/slo/evaluate")
def evaluate_slo() -> list[dict]:
    snapshot = get_deployment_metrics_collector().snapshot()
    results = get_deployment_slo_manager().evaluate(snapshot)
    return [result.to_dict() for result in results]
