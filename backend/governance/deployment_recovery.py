from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Iterable, Mapping, Optional

from fastapi import APIRouter, Body, HTTPException, Query

RECOVERY_STATUSES = ("SUCCEEDED", "FAILED")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownStrategyError(KeyError):
    pass


class UnknownDeploymentError(KeyError):
    pass


class NoApplicableStrategyError(RuntimeError):
    pass


class RecoveryStrategy:
    """Common interface every recovery strategy must implement."""

    def __init__(self, name: str, *, priority: int = 100) -> None:
        self.name = name
        self.priority = priority

    def can_handle(self, context: Mapping[str, Any]) -> bool:
        return True

    def execute(self, context: Mapping[str, Any]) -> str:
        """Return a human-readable outcome message, or raise on failure."""

        raise NotImplementedError


class RollbackStrategy(RecoveryStrategy):
    def __init__(self, *, priority: int = 10) -> None:
        super().__init__("rollback", priority=priority)

    def can_handle(self, context: Mapping[str, Any]) -> bool:
        return bool(context.get("has_previous_version", True))

    def execute(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"rolled back deployment {deployment} to the previous version"


class RetryDeploymentStrategy(RecoveryStrategy):
    def __init__(self, *, priority: int = 20, max_attempts: int = 3) -> None:
        super().__init__("retry_deployment", priority=priority)
        self._max_attempts = max_attempts

    def can_handle(self, context: Mapping[str, Any]) -> bool:
        return context.get("attempt", 1) < self._max_attempts

    def execute(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"retried deployment {deployment}"


class PauseRolloutStrategy(RecoveryStrategy):
    def __init__(self, *, priority: int = 30) -> None:
        super().__init__("pause_rollout", priority=priority)

    def execute(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"paused rollout for deployment {deployment}"


class ManualInterventionStrategy(RecoveryStrategy):
    def __init__(self, *, priority: int = 1000) -> None:
        super().__init__("manual_intervention", priority=priority)

    def execute(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"flagged deployment {deployment} for manual intervention"


@dataclass(frozen=True)
class RecoveryRecord:
    """One immutable record of a recovery attempt."""

    recovery_id: str
    deployment: str
    strategy: str
    status: str
    message: str
    started_at: datetime
    completed_at: datetime
    source: str = "manual"
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "recovery_id": self.recovery_id,
            "deployment": self.deployment,
            "strategy": self.strategy,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "source": self.source,
            "context": dict(self.context),
        }


class DeploymentRecoveryCoordinator:
    """Selects and executes recovery strategies after deployment failures."""

    def __init__(self, strategies: Optional[Iterable[RecoveryStrategy]] = None) -> None:
        self._strategies: dict[str, RecoveryStrategy] = {}
        self._history: list[RecoveryRecord] = []
        self._latest_by_deployment: dict[str, RecoveryRecord] = {}
        self._lock = Lock()
        for strategy in (
            (
                RollbackStrategy(),
                RetryDeploymentStrategy(),
                PauseRolloutStrategy(),
                ManualInterventionStrategy(),
            )
            if strategies is None
            else strategies
        ):
            self.register_strategy(strategy)

    def register_strategy(self, strategy: RecoveryStrategy) -> None:
        with self._lock:
            self._strategies[strategy.name] = strategy

    def recover(
        self,
        context: Mapping[str, Any],
        *,
        strategy_name: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        source: str = "manual",
    ) -> RecoveryRecord:
        deployment = context.get("deployment")
        if not deployment:
            raise ValueError("context must provide a 'deployment' identifier")

        with self._lock:
            if strategy_name is not None:
                strategy = self._strategies.get(strategy_name)
                if strategy is None:
                    raise UnknownStrategyError(strategy_name)
                candidates = [strategy]
            else:
                candidates = sorted(
                    (s for s in self._strategies.values() if s.can_handle(context)),
                    key=lambda s: s.priority,
                )

        if not candidates:
            raise NoApplicableStrategyError(deployment)

        started_at = timestamp or datetime.now(timezone.utc)
        last_strategy_name = candidates[-1].name
        last_error: Optional[str] = None
        for strategy in candidates:
            try:
                message = strategy.execute(context)
            except Exception as exc:  # noqa: BLE001 - strategy failures are data
                last_error = str(exc)
                last_strategy_name = strategy.name
                continue
            return self._store(
                deployment=deployment,
                strategy=strategy.name,
                status="SUCCEEDED",
                message=message,
                context=context,
                started_at=started_at,
                timestamp=timestamp,
                source=source,
            )

        return self._store(
            deployment=deployment,
            strategy=last_strategy_name,
            status="FAILED",
            message=last_error or "all recovery strategies failed",
            context=context,
            started_at=started_at,
            timestamp=timestamp,
            source=source,
        )

    def status(
        self, deployment: Optional[str] = None
    ) -> "RecoveryRecord | list[RecoveryRecord]":
        with self._lock:
            if deployment is not None:
                record = self._latest_by_deployment.get(deployment)
                if record is None:
                    raise UnknownDeploymentError(deployment)
                return record
            return list(self._latest_by_deployment.values())

    def history(self, deployment: Optional[str] = None) -> list[RecoveryRecord]:
        with self._lock:
            records = list(self._history)
        if deployment is not None:
            records = [r for r in records if r.deployment == deployment]
        return records

    def _store(
        self,
        *,
        deployment: str,
        strategy: str,
        status: str,
        message: str,
        context: Mapping[str, Any],
        started_at: datetime,
        timestamp: Optional[datetime],
        source: str = "manual",
    ) -> RecoveryRecord:
        record = RecoveryRecord(
            recovery_id=_new_id(),
            deployment=deployment,
            strategy=strategy,
            status=status,
            message=message,
            started_at=started_at,
            completed_at=timestamp or datetime.now(timezone.utc),
            source=source,
            context=dict(context),
        )
        with self._lock:
            self._history.append(record)
            self._latest_by_deployment[deployment] = record
        return record


_coordinator = DeploymentRecoveryCoordinator()


def get_deployment_recovery_coordinator() -> DeploymentRecoveryCoordinator:
    return _coordinator


router = APIRouter(prefix="/governance", tags=["governance-recovery"])


@router.post("/recovery/start")
def start_recovery(payload: dict = Body(...)) -> dict:
    deployment = payload.get("deployment")
    if not deployment:
        raise HTTPException(status_code=422, detail="deployment is required")
    strategy_name = payload.get("strategy")
    try:
        record = get_deployment_recovery_coordinator().recover(
            payload, strategy_name=strategy_name
        )
    except UnknownStrategyError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown strategy: {exc.args[0]}"
        )
    except NoApplicableStrategyError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"no applicable recovery strategy for {exc.args[0]}",
        )
    return record.to_dict()


@router.get("/recovery/status")
def recovery_status(deployment: Optional[str] = Query(default=None)):
    coordinator = get_deployment_recovery_coordinator()
    if deployment is not None:
        try:
            record = coordinator.status(deployment)
        except UnknownDeploymentError:
            raise HTTPException(
                status_code=404, detail="no recovery recorded for deployment"
            )
        return record.to_dict()
    return [record.to_dict() for record in coordinator.status()]


@router.get("/recovery/history")
def recovery_history(deployment: Optional[str] = Query(default=None)) -> list[dict]:
    records = get_deployment_recovery_coordinator().history(deployment=deployment)
    return [record.to_dict() for record in records]
