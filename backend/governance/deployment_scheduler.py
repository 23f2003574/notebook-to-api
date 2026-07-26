from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_workflow import DeploymentWorkflowEngine

SCHEDULE_STATUSES = ("PENDING", "CANCELLED", "COMPLETED")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownScheduleError(KeyError):
    pass


class InvalidScheduleStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchedulePolicy:
    """Recurrence and eligibility rules for a scheduled deployment."""

    recurrence_seconds: Optional[int] = None
    window_start_hour: int = 0
    window_end_hour: int = 24
    max_runs: Optional[int] = None

    def __post_init__(self) -> None:
        if self.recurrence_seconds is not None and self.recurrence_seconds <= 0:
            raise ValueError("recurrence_seconds must be positive")
        if not 0 <= self.window_start_hour < self.window_end_hour <= 24:
            raise ValueError(
                "window_start_hour must be less than window_end_hour, within 0-24"
            )
        if self.max_runs is not None and self.max_runs <= 0:
            raise ValueError("max_runs must be positive")

    def is_within_window(self, moment: datetime) -> bool:
        return self.window_start_hour <= moment.hour < self.window_end_hour

    def to_dict(self) -> dict:
        return {
            "recurrence_seconds": self.recurrence_seconds,
            "window_start_hour": self.window_start_hour,
            "window_end_hour": self.window_end_hour,
            "max_runs": self.max_runs,
        }


@dataclass(frozen=True)
class ScheduledDeployment:
    """An immutable snapshot of one scheduled pipeline deployment."""

    schedule_id: str
    pipeline: str
    run_at: datetime
    priority: int
    policy: SchedulePolicy
    status: str
    runs_completed: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "schedule_id": self.schedule_id,
            "pipeline": self.pipeline,
            "run_at": self.run_at.isoformat(),
            "priority": self.priority,
            "policy": self.policy.to_dict(),
            "status": self.status,
            "runs_completed": self.runs_completed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeploymentScheduler:
    """Queues and dispatches pipeline deployments for one-time or recurring execution."""

    def __init__(self, workflow_engine: Optional[DeploymentWorkflowEngine] = None) -> None:
        self._schedules: dict[str, ScheduledDeployment] = {}
        self._lock = Lock()
        self._workflow_engine = workflow_engine

    def schedule(
        self,
        pipeline: str,
        run_at: datetime,
        *,
        priority: int = 0,
        policy: Optional[SchedulePolicy] = None,
        timestamp: Optional[datetime] = None,
    ) -> ScheduledDeployment:
        if not pipeline:
            raise ValueError("pipeline is required")

        now = timestamp or datetime.now(timezone.utc)
        deployment = ScheduledDeployment(
            schedule_id=_new_id(),
            pipeline=pipeline,
            run_at=run_at,
            priority=priority,
            policy=policy or SchedulePolicy(),
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._schedules[deployment.schedule_id] = deployment
        return deployment

    def reschedule(
        self,
        schedule_id: str,
        *,
        run_at: Optional[datetime] = None,
        priority: Optional[int] = None,
        policy: Optional[SchedulePolicy] = None,
        timestamp: Optional[datetime] = None,
    ) -> ScheduledDeployment:
        existing = self.get(schedule_id)
        if existing.status != "PENDING":
            raise InvalidScheduleStateError(
                f"cannot reschedule a schedule in status {existing.status}"
            )

        updated = replace(
            existing,
            run_at=run_at if run_at is not None else existing.run_at,
            priority=priority if priority is not None else existing.priority,
            policy=policy if policy is not None else existing.policy,
            updated_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._schedules[schedule_id] = updated
        return updated

    def cancel(
        self, schedule_id: str, *, timestamp: Optional[datetime] = None
    ) -> ScheduledDeployment:
        existing = self.get(schedule_id)
        if existing.status != "PENDING":
            raise InvalidScheduleStateError(
                f"cannot cancel a schedule in status {existing.status}"
            )

        updated = replace(
            existing, status="CANCELLED", updated_at=timestamp or datetime.now(timezone.utc)
        )
        with self._lock:
            self._schedules[schedule_id] = updated
        return updated

    def pending(self) -> tuple:
        with self._lock:
            schedules = [s for s in self._schedules.values() if s.status == "PENDING"]
        return tuple(sorted(schedules, key=lambda s: (-s.priority, s.run_at, s.schedule_id)))

    def get(self, schedule_id: str) -> ScheduledDeployment:
        with self._lock:
            deployment = self._schedules.get(schedule_id)
        if deployment is None:
            raise UnknownScheduleError(schedule_id)
        return deployment

    def dispatch_due(
        self,
        *,
        now: Optional[datetime] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
    ) -> tuple:
        engine = workflow_engine or self._workflow_engine
        moment = now or datetime.now(timezone.utc)

        dispatched = []
        for deployment in self.pending():
            if deployment.run_at > moment:
                continue
            if not deployment.policy.is_within_window(moment):
                continue
            if engine is not None:
                active = engine.executions_for(deployment.pipeline)
                if any(execution.status in ("RUNNING", "PAUSED") for execution in active):
                    continue
                engine.start(
                    deployment.pipeline,
                    context={"schedule_id": deployment.schedule_id},
                    timestamp=moment,
                )
            dispatched.append(self._advance(deployment, moment))
        return tuple(dispatched)

    def _advance(self, deployment: ScheduledDeployment, moment: datetime) -> ScheduledDeployment:
        runs_completed = deployment.runs_completed + 1
        policy = deployment.policy
        can_recur = policy.recurrence_seconds is not None and (
            policy.max_runs is None or runs_completed < policy.max_runs
        )

        if can_recur:
            updated = replace(
                deployment,
                run_at=moment + timedelta(seconds=policy.recurrence_seconds),
                runs_completed=runs_completed,
                status="PENDING",
                updated_at=moment,
            )
        else:
            updated = replace(
                deployment, runs_completed=runs_completed, status="COMPLETED", updated_at=moment
            )

        with self._lock:
            self._schedules[deployment.schedule_id] = updated
        return updated


_scheduler = DeploymentScheduler()


def get_deployment_scheduler() -> DeploymentScheduler:
    return _scheduler


router = APIRouter(prefix="/governance", tags=["governance-scheduler"])


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid datetime format")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_policy(payload: Optional[dict]) -> Optional[SchedulePolicy]:
    if payload is None:
        return None
    try:
        return SchedulePolicy(
            recurrence_seconds=payload.get("recurrence_seconds"),
            window_start_hour=payload.get("window_start_hour", 0),
            window_end_hour=payload.get("window_end_hour", 24),
            max_runs=payload.get("max_runs"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/schedules")
def create_schedule(payload: dict = Body(...)) -> dict:
    pipeline = payload.get("pipeline")
    run_at = payload.get("run_at")
    if not pipeline or not run_at:
        raise HTTPException(status_code=422, detail="pipeline and run_at are required")

    try:
        deployment = get_deployment_scheduler().schedule(
            pipeline,
            _parse_datetime(run_at),
            priority=payload.get("priority", 0),
            policy=_parse_policy(payload.get("policy")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return deployment.to_dict()


@router.patch("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, payload: dict = Body(default={})) -> dict:
    run_at = _parse_datetime(payload["run_at"]) if "run_at" in payload else None
    policy = _parse_policy(payload.get("policy")) if "policy" in payload else None

    try:
        deployment = get_deployment_scheduler().reschedule(
            schedule_id,
            run_at=run_at,
            priority=payload.get("priority"),
            policy=policy,
        )
    except UnknownScheduleError:
        raise HTTPException(status_code=404, detail="unknown schedule")
    except InvalidScheduleStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return deployment.to_dict()


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict:
    try:
        deployment = get_deployment_scheduler().cancel(schedule_id)
    except UnknownScheduleError:
        raise HTTPException(status_code=404, detail="unknown schedule")
    except InvalidScheduleStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return deployment.to_dict()


@router.get("/schedules")
def list_schedules() -> list:
    return [deployment.to_dict() for deployment in get_deployment_scheduler().pending()]
