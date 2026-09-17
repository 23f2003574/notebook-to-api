from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from backend.observability.health_checks import HealthReport
from backend.observability.metrics_registry import MetricsRegistry


VALID_RULE_TYPES = ("threshold", "rate", "anomaly", "composite")
VALID_SEVERITIES = ("info", "warning", "critical")
VALID_COMPARATORS = ("gt", "gte", "lt", "lte", "eq")


@dataclass
class AlertRule:
    name: str
    metric_name: str
    rule_type: str
    severity: str
    threshold: Optional[float] = None
    comparator: str = "gt"
    labels: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.rule_type not in VALID_RULE_TYPES:
            raise ValueError(
                f"Unsupported rule_type '{self.rule_type}'. Expected one of {VALID_RULE_TYPES}."
            )
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"Unsupported severity '{self.severity}'. Expected one of {VALID_SEVERITIES}."
            )
        if self.comparator not in VALID_COMPARATORS:
            raise ValueError(
                f"Unsupported comparator '{self.comparator}'. Expected one of {VALID_COMPARATORS}."
            )
        if self.rule_type == "threshold" and self.threshold is None:
            raise ValueError("threshold rules require a threshold value")


@dataclass
class AlertEvent:
    alert_id: str
    rule_name: str
    severity: str
    message: str
    triggered_at: str
    resolved_at: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.resolved_at is None


class AlertRuleEngine:
    def __init__(self):
        self._rules: Dict[str, AlertRule] = {}
        self._active_alerts: Dict[str, AlertEvent] = {}
        self._history: List[AlertEvent] = []

    def register_rule(self, rule: AlertRule) -> AlertRule:
        if rule.name in self._rules:
            raise ValueError(f"Rule '{rule.name}' is already registered")

        self._rules[rule.name] = rule
        return rule

    def evaluate(self, rule_name: str, registry: MetricsRegistry) -> Optional[AlertEvent]:
        rule = self._rules.get(rule_name)
        if rule is None:
            raise KeyError(f"Rule '{rule_name}' is not registered")

        sample = registry.latest(rule.metric_name, labels=rule.labels or None)
        if sample is None:
            return None

        breached = _compare(sample.value, rule.comparator, rule.threshold)
        if breached:
            return self.trigger(
                rule_name,
                message=(
                    f"{rule.metric_name} {rule.comparator} {rule.threshold} "
                    f"(value={sample.value})"
                ),
            )

        existing = self._active_alerts.get(rule_name)
        if existing is not None:
            self.resolve(existing.alert_id)
        return None

    def evaluate_health(self, rule_name: str, report: HealthReport) -> Optional[AlertEvent]:
        rule = self._rules.get(rule_name)
        if rule is None:
            raise KeyError(f"Rule '{rule_name}' is not registered")

        if report.status != "healthy":
            return self.trigger(rule_name, message=report.error or f"{report.name} is {report.status}")

        existing = self._active_alerts.get(rule_name)
        if existing is not None:
            self.resolve(existing.alert_id)
        return None

    def trigger(self, rule_name: str, message: str) -> AlertEvent:
        rule = self._rules.get(rule_name)
        if rule is None:
            raise KeyError(f"Rule '{rule_name}' is not registered")

        existing = self._active_alerts.get(rule_name)
        if existing is not None:
            return existing

        event = AlertEvent(
            alert_id=str(uuid4()),
            rule_name=rule_name,
            severity=rule.severity,
            message=message,
            triggered_at=_utc_now_iso(),
        )
        self._active_alerts[rule_name] = event
        self._history.append(event)
        return event

    def resolve(self, alert_id: str) -> AlertEvent:
        event = next((e for e in self._history if e.alert_id == alert_id), None)
        if event is None:
            raise KeyError(f"Alert '{alert_id}' not found")
        if event.resolved_at is not None:
            raise ValueError(f"Alert '{alert_id}' is already resolved")

        event.resolved_at = _utc_now_iso()
        self._active_alerts.pop(event.rule_name, None)
        return event

    def list_alerts(self, active_only: bool = False) -> List[AlertEvent]:
        if active_only:
            return list(self._active_alerts.values())
        return list(self._history)


def _compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == "gt":
        return value > threshold
    if comparator == "gte":
        return value >= threshold
    if comparator == "lt":
        return value < threshold
    if comparator == "lte":
        return value <= threshold
    return value == threshold


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
