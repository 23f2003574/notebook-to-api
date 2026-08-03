from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .worker_discovery import WorkerDiscoveryService, get_worker_discovery_service

_VALID_POLICIES = {"least_loaded", "capability_aware", "priority", "affinity"}
_REBALANCE_MARGIN = 1


@dataclass(frozen=True)
class SchedulingDecision:
    """The outcome of evaluating candidate workers for a single job."""

    worker_id: Optional[str]
    score: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "score": self.score,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SchedulingPlan:
    """A scheduling decision recorded for a specific job."""

    job_id: str
    capability: str
    policy: str
    decision: SchedulingDecision
    planned_at: datetime

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "capability": self.capability,
            "policy": self.policy,
            "decision": self.decision.to_dict(),
            "planned_at": self.planned_at.isoformat(),
        }


class DistributedScheduler:
    """Plans workload placement across healthy workers and reserves capacity ahead of dispatch."""

    def __init__(self, discovery: WorkerDiscoveryService, *, default_policy: str = "capability_aware") -> None:
        self._discovery = discovery
        self._default_policy = default_policy
        self._plans: dict = {}
        self._reservations: dict = {}
        self._affinity: dict = {}
        self._metrics = {"scheduled": 0, "rebalanced": 0}
        self._lock = Lock()

    def schedule(
        self,
        job_id: str,
        capability: str,
        *,
        priority: int = 0,
        policy: Optional[str] = None,
        affinity_worker_id: Optional[str] = None,
    ) -> SchedulingPlan:
        policy = policy or self._default_policy
        if policy not in _VALID_POLICIES:
            raise ValueError(f"unsupported scheduling policy '{policy}'; expected one of {sorted(_VALID_POLICIES)}")

        with self._lock:
            if affinity_worker_id is not None:
                self._affinity[job_id] = affinity_worker_id

            candidates = self._discovery.available_workers(capability=capability)
            decision = self._decide(job_id, priority, policy, candidates)
            plan = SchedulingPlan(
                job_id=job_id,
                capability=capability,
                policy=policy,
                decision=decision,
                planned_at=datetime.now(timezone.utc),
            )
            self._plans[job_id] = plan
            self._metrics["scheduled"] += 1
            return plan

    def reserve(self, job_id: str) -> bool:
        with self._lock:
            plan = self._plans.get(job_id)
            if plan is None:
                raise KeyError(job_id)
            if plan.decision.worker_id is None or job_id in self._reservations:
                return False
            self._reservations[job_id] = plan.decision.worker_id
            return True

    def release(self, job_id: str) -> bool:
        with self._lock:
            return self._reservations.pop(job_id, None) is not None

    def rebalance(self) -> list:
        with self._lock:
            reserved = list(self._reservations.items())

        moved = []
        for job_id, current_worker_id in reserved:
            plan = self._plans.get(job_id)
            if plan is None:
                continue

            candidates = self._discovery.available_workers(capability=plan.capability)
            candidate_ids = {worker.worker_id for worker in candidates}
            if not candidates:
                continue

            needs_move = current_worker_id not in candidate_ids
            best = min(candidates, key=lambda worker: (self._effective_load(worker.worker_id), worker.worker_id))
            if not needs_move and best.worker_id != current_worker_id:
                if self._effective_load(best.worker_id) + _REBALANCE_MARGIN < self._effective_load(current_worker_id):
                    needs_move = True

            if not needs_move:
                continue

            decision = SchedulingDecision(
                worker_id=best.worker_id,
                score=self._effective_load(best.worker_id),
                reason=f"rebalanced from '{current_worker_id}'",
            )
            new_plan = SchedulingPlan(
                job_id=job_id,
                capability=plan.capability,
                policy=plan.policy,
                decision=decision,
                planned_at=datetime.now(timezone.utc),
            )
            with self._lock:
                self._plans[job_id] = new_plan
                self._reservations[job_id] = best.worker_id
                self._metrics["rebalanced"] += 1
            moved.append(new_plan)
        return moved

    def get_plan(self, job_id: str) -> Optional[SchedulingPlan]:
        return self._plans.get(job_id)

    def stats(self) -> dict:
        with self._lock:
            return {
                "scheduled": self._metrics["scheduled"],
                "rebalanced": self._metrics["rebalanced"],
                "reservations_active": len(self._reservations),
            }

    def _decide(self, job_id: str, priority: int, policy: str, candidates: list) -> SchedulingDecision:
        if not candidates:
            return SchedulingDecision(worker_id=None, score=float("inf"), reason="no available worker with required capability")

        if policy == "affinity":
            preferred = self._affinity.get(job_id)
            match = next((worker for worker in candidates if worker.worker_id == preferred), None) if preferred else None
            if match is not None:
                return SchedulingDecision(
                    worker_id=match.worker_id,
                    score=self._effective_load(match.worker_id),
                    reason=f"affinity to '{preferred}'",
                )
            fallback = self._least_loaded(candidates)
            return SchedulingDecision(
                worker_id=fallback.worker_id,
                score=self._effective_load(fallback.worker_id),
                reason="affinity target unavailable; fell back to least loaded",
            )

        if policy == "capability_aware":
            best = min(candidates, key=lambda worker: (len(worker.capabilities), self._effective_load(worker.worker_id), worker.worker_id))
            return SchedulingDecision(worker_id=best.worker_id, score=self._effective_load(best.worker_id), reason="best capability fit")

        if policy == "priority":
            if priority >= 5:
                best = self._least_loaded(candidates)
                return SchedulingDecision(worker_id=best.worker_id, score=self._effective_load(best.worker_id), reason="high priority; placed on least loaded")
            best = sorted(candidates, key=lambda worker: worker.worker_id)[0]
            return SchedulingDecision(worker_id=best.worker_id, score=self._effective_load(best.worker_id), reason="standard priority placement")

        best = self._least_loaded(candidates)
        return SchedulingDecision(worker_id=best.worker_id, score=self._effective_load(best.worker_id), reason="least loaded")

    def _least_loaded(self, candidates: list):
        return min(candidates, key=lambda worker: (self._effective_load(worker.worker_id), worker.worker_id))

    def _effective_load(self, worker_id: str) -> int:
        reserved = sum(1 for held_worker_id in self._reservations.values() if held_worker_id == worker_id)
        return self._discovery.get_load(worker_id) + reserved


_distributed_scheduler = DistributedScheduler(get_worker_discovery_service())


def get_distributed_scheduler() -> DistributedScheduler:
    return _distributed_scheduler


router = APIRouter(prefix="/cluster", tags=["distributed-scheduler"])


@router.post("/schedule")
def schedule_endpoint(
    payload: dict,
    scheduler: DistributedScheduler = Depends(get_distributed_scheduler),
) -> dict:
    try:
        plan = scheduler.schedule(
            job_id=payload["job_id"],
            capability=payload["capability"],
            priority=payload.get("priority", 0),
            policy=payload.get("policy"),
            affinity_worker_id=payload.get("affinity_worker_id"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return plan.to_dict()


@router.post("/rebalance")
def rebalance_endpoint(
    scheduler: DistributedScheduler = Depends(get_distributed_scheduler),
) -> list:
    return [plan.to_dict() for plan in scheduler.rebalance()]


@router.get("/schedule/{job_id}")
def get_schedule_endpoint(
    job_id: str,
    scheduler: DistributedScheduler = Depends(get_distributed_scheduler),
) -> dict:
    plan = scheduler.get_plan(job_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"no scheduling plan for job '{job_id}'")
    return plan.to_dict()


@router.get("/scheduler/stats")
def scheduler_stats_endpoint(
    scheduler: DistributedScheduler = Depends(get_distributed_scheduler),
) -> dict:
    return scheduler.stats()
