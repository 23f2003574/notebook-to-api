from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import Lock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from .etl_engine import ETLWorkflowEngine, UnknownWorkflowError, get_etl_workflow_engine


class TriggerType(str, Enum):
    CRON = "cron"
    INTERVAL = "interval"
    ONE_TIME = "one_time"
    MANUAL = "manual"


class InvalidTriggerError(ValueError):
    pass


class UnknownScheduleError(KeyError):
    pass


@dataclass(frozen=True)
class ScheduleTrigger:
    """Describes when a schedule should fire."""

    trigger_type: TriggerType
    expression: str = ""
    interval_seconds: Optional[int] = None
    run_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "trigger_type": self.trigger_type.value,
            "expression": self.expression,
            "interval_seconds": self.interval_seconds,
            "run_at": self.run_at.isoformat() if self.run_at else None,
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "ScheduleTrigger":
        payload = payload or {}
        if "trigger_type" not in payload:
            raise InvalidTriggerError("trigger_type is required")
        run_at_raw = payload.get("run_at")
        return cls(
            trigger_type=TriggerType(payload["trigger_type"]),
            expression=payload.get("expression", ""),
            interval_seconds=payload.get("interval_seconds"),
            run_at=datetime.fromisoformat(run_at_raw) if run_at_raw else None,
        )


def _next_cron_run(expression: str, after: datetime) -> datetime:
    parts = expression.split()
    if len(parts) != 5:
        raise InvalidTriggerError(f"unsupported cron expression '{expression}'")
    minute, hour, day, month, weekday = parts
    if day != "*" or month != "*" or weekday != "*":
        raise InvalidTriggerError("only minute/hour cron fields are supported")

    base = after.replace(second=0, microsecond=0)

    if minute.startswith("*/"):
        if hour != "*":
            raise InvalidTriggerError("interval minute fields require hour to be '*'")
        try:
            step = int(minute[2:])
        except ValueError:
            raise InvalidTriggerError(f"invalid minute step in '{expression}'")
        if step <= 0:
            raise InvalidTriggerError("minute step must be positive")
        next_minute = ((base.minute // step) + 1) * step
        return base.replace(minute=0) + timedelta(minutes=next_minute)

    if minute == "*" and hour == "*":
        return base + timedelta(minutes=1)

    if minute.isdigit() and hour.isdigit():
        target = base.replace(hour=int(hour), minute=int(minute))
        if target <= after:
            target += timedelta(days=1)
        return target

    raise InvalidTriggerError(f"unsupported cron expression '{expression}'")


def _compute_next_run(trigger: ScheduleTrigger, after: Optional[datetime] = None) -> Optional[datetime]:
    after = after or datetime.now(timezone.utc)
    if trigger.trigger_type == TriggerType.MANUAL:
        return None
    if trigger.trigger_type == TriggerType.ONE_TIME:
        if trigger.run_at is None:
            raise InvalidTriggerError("run_at is required for one_time triggers")
        if trigger.run_at <= after:
            raise InvalidTriggerError("run_at must be in the future")
        return trigger.run_at
    if trigger.trigger_type == TriggerType.INTERVAL:
        if not trigger.interval_seconds or trigger.interval_seconds <= 0:
            raise InvalidTriggerError("interval_seconds must be a positive number")
        return after + timedelta(seconds=trigger.interval_seconds)
    if trigger.trigger_type == TriggerType.CRON:
        if not trigger.expression:
            raise InvalidTriggerError("expression is required for cron triggers")
        return _next_cron_run(trigger.expression, after)
    raise InvalidTriggerError(f"unsupported trigger type '{trigger.trigger_type}'")


@dataclass(frozen=True)
class PipelineSchedule:
    """A configured trigger bound to a workflow, with its current run state."""

    schedule_id: str
    workflow_name: str
    trigger: ScheduleTrigger
    status: str
    next_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "schedule_id": self.schedule_id,
            "workflow_name": self.workflow_name,
            "trigger": self.trigger.to_dict(),
            "status": self.status,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PipelineScheduler:
    """Manages recurring, one-time, and manual triggers for ETL workflows."""

    def __init__(self) -> None:
        self._schedules: dict = {}
        self._lock = Lock()

    def schedule(
        self,
        workflow_name: str,
        trigger: ScheduleTrigger,
        *,
        workflows: Optional[ETLWorkflowEngine] = None,
    ) -> PipelineSchedule:
        if not workflow_name:
            raise ValueError("workflow_name is required")
        if workflows is not None and not workflows.workflow_exists(workflow_name):
            raise UnknownWorkflowError(workflow_name)
        next_run_at = _compute_next_run(trigger)
        now = datetime.now(timezone.utc)
        record = PipelineSchedule(
            schedule_id=uuid4().hex,
            workflow_name=workflow_name,
            trigger=trigger,
            status="active",
            next_run_at=next_run_at,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._schedules[record.schedule_id] = record
        return record

    def reschedule(self, schedule_id: str, trigger: ScheduleTrigger) -> PipelineSchedule:
        next_run_at = _compute_next_run(trigger)
        with self._lock:
            existing = self._schedules.get(schedule_id)
            if existing is None:
                raise UnknownScheduleError(schedule_id)
            updated = replace(
                existing,
                trigger=trigger,
                next_run_at=next_run_at,
                status="active",
                updated_at=datetime.now(timezone.utc),
            )
            self._schedules[schedule_id] = updated
            return updated

    def cancel(self, schedule_id: str) -> PipelineSchedule:
        with self._lock:
            existing = self._schedules.get(schedule_id)
            if existing is None:
                raise UnknownScheduleError(schedule_id)
            updated = replace(
                existing,
                status="cancelled",
                next_run_at=None,
                updated_at=datetime.now(timezone.utc),
            )
            self._schedules[schedule_id] = updated
            return updated

    def get(self, schedule_id: str) -> PipelineSchedule:
        with self._lock:
            schedule = self._schedules.get(schedule_id)
        if schedule is None:
            raise UnknownScheduleError(schedule_id)
        return schedule

    def list_schedules(self) -> list:
        with self._lock:
            items = list(self._schedules.values())
        return sorted(items, key=lambda schedule: schedule.created_at)

    def upcoming(self, *, limit: Optional[int] = None) -> list:
        with self._lock:
            items = list(self._schedules.values())
        due = [schedule for schedule in items if schedule.status == "active" and schedule.next_run_at is not None]
        due.sort(key=lambda schedule: schedule.next_run_at)
        if limit is not None:
            due = due[:limit]
        return due

    def due(self, now: Optional[datetime] = None) -> list:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            items = list(self._schedules.values())
        ready = [
            schedule
            for schedule in items
            if schedule.status == "active" and schedule.next_run_at is not None and schedule.next_run_at <= now
        ]
        ready.sort(key=lambda schedule: schedule.next_run_at)
        return ready

    def mark_dispatched(self, schedule_id: str, *, now: Optional[datetime] = None) -> PipelineSchedule:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            existing = self._schedules.get(schedule_id)
            if existing is None:
                raise UnknownScheduleError(schedule_id)
            if existing.trigger.trigger_type == TriggerType.ONE_TIME:
                updated = replace(existing, status="completed", next_run_at=None, updated_at=now)
            else:
                updated = replace(existing, next_run_at=_compute_next_run(existing.trigger, now), updated_at=now)
            self._schedules[schedule_id] = updated
            return updated


_pipeline_scheduler = PipelineScheduler()


def get_pipeline_scheduler() -> PipelineScheduler:
    return _pipeline_scheduler


router = APIRouter(prefix="/pipelines/schedules", tags=["pipeline-schedules"])


@router.post("", status_code=201)
def create_schedule_endpoint(
    payload: dict = Body(default={}),
    scheduler: PipelineScheduler = Depends(get_pipeline_scheduler),
    workflows: ETLWorkflowEngine = Depends(get_etl_workflow_engine),
) -> dict:
    try:
        trigger = ScheduleTrigger.from_dict(payload.get("trigger"))
    except (ValueError, InvalidTriggerError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        schedule = scheduler.schedule(payload.get("workflow_name", ""), trigger, workflows=workflows)
    except UnknownWorkflowError:
        raise HTTPException(status_code=404, detail="unknown workflow")
    except (ValueError, InvalidTriggerError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return schedule.to_dict()


@router.get("")
def list_schedules_endpoint(
    upcoming: bool = False,
    limit: Optional[int] = None,
    scheduler: PipelineScheduler = Depends(get_pipeline_scheduler),
) -> list:
    if upcoming:
        return [schedule.to_dict() for schedule in scheduler.upcoming(limit=limit)]
    return [schedule.to_dict() for schedule in scheduler.list_schedules()]


@router.put("/{schedule}")
def reschedule_endpoint(
    schedule: str,
    payload: dict = Body(default={}),
    scheduler: PipelineScheduler = Depends(get_pipeline_scheduler),
) -> dict:
    try:
        trigger = ScheduleTrigger.from_dict(payload.get("trigger"))
    except (ValueError, InvalidTriggerError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        updated = scheduler.reschedule(schedule, trigger)
    except UnknownScheduleError:
        raise HTTPException(status_code=404, detail="unknown schedule")
    except InvalidTriggerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return updated.to_dict()


@router.delete("/{schedule}", status_code=204)
def cancel_schedule_endpoint(
    schedule: str,
    scheduler: PipelineScheduler = Depends(get_pipeline_scheduler),
) -> None:
    try:
        scheduler.cancel(schedule)
    except UnknownScheduleError:
        raise HTTPException(status_code=404, detail="unknown schedule")
