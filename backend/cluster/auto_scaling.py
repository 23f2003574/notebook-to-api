from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .distributed_scheduler import DistributedScheduler, get_distributed_scheduler
from .worker_discovery import WorkerDiscoveryService, get_worker_discovery_service
from .worker_registry import WorkerMetadata, WorkerRegistry, get_worker_registry


@dataclass(frozen=True)
class ScalingPolicy:
    """Thresholds and limits governing when the cluster should grow or shrink."""

    name: str = "default"
    min_workers: int = 1
    max_workers: int = 10
    scale_up_step: int = 1
    scale_down_step: int = 1
    scale_up_queue_threshold: int = 5
    scale_down_queue_threshold: int = 0
    scale_up_cpu_threshold: float = 80.0
    scale_down_cpu_threshold: float = 20.0
    scale_up_memory_threshold: float = 80.0
    cooldown_seconds: float = 60.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "min_workers": self.min_workers,
            "max_workers": self.max_workers,
            "scale_up_step": self.scale_up_step,
            "scale_down_step": self.scale_down_step,
            "scale_up_queue_threshold": self.scale_up_queue_threshold,
            "scale_down_queue_threshold": self.scale_down_queue_threshold,
            "scale_up_cpu_threshold": self.scale_up_cpu_threshold,
            "scale_down_cpu_threshold": self.scale_down_cpu_threshold,
            "scale_up_memory_threshold": self.scale_up_memory_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }


@dataclass(frozen=True)
class ScalingDecision:
    """A single scaling verdict: what was decided (and whether it was actually carried out)."""

    capability: str
    action: str
    worker_delta: int
    reason: str
    triggers: tuple
    evaluated_at: datetime
    executed: bool = False

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "action": self.action,
            "worker_delta": self.worker_delta,
            "reason": self.reason,
            "triggers": list(self.triggers),
            "evaluated_at": self.evaluated_at.isoformat(),
            "executed": self.executed,
        }


class AutoScalingEngine:
    """Watches queue pressure and resource utilization, growing or shrinking the worker pool."""

    def __init__(
        self,
        registry: WorkerRegistry,
        discovery: WorkerDiscoveryService,
        scheduler: DistributedScheduler,
        *,
        policy: Optional[ScalingPolicy] = None,
    ) -> None:
        self._registry = registry
        self._discovery = discovery
        self._scheduler = scheduler
        self._policy = policy or ScalingPolicy()
        self._history: list = []
        self._last_scale_at: Optional[datetime] = None
        self._provisioned_count = 0
        self._lock = Lock()

    def evaluate(
        self,
        capability: str,
        *,
        cpu_utilization: float = 0.0,
        memory_utilization: float = 0.0,
    ) -> ScalingDecision:
        policy = self._policy
        report = self._scheduler.capacity_report(capability=capability)
        worker_count = report["worker_count"]
        queue_length = report["reservations_active"]

        triggers = []
        if queue_length >= policy.scale_up_queue_threshold:
            triggers.append("queue_length")
        if cpu_utilization >= policy.scale_up_cpu_threshold:
            triggers.append("cpu_utilization")
        if memory_utilization >= policy.scale_up_memory_threshold:
            triggers.append("memory_usage")

        if triggers:
            if worker_count < policy.max_workers:
                action = "scale_up"
                worker_delta = min(policy.scale_up_step, policy.max_workers - worker_count)
                reason = f"triggered by {', '.join(triggers)} at {worker_count} workers"
            else:
                action = "hold"
                worker_delta = 0
                reason = f"trigger(s) {', '.join(triggers)} fired but already at max_workers ({policy.max_workers})"
        elif (
            queue_length <= policy.scale_down_queue_threshold
            and cpu_utilization <= policy.scale_down_cpu_threshold
            and worker_count > policy.min_workers
        ):
            action = "scale_down"
            worker_delta = -min(policy.scale_down_step, worker_count - policy.min_workers)
            triggers = ["worker_count", "cpu_utilization"]
            reason = f"low utilization at {worker_count} workers"
        else:
            action = "hold"
            worker_delta = 0
            reason = f"steady state at {worker_count} workers"

        if action != "hold" and not self._cooldown_elapsed():
            action, worker_delta = "hold", 0
            reason = "cooldown active; holding until it expires"

        decision = ScalingDecision(
            capability=capability,
            action=action,
            worker_delta=worker_delta,
            reason=reason,
            triggers=tuple(triggers),
            evaluated_at=datetime.now(timezone.utc),
        )
        return self._record(decision)

    def scale_up(self, capability: str, count: int = 1, *, reason: str = "manual scale up") -> ScalingDecision:
        if count <= 0:
            raise ValueError("count must be positive")

        provisioned = []
        with self._lock:
            for _ in range(count):
                self._provisioned_count += 1
                worker_id = f"autoscale-{self._provisioned_count}"
                self._registry.register(
                    worker_id,
                    [capability],
                    WorkerMetadata(hostname=f"{worker_id}.autoscale.local", region="auto", version="auto"),
                )
                provisioned.append(worker_id)
            self._last_scale_at = datetime.now(timezone.utc)

        decision = ScalingDecision(
            capability=capability,
            action="scale_up",
            worker_delta=len(provisioned),
            reason=reason,
            triggers=("manual",),
            evaluated_at=datetime.now(timezone.utc),
            executed=True,
        )
        return self._record(decision)

    def scale_down(self, capability: str, count: int = 1, *, reason: str = "manual scale down") -> ScalingDecision:
        if count <= 0:
            raise ValueError("count must be positive")

        candidates = sorted(
            self._registry.list_workers(capability=capability),
            key=lambda worker: (self._discovery.get_load(worker.worker_id), worker.worker_id),
        )
        removed = []
        with self._lock:
            for worker in candidates[:count]:
                if self._registry.unregister(worker.worker_id):
                    removed.append(worker.worker_id)
            self._last_scale_at = datetime.now(timezone.utc)

        decision = ScalingDecision(
            capability=capability,
            action="scale_down",
            worker_delta=-len(removed),
            reason=reason,
            triggers=("manual",),
            evaluated_at=datetime.now(timezone.utc),
            executed=True,
        )
        return self._record(decision)

    def recommend(
        self,
        capability: str,
        *,
        cpu_utilization: float = 0.0,
        memory_utilization: float = 0.0,
    ) -> ScalingDecision:
        decision = self.evaluate(capability, cpu_utilization=cpu_utilization, memory_utilization=memory_utilization)
        if decision.action == "scale_up":
            return self.scale_up(capability, decision.worker_delta, reason=decision.reason)
        if decision.action == "scale_down":
            return self.scale_down(capability, abs(decision.worker_delta), reason=decision.reason)
        return decision

    def get_history(self, *, capability: Optional[str] = None) -> list:
        with self._lock:
            history = list(self._history)
        if capability is not None:
            history = [decision for decision in history if decision.capability == capability]
        return history

    def _cooldown_elapsed(self) -> bool:
        if self._last_scale_at is None:
            return True
        return (datetime.now(timezone.utc) - self._last_scale_at).total_seconds() >= self._policy.cooldown_seconds

    def _record(self, decision: ScalingDecision) -> ScalingDecision:
        with self._lock:
            self._history.append(decision)
        return decision


_auto_scaling_engine = AutoScalingEngine(
    get_worker_registry(), get_worker_discovery_service(), get_distributed_scheduler()
)


def get_auto_scaling_engine() -> AutoScalingEngine:
    return _auto_scaling_engine


router = APIRouter(prefix="/cluster/scaling", tags=["auto-scaling"])


@router.post("/evaluate")
def evaluate_endpoint(
    payload: dict,
    engine: AutoScalingEngine = Depends(get_auto_scaling_engine),
) -> dict:
    try:
        decision = engine.evaluate(
            payload["capability"],
            cpu_utilization=payload.get("cpu_utilization", 0.0),
            memory_utilization=payload.get("memory_utilization", 0.0),
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return decision.to_dict()


@router.post("/up")
def scale_up_endpoint(
    payload: dict,
    engine: AutoScalingEngine = Depends(get_auto_scaling_engine),
) -> dict:
    try:
        decision = engine.scale_up(payload["capability"], payload.get("count", 1))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return decision.to_dict()


@router.post("/down")
def scale_down_endpoint(
    payload: dict,
    engine: AutoScalingEngine = Depends(get_auto_scaling_engine),
) -> dict:
    try:
        decision = engine.scale_down(payload["capability"], payload.get("count", 1))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return decision.to_dict()


@router.get("/history")
def history_endpoint(
    capability: Optional[str] = None,
    engine: AutoScalingEngine = Depends(get_auto_scaling_engine),
) -> list:
    return [decision.to_dict() for decision in engine.get_history(capability=capability)]
