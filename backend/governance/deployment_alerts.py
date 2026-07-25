from __future__ import annotations

import operator
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Iterable, Mapping, Optional

from fastapi import APIRouter, HTTPException

from .deployment_metrics import (
    DEPLOY_DURATION_MS,
    FAILURE_COUNT,
    ROLLBACK_COUNT,
    SUCCESS_COUNT,
    MetricsSnapshot,
    get_deployment_metrics_collector,
)
from .deployment_notifications import DeploymentNotificationService

ALERT_LEVELS = ("INFO", "WARNING", "ERROR", "CRITICAL")

_COMPARATORS: Mapping[str, Callable[[float, float], bool]] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
}


def _new_id() -> str:
    return uuid.uuid4().hex


def _normalize_level(level: str) -> str:
    level = level.upper()
    if level not in ALERT_LEVELS:
        raise ValueError(
            f"invalid alert level {level!r}; expected one of {ALERT_LEVELS}"
        )
    return level


class UnknownAlertError(KeyError):
    pass


def _error_rate(snapshot: MetricsSnapshot) -> Optional[float]:
    success = snapshot.counters.get(SUCCESS_COUNT, 0.0)
    failure = snapshot.counters.get(FAILURE_COUNT, 0.0)
    total = success + failure
    if total == 0:
        return None
    return failure / total


@dataclass(frozen=True)
class AlertRule:
    """A threshold-based rule evaluated against a metrics snapshot."""

    name: str
    level: str
    threshold: float
    comparator: str = "gte"
    metric: Optional[str] = None
    histogram_field: str = "avg"
    message: Optional[str] = None
    value_fn: Optional[Callable[[MetricsSnapshot], Optional[float]]] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", _normalize_level(self.level))
        if self.comparator not in _COMPARATORS:
            raise ValueError(
                f"invalid comparator {self.comparator!r}; "
                f"expected one of {tuple(_COMPARATORS)}"
            )
        if self.metric is None and self.value_fn is None:
            raise ValueError("a rule needs either 'metric' or 'value_fn'")

    def evaluate(self, snapshot: MetricsSnapshot) -> Optional[float]:
        value = (
            self.value_fn(snapshot)
            if self.value_fn is not None
            else snapshot.get(self.metric, histogram_field=self.histogram_field)
        )
        if value is None:
            return None
        return value if _COMPARATORS[self.comparator](value, self.threshold) else None


DEFAULT_RULES: tuple[AlertRule, ...] = (
    AlertRule(
        name="deployment_failure",
        level="ERROR",
        threshold=1,
        comparator="gte",
        metric=FAILURE_COUNT,
        message="one or more deployments have failed",
    ),
    AlertRule(
        name="high_latency",
        level="WARNING",
        threshold=30000.0,
        comparator="gt",
        metric=DEPLOY_DURATION_MS,
        histogram_field="max",
        message="deployment latency exceeded threshold",
    ),
    AlertRule(
        name="rollback_threshold",
        level="CRITICAL",
        threshold=3,
        comparator="gte",
        metric=ROLLBACK_COUNT,
        message="rollback count exceeded threshold",
    ),
    AlertRule(
        name="error_rate",
        level="ERROR",
        threshold=0.5,
        comparator="gte",
        value_fn=_error_rate,
        message="deployment error rate exceeded threshold",
    ),
)


@dataclass(frozen=True)
class Alert:
    """One immutable alert record produced by a triggered rule."""

    alert_id: str
    rule_name: str
    level: str
    message: str
    value: float
    threshold: float
    triggered_at: datetime
    resolved_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.resolved_at is None

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "rule_name": self.rule_name,
            "level": self.level,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "triggered_at": self.triggered_at.isoformat(),
            "resolved_at": (
                self.resolved_at.isoformat() if self.resolved_at else None
            ),
            "is_active": self.is_active,
        }


class DeploymentAlertManager:
    """Evaluates rules against deployment metrics and tracks alerts."""

    def __init__(self, rules: Optional[Iterable[AlertRule]] = None) -> None:
        self._rules: dict[str, AlertRule] = {}
        self._alerts: dict[str, Alert] = {}
        self._lock = Lock()
        for rule in DEFAULT_RULES if rules is None else rules:
            self.register_rule(rule)

    def register_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self._rules[rule.name] = rule

    def evaluate(
        self,
        snapshot: MetricsSnapshot,
        *,
        timestamp: Optional[datetime] = None,
        notification_service: Optional[DeploymentNotificationService] = None,
    ) -> list[Alert]:
        with self._lock:
            rules = list(self._rules.values())

        triggered: list[Alert] = []
        for rule in rules:
            value = rule.evaluate(snapshot)
            if value is None:
                continue
            alert = self._trigger(rule, value, timestamp)
            if alert is not None:
                triggered.append(alert)
                if notification_service is not None:
                    notification_service.notify(alert, timestamp=timestamp)
        return triggered

    def _trigger(
        self, rule: AlertRule, value: float, timestamp: Optional[datetime]
    ) -> Optional[Alert]:
        with self._lock:
            for existing in self._alerts.values():
                if existing.rule_name == rule.name and existing.is_active:
                    return None
            alert = Alert(
                alert_id=_new_id(),
                rule_name=rule.name,
                level=rule.level,
                message=rule.message or f"{rule.name} threshold breached",
                value=value,
                threshold=rule.threshold,
                triggered_at=timestamp or datetime.now(timezone.utc),
            )
            self._alerts[alert.alert_id] = alert
        return alert

    def active_alerts(self) -> list[Alert]:
        with self._lock:
            return [alert for alert in self._alerts.values() if alert.is_active]

    def resolve(
        self, alert_id: str, *, timestamp: Optional[datetime] = None
    ) -> Alert:
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert is None:
                raise UnknownAlertError(alert_id)
            resolved = Alert(
                alert_id=alert.alert_id,
                rule_name=alert.rule_name,
                level=alert.level,
                message=alert.message,
                value=alert.value,
                threshold=alert.threshold,
                triggered_at=alert.triggered_at,
                resolved_at=timestamp or datetime.now(timezone.utc),
            )
            self._alerts[alert_id] = resolved
        return resolved


_manager = DeploymentAlertManager()


def get_deployment_alert_manager() -> DeploymentAlertManager:
    return _manager


router = APIRouter(prefix="/governance", tags=["governance-alerts"])


@router.get("/alerts")
def list_active_alerts() -> list[dict]:
    return [alert.to_dict() for alert in get_deployment_alert_manager().active_alerts()]


@router.post("/alerts/evaluate")
def evaluate_alerts() -> list[dict]:
    snapshot = get_deployment_metrics_collector().snapshot()
    triggered = get_deployment_alert_manager().evaluate(snapshot)
    return [alert.to_dict() for alert in triggered]


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str) -> dict:
    try:
        resolved = get_deployment_alert_manager().resolve(alert_id)
    except UnknownAlertError:
        raise HTTPException(status_code=404, detail="alert not found")
    return resolved.to_dict()
