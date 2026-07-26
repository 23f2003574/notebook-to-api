from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_scheduler import DeploymentScheduler

TRIGGER_TYPES = ("git_push", "release_created", "schedule", "webhook", "manual")
CONDITION_OPERATORS = ("equals", "not_equals", "in", "contains")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownRuleError(KeyError):
    pass


@dataclass(frozen=True)
class TriggerCondition:
    """One condition an automation rule's trigger payload must satisfy to match."""

    field: str
    operator: str = "equals"
    value: object = None

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("condition field is required")
        if self.operator not in CONDITION_OPERATORS:
            raise ValueError(f"unsupported operator '{self.operator}'")

    def matches(self, payload: dict) -> bool:
        actual = payload.get(self.field)
        if self.operator == "equals":
            return actual == self.value
        if self.operator == "not_equals":
            return actual != self.value
        if self.operator == "in":
            return actual in (self.value or ())
        if self.operator == "contains":
            return self.value in (actual or ())
        return False

    def to_dict(self) -> dict:
        return {"field": self.field, "operator": self.operator, "value": self.value}


@dataclass(frozen=True)
class AutomationRule:
    """An immutable rule that triggers a pipeline when its trigger type and conditions match an event."""

    rule_id: str
    name: str
    pipeline: str
    trigger_type: str
    conditions: tuple = ()
    priority: int = 0
    enabled: bool = True
    created_at: Optional[datetime] = None

    def matches(self, trigger_type: str, payload: dict) -> bool:
        if not self.enabled or self.trigger_type != trigger_type:
            return False
        return all(condition.matches(payload) for condition in self.conditions)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "pipeline": self.pipeline,
            "trigger_type": self.trigger_type,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "priority": self.priority,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DeploymentAutomationEngine:
    """Matches incoming trigger events against registered rules and dispatches deployments for them."""

    def __init__(self, scheduler: Optional[DeploymentScheduler] = None) -> None:
        self._rules: dict[str, AutomationRule] = {}
        self._lock = Lock()
        self._scheduler = scheduler

    def register_rule(
        self,
        name: str,
        pipeline: str,
        trigger_type: str,
        *,
        conditions=(),
        priority: int = 0,
        enabled: bool = True,
        timestamp: Optional[datetime] = None,
    ) -> AutomationRule:
        if not name:
            raise ValueError("rule name is required")
        if not pipeline:
            raise ValueError("pipeline is required")
        if trigger_type not in TRIGGER_TYPES:
            raise ValueError(f"unsupported trigger type '{trigger_type}'")

        rule = AutomationRule(
            rule_id=_new_id(),
            name=name,
            pipeline=pipeline,
            trigger_type=trigger_type,
            conditions=tuple(conditions),
            priority=priority,
            enabled=enabled,
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._rules[rule.rule_id] = rule
        return rule

    def remove_rule(self, rule_id: str) -> None:
        with self._lock:
            if rule_id not in self._rules:
                raise UnknownRuleError(rule_id)
            del self._rules[rule_id]

    def list_rules(self) -> tuple:
        with self._lock:
            rules = list(self._rules.values())
        return tuple(sorted(rules, key=lambda rule: (-rule.priority, rule.rule_id)))

    def evaluate(self, trigger_type: str, payload: dict) -> tuple:
        if trigger_type not in TRIGGER_TYPES:
            raise ValueError(f"unsupported trigger type '{trigger_type}'")
        return tuple(rule for rule in self.list_rules() if rule.matches(trigger_type, payload))

    def trigger(
        self,
        trigger_type: str,
        payload: dict,
        *,
        scheduler: Optional[DeploymentScheduler] = None,
        timestamp: Optional[datetime] = None,
    ) -> tuple:
        sched = scheduler or self._scheduler
        if sched is None:
            raise ValueError("scheduler is required to trigger automation rules")

        matched = self.evaluate(trigger_type, payload)
        return tuple(
            sched.schedule_now(rule.pipeline, priority=rule.priority, timestamp=timestamp)
            for rule in matched
        )


_engine = DeploymentAutomationEngine()


def get_deployment_automation_engine() -> DeploymentAutomationEngine:
    return _engine


router = APIRouter(prefix="/governance", tags=["governance-automation"])


def _parse_conditions(payload) -> list:
    return [
        TriggerCondition(
            field=condition["field"],
            operator=condition.get("operator", "equals"),
            value=condition.get("value"),
        )
        for condition in payload or []
    ]


@router.post("/automation/rules")
def create_rule(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    pipeline = payload.get("pipeline")
    trigger_type = payload.get("trigger_type")
    if not name or not pipeline or not trigger_type:
        raise HTTPException(
            status_code=422, detail="name, pipeline, and trigger_type are required"
        )

    try:
        rule = get_deployment_automation_engine().register_rule(
            name,
            pipeline,
            trigger_type,
            conditions=_parse_conditions(payload.get("conditions")),
            priority=payload.get("priority", 0),
            enabled=payload.get("enabled", True),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return rule.to_dict()


@router.get("/automation/rules")
def list_rules() -> list:
    return [rule.to_dict() for rule in get_deployment_automation_engine().list_rules()]


@router.post("/automation/evaluate")
def evaluate_trigger(payload: dict = Body(...)) -> list:
    trigger_type = payload.get("trigger_type")
    if not trigger_type:
        raise HTTPException(status_code=422, detail="trigger_type is required")

    try:
        matched = get_deployment_automation_engine().evaluate(
            trigger_type, payload.get("payload", {})
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [rule.to_dict() for rule in matched]
