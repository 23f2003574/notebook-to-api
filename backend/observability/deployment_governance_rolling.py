from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_version_registry import is_semantic_version

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )
    from .deployment_governance_traffic_router import (
        DeploymentTrafficRouter,
    )
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )


@dataclass(frozen=True)
class RollingDeployment:
    """
    One deployment's current rolling-update state: how many of its
    total_instances have been moved to target_version so far, and how
    many move per batch.
    """

    deployment_id: str

    target_version: str

    total_instances: int

    updated_instances: int

    batch_size: int

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not is_semantic_version(self.target_version):
            raise ValueError(
                f"target_version '{self.target_version}' is not a "
                "valid semantic version"
            )

        if self.total_instances <= 0:
            raise ValueError("total_instances must be greater than 0")

        if not 0 <= self.updated_instances <= self.total_instances:
            raise ValueError(
                "updated_instances must be between 0 and "
                "total_instances"
            )

        if not 1 <= self.batch_size <= self.total_instances:
            raise ValueError(
                "batch_size must be between 1 and total_instances"
            )

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "target_version": self.target_version,
            "total_instances": self.total_instances,
            "updated_instances": self.updated_instances,
            "batch_size": self.batch_size,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class RollingBatchResult:
    """
    One immutable batch-validation outcome: how many instances were
    on target_version once that batch was applied, and whether it
    passed its health check.
    """

    deployment_id: str

    batch_number: int

    updated_instances: int

    healthy: bool

    completed_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.batch_number < 1:
            raise ValueError("batch_number must be >= 1")

        if self.updated_instances < 0:
            raise ValueError("updated_instances must be >= 0")

        if self.completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "batch_number": self.batch_number,
            "updated_instances": self.updated_instances,
            "healthy": self.healthy,
            "completed_at": self.completed_at.isoformat(),
        }


class RollingDeploymentEngine:
    """
    Upgrades a deployment's instances to target_version one batch at a
    time — next_batch() applies a batch, validate_batch() health-checks
    it — while keeping the rest of the fleet on its current version
    throughout. The Rollout Manager (deployment_governance_
    rollout_manager) delegates strategy="ROLLING" rollout completion to
    this engine — see DeploymentRolloutManager.complete().

    Unlike CanaryDeploymentEngine's evaluate(), a failed
    validate_batch() does not automatically roll back: it pauses the
    rollout (this engine's own "pause on failed validation" rule),
    leaving the operator to inspect, resume once fixed, or call
    rollback() explicitly.

    If a version_registry is wired in, deploy() requires deployment_id
    to already be registered there; unlike Blue/Green and Canary,
    RollingDeployment has no field for the version being rolled away
    from, so there is nothing to resolve from it — only an existence
    check.

    If a scheduler is wired in, deploy() registers a recurring job
    there for the deployment's batch execution interval, unregistered
    again once the rollout reaches a terminal state. As with
    CanaryDeploymentEngine, that job is purely declarative — something
    else is still responsible for actually calling next_batch()/
    validate_batch() when it is due.

    If a metrics service is wired in, every validate_batch() call's
    outcome and duration are recorded into it.

    Thread-safe: every mutation of engine state is guarded by an
    internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        version_registry: "DeploymentVersionRegistry | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        traffic_router: "DeploymentTrafficRouter | None" = None,
        default_batch_percentage: int = 25,
        batch_interval_seconds: int = 60,
    ) -> None:
        if not 1 <= default_batch_percentage <= 100:
            raise ValueError(
                "default_batch_percentage must be between 1 and 100"
            )

        self._lock = threading.Lock()

        self._deployments: "dict[str, RollingDeployment]" = {}

        self._active_deployment_ids: "set[str]" = set()

        self._paused: "dict[str, bool]" = {}

        self._validated: "dict[str, bool]" = {}

        self._batch_number: "dict[str, int]" = {}

        self._history: "dict[str, list[RollingBatchResult]]" = {}

        self._scheduler_jobs: "dict[str, str]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._version_registry = version_registry

        self._scheduler = scheduler

        self._metrics = metrics

        self._traffic_router = traffic_router

        self._default_batch_percentage = default_batch_percentage

        self._batch_interval_seconds = batch_interval_seconds

    def deploy(
        self,
        deployment_id: str,
        target_version: str,
        total_instances: int,
        batch_size: "int | None" = None,
        batch_percentage: "int | None" = None,
    ) -> RollingDeployment:
        """
        Start a new rolling update for deployment_id: total_instances
        instances, none yet updated, moving batch_size (or
        batch_percentage of total_instances, rounded up) at a time.

        Raises ValueError if deployment_id already has an active
        rolling deployment, if both batch_size and batch_percentage
        are given, if either is out of range, if target_version is not
        a valid semantic version, or if a version_registry is wired in
        and deployment_id is not registered there.
        """

        if batch_size is not None and batch_percentage is not None:
            raise ValueError(
                "provide at most one of batch_size or batch_percentage"
            )

        if total_instances <= 0:
            raise ValueError("total_instances must be greater than 0")

        if batch_size is not None:
            if not 1 <= batch_size <= total_instances:
                raise ValueError(
                    "batch_size must be between 1 and total_instances"
                )

            resolved_batch_size = batch_size

        else:
            percentage = (
                batch_percentage
                if batch_percentage is not None
                else self._default_batch_percentage
            )

            if not 1 <= percentage <= 100:
                raise ValueError(
                    "batch_percentage must be between 1 and 100"
                )

            resolved_batch_size = max(
                1, math.ceil(total_instances * percentage / 100)
            )

        with self._lock:
            if deployment_id in self._active_deployment_ids:
                raise ValueError(
                    f"deployment '{deployment_id}' already has an "
                    "active rolling deployment"
                )

            if (
                self._version_registry is not None
                and not self._version_registry.exists(deployment_id)
            ):
                raise ValueError(
                    f"deployment '{deployment_id}' is not registered "
                    "in the version registry"
                )

            now = self._clock()

            record = RollingDeployment(
                deployment_id=deployment_id,
                target_version=target_version,
                total_instances=total_instances,
                updated_instances=0,
                batch_size=resolved_batch_size,
                created_at=now,
            )

            self._deployments[deployment_id] = record
            self._active_deployment_ids.add(deployment_id)
            self._paused[deployment_id] = False
            self._validated[deployment_id] = True
            self._batch_number[deployment_id] = 0
            self._history.setdefault(deployment_id, [])

        if self._scheduler is not None:
            job = self._scheduler.register(
                f"rolling-batch-{deployment_id}",
                interval_seconds=self._batch_interval_seconds,
                namespace="rolling",
                description=(
                    "Batch execution interval for rolling deployment "
                    f"'{deployment_id}'"
                ),
            )

            with self._lock:
                self._scheduler_jobs[deployment_id] = job.job_id

        self._publish(
            "rolling_started",
            deployment_id,
            {
                "target_version": target_version,
                "total_instances": total_instances,
                "batch_size": resolved_batch_size,
            },
        )

        self._route_configure(deployment_id, target_version, 0.0)

        return record

    def next_batch(self, deployment_id: str) -> RollingDeployment:
        """
        Apply the next batch: move up to batch_size more instances
        (never exceeding total_instances) onto target_version.

        Raises KeyError if deployment_id has no rolling deployment
        record, or ValueError if it is not active, is paused, has not
        had its previous batch (if any) validated, or is already
        fully updated.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rolling "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"rolling deployment '{deployment_id}' is not "
                    "active"
                )

            if self._paused.get(deployment_id, False):
                raise ValueError(
                    f"rolling deployment '{deployment_id}' is paused"
                )

            if not self._validated.get(deployment_id, True):
                raise ValueError(
                    f"rolling deployment '{deployment_id}' has an "
                    "unvalidated batch pending"
                )

            if record.updated_instances >= record.total_instances:
                raise ValueError(
                    f"rolling deployment '{deployment_id}' is already "
                    "fully updated"
                )

            new_updated_instances = min(
                record.updated_instances + record.batch_size,
                record.total_instances,
            )

            updated = replace(
                record, updated_instances=new_updated_instances
            )

            self._deployments[deployment_id] = updated
            self._batch_number[deployment_id] += 1
            self._validated[deployment_id] = False

            batch_number = self._batch_number[deployment_id]

        self._publish(
            "rolling_batch_started",
            deployment_id,
            {
                "batch_number": batch_number,
                "updated_instances": updated.updated_instances,
            },
        )

        self._route_allocate(
            deployment_id,
            updated.target_version,
            100.0 * updated.updated_instances / updated.total_instances,
        )

        return updated

    def validate_batch(
        self,
        deployment_id: str,
        check: "Callable[[], bool] | None" = None,
    ) -> RollingBatchResult:
        """
        Validate the most recently applied batch's health.

        check, if given, is called with no arguments and its return
        value determines success; omitted, validation always
        succeeds. A failing validation pauses the rollout (see
        pause()) rather than rolling it back automatically. Reaching a
        fully-updated, healthy state marks the rollout COMPLETED.

        Raises KeyError if deployment_id has no rolling deployment
        record, ValueError if it is not active, or if next_batch() has
        never been called for it.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rolling "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"rolling deployment '{deployment_id}' is not "
                    "active"
                )

            batch_number = self._batch_number.get(deployment_id, 0)

            if batch_number == 0:
                raise ValueError(
                    f"rolling deployment '{deployment_id}' has no "
                    "applied batch to validate; call next_batch() "
                    "first"
                )

            updated_instances = record.updated_instances

            already_paused = self._paused.get(deployment_id, False)

        started = time.monotonic()

        healthy = True if check is None else bool(check())

        execution_ms = (time.monotonic() - started) * 1000

        now = self._clock()

        result = RollingBatchResult(
            deployment_id=deployment_id,
            batch_number=batch_number,
            updated_instances=updated_instances,
            healthy=healthy,
            completed_at=now,
        )

        completed = False

        with self._lock:
            self._history[deployment_id].append(result)

            if healthy:
                self._validated[deployment_id] = True

                if updated_instances >= record.total_instances:
                    self._active_deployment_ids.discard(deployment_id)
                    completed = True

            else:
                self._paused[deployment_id] = True

        if self._metrics is not None:
            if healthy:
                self._metrics.record_completion(
                    execution_ms=execution_ms
                )
            else:
                self._metrics.record_failure(
                    execution_ms=execution_ms
                )

        if healthy:
            self._publish("rolling_batch_completed", deployment_id, {
                "batch_number": batch_number,
                "updated_instances": updated_instances,
            })

            if completed:
                self._unregister_scheduler_job(deployment_id)

                self._publish("rolling_completed", deployment_id, {})

        else:
            self._publish("rolling_failed", deployment_id, {
                "batch_number": batch_number,
            })

            if not already_paused:
                self._publish("rolling_paused", deployment_id, {})

        return result

    def pause(self, deployment_id: str) -> RollingDeployment:
        """
        Pause deployment_id's rolling update, blocking next_batch()
        until resume() is called.

        Idempotent: a no-op (still returning the current record) if
        already paused. Raises KeyError if deployment_id has no
        rolling deployment record, or ValueError if it is not active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rolling "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"rolling deployment '{deployment_id}' is not "
                    "active"
                )

            already_paused = self._paused.get(deployment_id, False)

            self._paused[deployment_id] = True

        if not already_paused:
            self._publish("rolling_paused", deployment_id, {})

        return record

    def resume(self, deployment_id: str) -> RollingDeployment:
        """
        Resume deployment_id's paused rolling update.

        Idempotent: a no-op (still returning the current record) if
        not currently paused. Raises KeyError if deployment_id has no
        rolling deployment record, or ValueError if it is not active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rolling "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"rolling deployment '{deployment_id}' is not "
                    "active"
                )

            was_paused = self._paused.get(deployment_id, False)

            self._paused[deployment_id] = False

        if was_paused:
            self._publish("rolling_resumed", deployment_id, {})

        return record

    def rollback(self, deployment_id: str) -> RollingDeployment:
        """
        Restore deployment_id's already-updated instances to their
        previous version (updated_instances back to 0) and mark the
        rollout terminal, freeing deployment_id for a future deploy().

        Raises KeyError if deployment_id has no rolling deployment
        record, or ValueError if it is not currently active.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rolling "
                    "deployment record"
                )

            if deployment_id not in self._active_deployment_ids:
                raise ValueError(
                    f"rolling deployment '{deployment_id}' is not "
                    "active"
                )

            updated = replace(record, updated_instances=0)

            self._deployments[deployment_id] = updated
            self._active_deployment_ids.discard(deployment_id)
            self._batch_number[deployment_id] = 0
            self._validated[deployment_id] = True

        self._unregister_scheduler_job(deployment_id)

        self._publish("rolling_rolled_back", deployment_id, {})

        self._route_allocate(deployment_id, updated.target_version, 0.0)

        return updated

    def status(self, deployment_id: str) -> RollingDeployment:
        """
        Return deployment_id's current rolling deployment state.

        Raises KeyError if deployment_id has no rolling deployment
        record.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no rolling "
                    "deployment record"
                )

            return record

    def history(
        self, deployment_id: str
    ) -> "tuple[RollingBatchResult, ...]":
        """
        Return every batch validation outcome ever recorded for
        deployment_id, oldest first. Returns an empty tuple if
        deployment_id has never been deployed.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def list(self) -> "tuple[RollingDeployment, ...]":
        """
        Return every currently tracked rolling deployment, ordered
        deterministically by deployment_id.
        """

        with self._lock:
            records = list(self._deployments.values())

        return tuple(
            sorted(records, key=lambda record: record.deployment_id)
        )

    def clear(self) -> None:
        """
        Remove every tracked rolling deployment, its progression
        state, and its history. Does not unregister any scheduler
        jobs — callers resetting a shared scheduler should clear it
        separately.
        """

        with self._lock:
            self._deployments.clear()
            self._active_deployment_ids.clear()
            self._paused.clear()
            self._validated.clear()
            self._batch_number.clear()
            self._history.clear()
            self._scheduler_jobs.clear()

    def _unregister_scheduler_job(self, deployment_id: str) -> None:
        if self._scheduler is None:
            return

        with self._lock:
            job_id = self._scheduler_jobs.pop(deployment_id, None)

        if job_id is not None:
            try:
                self._scheduler.unregister(job_id)

            except KeyError:
                pass

    def _route_configure(
        self, deployment_id: str, target_version: str, percentage: float
    ) -> None:
        if self._traffic_router is None:
            return

        # RollingDeployment has no field for the version instances are
        # being rolled away from, unlike Blue/Green's blue_version or
        # Canary's stable_version — "PREVIOUS" is a sentinel label for
        # "not yet on target_version", not a real version string.
        try:
            self._traffic_router.configure(
                deployment_id,
                [(target_version, percentage), ("PREVIOUS", 100.0 - percentage)],
                strategy="ROLLING",
            )

        except ValueError:
            pass

    def _route_allocate(
        self, deployment_id: str, version: str, percentage: float
    ) -> None:
        if self._traffic_router is None:
            return

        try:
            self._traffic_router.allocate(
                deployment_id, version, percentage
            )

        except (KeyError, ValueError):
            pass

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, Any] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_rolling_engine() -> RollingDeploymentEngine:
    """
    Build the process-wide rolling update engine, wired to the
    process-wide governance event bus, deployment version registry,
    scheduler, scheduler metrics, and traffic router.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )
    from .deployment_governance_traffic_router import get_traffic_router
    from .deployment_governance_version_registry import (
        get_version_registry,
    )

    return RollingDeploymentEngine(
        event_bus=get_event_bus(),
        version_registry=get_version_registry(),
        scheduler=get_scheduler(),
        metrics=get_scheduler_metrics(),
        traffic_router=get_traffic_router(),
    )


# Shared for the lifetime of the process: which deployments have an
# active rolling update, and their progression/history, is inherently
# process-wide, not something that can be meaningfully rebuilt fresh
# per request.
_rolling_engine = build_default_governance_rolling_engine()


def get_rolling_engine() -> RollingDeploymentEngine:
    """
    Return the process-wide rolling update engine.
    """

    return _rolling_engine
