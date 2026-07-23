from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .deployment_governance_version_registry import is_semantic_version

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_version_registry import (
        DeploymentVersionRegistry,
    )

# Which of the two environments is currently serving live traffic.
# BLUE is always where a deployment starts (the environment already
# serving before this engine ever saw it); GREEN is the environment
# a new version is deployed into and validated before switch() cuts
# traffic over.
ENVIRONMENTS: "tuple[str, ...]" = ("BLUE", "GREEN")


def _other_environment(environment: str) -> str:
    return "GREEN" if environment == "BLUE" else "BLUE"


@dataclass(frozen=True)
class BlueGreenDeployment:
    """
    One deployment's current Blue/Green state: which version each
    environment is running, and which one is currently live.
    """

    deployment_id: str

    blue_version: str

    green_version: str

    active_environment: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not is_semantic_version(self.blue_version):
            raise ValueError(
                f"blue_version '{self.blue_version}' is not a valid "
                "semantic version"
            )

        if not is_semantic_version(self.green_version):
            raise ValueError(
                f"green_version '{self.green_version}' is not a "
                "valid semantic version"
            )

        if self.active_environment not in ENVIRONMENTS:
            raise ValueError(
                f"active_environment must be one of {ENVIRONMENTS}"
            )

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "blue_version": self.blue_version,
            "green_version": self.green_version,
            "active_environment": self.active_environment,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class BlueGreenSwitchResult:
    """
    The immutable, terminal outcome of one switch() or rollback()
    call.
    """

    deployment_id: str

    previous_environment: str

    active_environment: str

    switched_at: datetime

    success: bool

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.previous_environment not in ENVIRONMENTS:
            raise ValueError(
                f"previous_environment must be one of {ENVIRONMENTS}"
            )

        if self.active_environment not in ENVIRONMENTS:
            raise ValueError(
                f"active_environment must be one of {ENVIRONMENTS}"
            )

        if self.switched_at.tzinfo is None:
            raise ValueError("switched_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "previous_environment": self.previous_environment,
            "active_environment": self.active_environment,
            "switched_at": self.switched_at.isoformat(),
            "success": self.success,
        }


class BlueGreenDeploymentEngine:
    """
    Manages Blue/Green deployments: deploy a new version into the
    idle environment, validate it, then atomically switch live
    traffic over to it. The Rollout Manager
    (deployment_governance_rollout_manager) delegates strategy=
    "BLUE_GREEN" rollouts to this engine — see
    DeploymentRolloutManager.complete().

    blue_version, when not given explicitly to deploy(), is resolved
    through the Version Registry (deployment_governance_version_
    registry): the currently registered version for deployment_id is
    what BLUE is assumed to already be running.

    Thread-safe: every mutation of engine state is guarded by an
    internal lock. Switching traffic is atomic: the active_environment
    flip, previous-environment bookkeeping, and history append all
    happen under one lock acquisition — no caller can observe a
    partially-switched state.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        version_registry: "DeploymentVersionRegistry | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._deployments: "dict[str, BlueGreenDeployment]" = {}

        self._validated: "dict[str, bool]" = {}

        self._history: "dict[str, list[BlueGreenSwitchResult]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._version_registry = version_registry

    def deploy(
        self,
        deployment_id: str,
        green_version: str,
        blue_version: "str | None" = None,
    ) -> BlueGreenDeployment:
        """
        Deploy green_version into deployment_id's idle (non-active)
        environment.

        Re-enterable: calling deploy() again for a deployment_id that
        already has a Blue/Green record replaces its idle
        environment's version with a fresh green_version and clears
        any prior validation — deploying a new candidate always
        requires validating it again before the next switch().

        If blue_version is omitted, it is resolved as deployment_id's
        currently registered version in the Version Registry this
        engine was built with. Raises ValueError if blue_version is
        omitted and no version_registry is wired, or if green_version
        is not a valid semantic version.
        """

        with self._lock:
            existing = self._deployments.get(deployment_id)

            active_environment = (
                existing.active_environment if existing else "BLUE"
            )

            # The version currently live in the active slot never
            # changes here — deploy() only ever writes the idle slot.
            # An explicit blue_version always wins; otherwise, on a
            # redeploy, the active slot's existing version is reused,
            # and on a fresh deploy (active_environment is always
            # "BLUE" then) it is resolved from the version registry.
            if blue_version is not None:
                active_version = blue_version
            elif existing is not None:
                active_version = (
                    existing.green_version
                    if active_environment == "GREEN"
                    else existing.blue_version
                )
            elif self._version_registry is not None:
                active_version = self._version_registry.get(
                    deployment_id
                ).version
            else:
                raise ValueError(
                    "blue_version must be provided when no "
                    "version_registry is wired"
                )

            now = self._clock()

            if active_environment == "BLUE":
                record = BlueGreenDeployment(
                    deployment_id=deployment_id,
                    blue_version=active_version,
                    green_version=green_version,
                    active_environment="BLUE",
                    created_at=now,
                )
            else:
                record = BlueGreenDeployment(
                    deployment_id=deployment_id,
                    blue_version=green_version,
                    green_version=active_version,
                    active_environment="GREEN",
                    created_at=now,
                )

            self._deployments[deployment_id] = record
            self._validated[deployment_id] = False
            self._history.setdefault(deployment_id, [])

        self._publish(
            "blue_green_started",
            deployment_id,
            {
                "blue_version": record.blue_version,
                "green_version": record.green_version,
            },
        )

        return record

    def validate(
        self,
        deployment_id: str,
        check: "Callable[[], bool] | None" = None,
    ) -> bool:
        """
        Validate deployment_id's currently deployed idle (green, in
        the common case) environment, gating switch().

        check, if given, is called with no arguments and its return
        value determines success; omitted, validation always succeeds
        (there is no live environment for this engine to probe on its
        own). Raises KeyError if deployment_id has never been
        deployed.
        """

        with self._lock:
            if deployment_id not in self._deployments:
                raise KeyError(
                    f"deployment '{deployment_id}' has no Blue/Green "
                    "record"
                )

            passed = True if check is None else bool(check())

            self._validated[deployment_id] = passed

        if passed:
            self._publish(
                "green_environment_ready", deployment_id, {}
            )

        return passed

    def switch(self, deployment_id: str) -> BlueGreenSwitchResult:
        """
        Atomically switch deployment_id's live traffic to its
        currently idle environment.

        Raises KeyError if deployment_id has never been deployed, or
        ValueError if its idle environment has not been validated
        (validate() must be called, and pass, after every deploy()
        before switch() will proceed).
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no Blue/Green "
                    "record"
                )

            if not self._validated.get(deployment_id, False):
                raise ValueError(
                    f"deployment '{deployment_id}' has not been "
                    "validated"
                )

            previous_environment = record.active_environment
            new_environment = _other_environment(previous_environment)

            self._deployments[deployment_id] = replace(
                record, active_environment=new_environment
            )

            self._validated[deployment_id] = False

            now = self._clock()

            result = BlueGreenSwitchResult(
                deployment_id=deployment_id,
                previous_environment=previous_environment,
                active_environment=new_environment,
                switched_at=now,
                success=True,
            )

            self._history[deployment_id].append(result)

        self._publish(
            "traffic_switched",
            deployment_id,
            {
                "previous_environment": previous_environment,
                "active_environment": new_environment,
            },
        )

        self._publish("blue_green_completed", deployment_id, {})

        return result

    def rollback(self, deployment_id: str) -> BlueGreenSwitchResult:
        """
        Restore deployment_id's previously active environment,
        reverting its most recent switch().

        Raises KeyError if deployment_id has never been deployed, or
        ValueError if it has never been switched (there is nothing to
        roll back to).
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no Blue/Green "
                    "record"
                )

            if not self._history.get(deployment_id):
                raise ValueError(
                    f"deployment '{deployment_id}' has never been "
                    "switched; nothing to roll back"
                )

            previous_environment = record.active_environment
            restored_environment = _other_environment(
                previous_environment
            )

            self._deployments[deployment_id] = replace(
                record, active_environment=restored_environment
            )

            self._validated[deployment_id] = False

            now = self._clock()

            result = BlueGreenSwitchResult(
                deployment_id=deployment_id,
                previous_environment=previous_environment,
                active_environment=restored_environment,
                switched_at=now,
                success=True,
            )

            self._history[deployment_id].append(result)

        self._publish(
            "blue_green_rollback",
            deployment_id,
            {
                "previous_environment": previous_environment,
                "active_environment": restored_environment,
            },
        )

        return result

    def status(self, deployment_id: str) -> BlueGreenDeployment:
        """
        Return deployment_id's current Blue/Green state.

        Raises KeyError if deployment_id has never been deployed.
        """

        with self._lock:
            record = self._deployments.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' has no Blue/Green "
                    "record"
                )

            return record

    def history(
        self, deployment_id: str
    ) -> "tuple[BlueGreenSwitchResult, ...]":
        """
        Return every switch()/rollback() outcome ever recorded for
        deployment_id, oldest first. Returns an empty tuple if
        deployment_id has never been deployed or never switched.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def list(self) -> "tuple[BlueGreenDeployment, ...]":
        """
        Return every currently tracked Blue/Green deployment, ordered
        deterministically by deployment_id.
        """

        with self._lock:
            records = list(self._deployments.values())

        return tuple(
            sorted(records, key=lambda record: record.deployment_id)
        )

    def clear(self) -> None:
        """
        Remove every tracked Blue/Green deployment, its validation
        state, and its history.
        """

        with self._lock:
            self._deployments.clear()
            self._validated.clear()
            self._history.clear()

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


def build_default_governance_blue_green_engine() -> (
    BlueGreenDeploymentEngine
):
    """
    Build the process-wide Blue/Green deployment engine, wired to the
    process-wide governance event bus and deployment version registry.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_version_registry import (
        get_version_registry,
    )

    return BlueGreenDeploymentEngine(
        event_bus=get_event_bus(),
        version_registry=get_version_registry(),
    )


# Shared for the lifetime of the process: which environment is live
# for each deployment, and its switch history, is inherently
# process-wide, not something that can be meaningfully rebuilt fresh
# per request.
_blue_green_engine = build_default_governance_blue_green_engine()


def get_blue_green_engine() -> BlueGreenDeploymentEngine:
    """
    Return the process-wide Blue/Green deployment engine.
    """

    return _blue_green_engine
