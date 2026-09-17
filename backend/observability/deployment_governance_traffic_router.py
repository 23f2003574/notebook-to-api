from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_rollout_policy import (
        DeploymentRolloutPolicyEngine,
    )
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )

_ALLOCATION_TOLERANCE = 1e-6

# The routing strategies this router ships a builtin rebalance() rule
# for. Not an exhaustive closed set — register_strategy() accepts any
# other name, this is only what configure()/update() default to
# validating against when no custom strategy has been registered
# under that name.
ROUTING_STRATEGIES: "tuple[str, ...]" = (
    "STATIC",
    "BLUE_GREEN",
    "WEIGHTED",
    "CANARY",
    "ROLLING",
    "PROGRESSIVE",
)


@dataclass(frozen=True)
class TrafficAllocation:
    """
    What share of a deployment's traffic one version currently
    receives.
    """

    deployment_id: str

    version: str

    percentage: float

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not self.version:
            raise ValueError("version must not be empty")

        if self.percentage < 0:
            raise ValueError("percentage must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "version": self.version,
            "percentage": self.percentage,
        }


@dataclass(frozen=True)
class RoutingSnapshot:
    """
    One deployment's complete routing table at a point in time.

    allocations may be empty (see DeploymentTrafficRouter.reset()) —
    the "total allocation must equal 100%" rule is enforced by the
    router's write methods (configure/update/allocate/rebalance)
    before a snapshot is ever constructed from caller-supplied
    percentages, not by this dataclass itself, precisely so a
    deliberately-empty "nothing configured" snapshot stays
    representable.
    """

    deployment_id: str

    allocations: "tuple[TrafficAllocation, ...]"

    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "allocations": [
                allocation.to_dict() for allocation in self.allocations
            ],
            "updated_at": self.updated_at.isoformat(),
        }


def _equal_split_rebalance(
    allocations: "tuple[TrafficAllocation, ...]",
) -> "tuple[TrafficAllocation, ...]":
    """
    The builtin rebalance rule for every strategy except STATIC:
    redistribute 100% evenly across whichever versions are currently
    allocated, regardless of their previous split.
    """

    if not allocations:
        return allocations

    share = 100.0 / len(allocations)

    return tuple(
        TrafficAllocation(
            deployment_id=allocation.deployment_id,
            version=allocation.version,
            percentage=share,
        )
        for allocation in allocations
    )


def _static_rebalance(
    allocations: "tuple[TrafficAllocation, ...]",
) -> "tuple[TrafficAllocation, ...]":
    """
    STATIC's rebalance rule: unchanged — a statically-routed
    deployment does not dynamically redistribute traffic.
    """

    return allocations


class DeploymentTrafficRouter:
    """
    The shared traffic-routing layer Blue/Green, Canary, Rolling, and
    Progressive Delivery all delegate traffic changes to, instead of
    each strategy tracking (and validating) its own routing table
    independently.

    Every write (configure/update/allocate/rebalance) runs the same
    flow: build the candidate allocation set, validate it (total
    100%, no negatives), and only then atomically replace the stored
    snapshot and append it to history — a rejected candidate never
    touches stored state.

    Custom routing strategies can be registered via register_strategy
    with a callable of the same shape as the builtins
    (tuple[TrafficAllocation, ...] -> tuple[TrafficAllocation, ...]);
    rebalance() looks the deployment's configured strategy up in this
    registry.

    Thread-safe: every mutation is guarded by an internal lock, and a
    full snapshot swap (store + history append) happens as a single
    critical section — no caller can observe a routing table
    mid-update.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        metrics: "GovernanceSchedulerMetrics | None" = None,
        policy_engine: "DeploymentRolloutPolicyEngine | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._snapshots: "dict[str, RoutingSnapshot]" = {}

        self._strategy: "dict[str, str]" = {}

        self._history: "dict[str, list[RoutingSnapshot]]" = {}

        self._strategies: "dict[str, Callable[..., tuple]]" = {
            name: _static_rebalance if name == "STATIC"
            else _equal_split_rebalance
            for name in ROUTING_STRATEGIES
        }

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._metrics = metrics

        self._policy_engine = policy_engine

    def set_policy_engine(
        self, policy_engine: "DeploymentRolloutPolicyEngine"
    ) -> None:
        """
        Wire policy_engine in after construction — see
        CanaryDeploymentEngine.set_health_engine for why this exists
        instead of a constructor-injected singleton.
        """

        self._policy_engine = policy_engine

    def register_strategy(
        self,
        name: str,
        rebalance_fn: (
            "Callable[[tuple[TrafficAllocation, ...]], "
            "tuple[TrafficAllocation, ...]]"
        ),
    ) -> None:
        """
        Register (or override) the rebalance() rule used for
        strategy name, the strategy interface future custom routing
        strategies plug into.
        """

        if not name:
            raise ValueError("name must not be empty")

        with self._lock:
            self._strategies[name] = rebalance_fn

    def validate(
        self, allocations: "Sequence[TrafficAllocation]"
    ) -> bool:
        """
        Whether allocations is a valid routing table: every
        percentage non-negative (already enforced per-allocation by
        TrafficAllocation itself, rechecked here for a caller-built
        sequence of arbitrary objects) and summing to exactly 100
        (within floating-point tolerance).
        """

        if not allocations:
            return False

        if any(allocation.percentage < 0 for allocation in allocations):
            return False

        total = sum(allocation.percentage for allocation in allocations)

        return abs(total - 100.0) <= _ALLOCATION_TOLERANCE

    def configure(
        self,
        deployment_id: str,
        allocations: "Sequence[tuple[str, float]]",
        strategy: str = "STATIC",
    ) -> RoutingSnapshot:
        """
        Replace deployment_id's entire routing table with allocations
        (version, percentage) pairs, under strategy.

        Raises ValueError if strategy has not been registered (built
        in or via register_strategy), or if the resulting allocation
        set fails validate().
        """

        with self._lock:
            if strategy not in self._strategies:
                raise ValueError(
                    f"strategy '{strategy}' is not registered"
                )

        return self._write(
            deployment_id, allocations, strategy=strategy,
            event_type="routing_configured",
        )

    def update(
        self,
        deployment_id: str,
        allocations: "Sequence[tuple[str, float]]",
    ) -> RoutingSnapshot:
        """
        Replace deployment_id's entire routing table with allocations,
        keeping its currently configured strategy.

        Raises KeyError if deployment_id has never been configured, or
        ValueError if the resulting allocation set fails validate().
        """

        with self._lock:
            if deployment_id not in self._snapshots:
                raise KeyError(
                    f"deployment '{deployment_id}' has no routing "
                    "configuration"
                )

            strategy = self._strategy[deployment_id]

        return self._write(
            deployment_id, allocations, strategy=strategy,
            event_type="routing_updated",
        )

    def allocate(
        self, deployment_id: str, version: str, percentage: float
    ) -> RoutingSnapshot:
        """
        Set version's allocation to percentage, proportionally
        rescaling every other currently-allocated version so the
        total stays 100 (each other version keeps its relative share
        of whatever is left; if every other version was previously at
        0, the remainder is split evenly across them instead).

        Raises KeyError if deployment_id has never been configured,
        ValueError if percentage is out of [0, 100], or ValueError if
        a wired policy_engine denies the "traffic_shift" action.

        Reads the current snapshot and writes the recomputed one
        within a single lock acquisition — unlike configure()/
        update(), whose candidate allocations come entirely from the
        caller and so carry no risk of clobbering a concurrent write,
        this method's candidate is *derived from* the current
        snapshot, so read-then-write has to be one atomic step, not
        two.
        """

        if not 0 <= percentage <= 100:
            raise ValueError("percentage must be between 0 and 100")

        with self._lock:
            strategy = self._strategy.get(deployment_id)

        self._check_policy(
            deployment_id, {"strategy": strategy, "version": version}
        )

        return self._recompute(
            deployment_id,
            event_type="routing_updated",
            recompute=lambda current, strategy: (
                self._rescale_for_allocation(current, version, percentage)
            ),
        )

    def rebalance(self, deployment_id: str) -> RoutingSnapshot:
        """
        Reapply deployment_id's configured strategy's rebalance rule
        to its current allocation set.

        Raises KeyError if deployment_id has never been configured, or
        ValueError if the strategy's rule produces an invalid
        allocation set.

        Reads and writes within a single lock acquisition, for the
        same reason allocate() does.
        """

        return self._recompute(
            deployment_id,
            event_type="routing_rebalanced",
            recompute=lambda current, strategy: [
                (allocation.version, allocation.percentage)
                for allocation in self._strategies[strategy](
                    current.allocations
                )
            ],
        )

    def _rescale_for_allocation(
        self,
        current: RoutingSnapshot,
        version: str,
        percentage: float,
    ) -> "list[tuple[str, float]]":
        others = [
            allocation
            for allocation in current.allocations
            if allocation.version != version
        ]

        remaining = 100.0 - percentage
        others_total = sum(
            allocation.percentage for allocation in others
        )

        if others_total > _ALLOCATION_TOLERANCE:
            scale = remaining / others_total

            rescaled = [
                (allocation.version, allocation.percentage * scale)
                for allocation in others
            ]

        elif others:
            share = remaining / len(others)

            rescaled = [
                (allocation.version, share) for allocation in others
            ]

        else:
            rescaled = []

        return rescaled + [(version, percentage)]

    def _recompute(
        self,
        deployment_id: str,
        *,
        event_type: str,
        recompute: (
            "Callable[[RoutingSnapshot, str], Sequence[tuple[str, float]]]"
        ),
    ) -> RoutingSnapshot:
        with self._lock:
            current = self._snapshots.get(deployment_id)

            if current is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no routing "
                    "configuration"
                )

            strategy = self._strategy[deployment_id]

            candidate = recompute(current, strategy)

            built = tuple(
                sorted(
                    (
                        TrafficAllocation(
                            deployment_id=deployment_id,
                            version=version,
                            percentage=percentage,
                        )
                        for version, percentage in candidate
                    ),
                    key=lambda allocation: allocation.version,
                )
            )

            valid = self.validate(built)

            if valid:
                now = self._clock()

                snapshot = RoutingSnapshot(
                    deployment_id=deployment_id, allocations=built,
                    updated_at=now,
                )

                self._snapshots[deployment_id] = snapshot
                self._strategy[deployment_id] = strategy
                self._history.setdefault(deployment_id, []).append(
                    snapshot
                )

        if self._metrics is not None:
            self._metrics.record_policy_decision(allowed=valid)

        if not valid:
            self._publish(
                "routing_validation_failed", deployment_id,
                {"strategy": strategy},
            )

            raise ValueError(
                "routing allocations must be non-negative and total "
                "100%"
            )

        self._publish(event_type, deployment_id, {"strategy": strategy})

        return snapshot

    def reset(self, deployment_id: str) -> RoutingSnapshot:
        """
        Clear deployment_id's routing table to an empty allocation
        set, without validating it (an empty table represents "no
        active routing configuration," a deliberate exception to the
        100%-total rule the other write methods enforce).

        Idempotent regardless of prior state — works whether
        deployment_id was previously configured, already reset, or
        never seen before.
        """

        with self._lock:
            now = self._clock()

            snapshot = RoutingSnapshot(
                deployment_id=deployment_id, allocations=(),
                updated_at=now,
            )

            self._snapshots[deployment_id] = snapshot
            self._strategy.setdefault(deployment_id, "STATIC")
            self._history.setdefault(deployment_id, []).append(
                snapshot
            )

        self._publish("routing_reset", deployment_id, {})

        return snapshot

    def snapshot(self, deployment_id: str) -> RoutingSnapshot:
        """
        Return deployment_id's current routing snapshot.

        Raises KeyError if deployment_id has never been configured (or
        reset).
        """

        with self._lock:
            current = self._snapshots.get(deployment_id)

            if current is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no routing "
                    "configuration"
                )

            return current

    def history(
        self, deployment_id: str
    ) -> "tuple[RoutingSnapshot, ...]":
        """
        Return every routing snapshot ever recorded for deployment_id,
        oldest first. Returns an empty tuple if deployment_id has
        never been configured.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def list(self) -> "tuple[RoutingSnapshot, ...]":
        """
        Return every currently tracked deployment's routing snapshot,
        ordered deterministically by deployment_id.
        """

        with self._lock:
            snapshots = list(self._snapshots.values())

        return tuple(
            sorted(
                snapshots, key=lambda snapshot: snapshot.deployment_id
            )
        )

    def clear(self) -> None:
        """
        Remove every tracked deployment's routing state and history.
        """

        with self._lock:
            self._snapshots.clear()
            self._strategy.clear()
            self._history.clear()

    def _write(
        self,
        deployment_id: str,
        allocations: "Sequence[tuple[str, float]]",
        *,
        strategy: str,
        event_type: str,
    ) -> RoutingSnapshot:
        self._check_policy(deployment_id, {"strategy": strategy})

        built = tuple(
            sorted(
                (
                    TrafficAllocation(
                        deployment_id=deployment_id,
                        version=version,
                        percentage=percentage,
                    )
                    for version, percentage in allocations
                ),
                key=lambda allocation: allocation.version,
            )
        )

        valid = self.validate(built)

        if self._metrics is not None:
            self._metrics.record_policy_decision(allowed=valid)

        if not valid:
            self._publish(
                "routing_validation_failed", deployment_id,
                {"strategy": strategy},
            )

            raise ValueError(
                "routing allocations must be non-negative and total "
                "100%"
            )

        with self._lock:
            now = self._clock()

            snapshot = RoutingSnapshot(
                deployment_id=deployment_id, allocations=built,
                updated_at=now,
            )

            self._snapshots[deployment_id] = snapshot
            self._strategy[deployment_id] = strategy
            self._history.setdefault(deployment_id, []).append(
                snapshot
            )

        self._publish(
            event_type, deployment_id, {"strategy": strategy},
        )

        return snapshot

    def _check_policy(
        self, deployment_id: str, context: "dict[str, Any]"
    ) -> None:
        """
        Raise ValueError if a wired policy_engine denies the
        "traffic_shift" action for deployment_id. A no-op if no
        policy_engine is wired.
        """

        if self._policy_engine is None:
            return

        decision = self._policy_engine.evaluate(
            deployment_id, "traffic_shift", context
        )

        if not decision.allowed:
            raise ValueError(
                f"traffic_shift denied by policy '{decision.policy}': "
                f"{decision.reason}"
            )

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


def build_default_governance_traffic_router() -> DeploymentTrafficRouter:
    """
    Build the process-wide deployment traffic router, wired to the
    process-wide governance event bus and scheduler metrics.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )

    return DeploymentTrafficRouter(
        event_bus=get_event_bus(), metrics=get_scheduler_metrics(),
    )


# Shared for the lifetime of the process: every deployment's current
# routing table needs to be visible to every caller (every rollout
# engine, and API readers), which cannot be meaningfully rebuilt fresh
# per request.
_traffic_router = build_default_governance_traffic_router()


def get_traffic_router() -> DeploymentTrafficRouter:
    """
    Return the process-wide deployment traffic router.
    """

    return _traffic_router
