from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .deployment_governance_lifecycle import GovernanceLifecycleManager
    from .deployment_governance_policy import GovernancePolicyEngine
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_event_bus import GovernanceEventBus

RecoveryActionResult = Union[bool, "tuple[bool, str | None]"]

BUILT_IN_RECOVERY_STRATEGIES: "tuple[str, ...]" = (
    "restart_component",
    "reload_component",
    "reinitialize_component",
    "no_op",
)

_OPERATION_COMPONENT_RECOVERY = "component_recovery"


@dataclass(frozen=True)
class RecoveryPlan:
    """
    A named, registered recovery plan: which strategy to use for a
    component, how many times to retry, and how long to wait between
    attempts.
    """

    component: str

    strategy: str

    max_attempts: int

    retry_delay_seconds: int

    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.component:
            raise ValueError("component must not be empty")

        if not self.strategy:
            raise ValueError("strategy must not be empty")

        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "strategy": self.strategy,
            "max_attempts": self.max_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RecoveryResult:
    """
    The immutable outcome of one recover() call.
    """

    component: str

    strategy: str

    success: bool

    attempts: int

    started_at: datetime

    completed_at: datetime

    message: "str | None"

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError("attempts must be >= 0")

        if self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")

        if self.completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")

        if self.success and self.message is not None:
            raise ValueError(
                "message must not be set when success is True"
            )

        if not self.success and self.message is None:
            raise ValueError(
                "message must be set when success is False"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "strategy": self.strategy,
            "success": self.success,
            "attempts": self.attempts,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "message": self.message,
        }


@dataclass
class _RegisteredPlan:
    definition: RecoveryPlan
    action: "Callable[[str, dict[str, Any]], RecoveryActionResult]"


class GovernanceRecoveryManager:
    """
    Detects and recovers failed governance components: a registered
    RecoveryPlan names a strategy and a retry budget for one
    component, and recover()/recover_all() execute it, coordinating
    with every other governance subsystem along the way — policy
    authorizes the attempt, the audit trail records the outcome, and
    the event bus is told about every step.

    Built-in strategies (restart_component, reload_component,
    reinitialize_component) delegate to a configured
    GovernanceLifecycleManager's matching per-component method;
    no_op is a placeholder that always succeeds without doing
    anything. register() also accepts an explicit action callable for
    a fully custom strategy, bypassing the built-in lookup entirely.
    """

    def __init__(
        self,
        *,
        lifecycle_manager: "GovernanceLifecycleManager | None" = None,
        policy_engine: "GovernancePolicyEngine | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        clock: "Callable[[], datetime] | None" = None,
        sleep: "Callable[[float], None] | None" = None,
    ) -> None:
        self._plans: "dict[str, _RegisteredPlan]" = {}

        self._history: "list[RecoveryResult]" = []

        self._lifecycle_manager = lifecycle_manager

        self._policy_engine = policy_engine

        self._audit_service = audit_service

        self._event_bus = event_bus

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._sleep = sleep or time.sleep

    def register(
        self,
        component: str,
        *,
        strategy: str,
        max_attempts: int = 3,
        retry_delay_seconds: int = 1,
        enabled: bool = True,
        action: (
            "Callable[[str, dict[str, Any]], RecoveryActionResult] | None"
        ) = None,
    ) -> RecoveryPlan:
        """
        Register a new recovery plan for component.

        If action is omitted, strategy must be one of
        BUILT_IN_RECOVERY_STRATEGIES; if action is given, it is used
        regardless of what strategy is named (a custom callable
        strategy registration), so strategy becomes just a label in
        that case.

        Raises ValueError if component already has a registered plan,
        or if no action was given and strategy is not a built-in.
        """

        if component in self._plans:
            raise ValueError(
                f"recovery plan for component '{component}' is "
                "already registered"
            )

        if action is None:
            builtin = self._built_in_strategies().get(strategy)

            if builtin is None:
                raise ValueError(
                    f"unknown recovery strategy '{strategy}'; pass "
                    "an explicit action for a custom strategy"
                )

            action = builtin

        definition = RecoveryPlan(
            component=component,
            strategy=strategy,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
            enabled=enabled,
        )

        self._plans[component] = _RegisteredPlan(
            definition=definition, action=action
        )

        return definition

    def remove(self, component: str) -> None:
        """
        Remove a registered recovery plan.

        Raises KeyError if component has no registered plan.
        """

        if component not in self._plans:
            raise KeyError(
                f"no recovery plan registered for component "
                f"'{component}'"
            )

        del self._plans[component]

    def recover(
        self,
        component: str,
        context: "dict[str, Any] | None" = None,
    ) -> RecoveryResult:
        """
        Attempt to recover component according to its registered
        plan, retrying up to max_attempts times with exponential
        backoff (retry_delay_seconds * 2**(attempt - 1) between
        attempts) until the strategy's action succeeds.

        Requires policy approval before any attempt is made — a
        denying policy (or a disabled plan) aborts immediately with a
        failed result (attempts=0) and a "recovery_aborted" event,
        rather than raising: recover() always produces a
        RecoveryResult, never an exception.

        Every terminal outcome (aborted, succeeded, or exhausted) is
        recorded in the audit trail and published to the event bus.

        Raises KeyError if component has no registered plan.
        """

        context = context or {}

        entry = self._plans.get(component)

        if entry is None:
            raise KeyError(
                f"no recovery plan registered for component "
                f"'{component}'"
            )

        plan = entry.definition

        started_at = self._clock()

        if not plan.enabled:
            return self._abort(
                plan, started_at, "recovery plan is disabled"
            )

        if self._policy_engine is not None:
            from .deployment_governance_policy import (
                GovernancePolicyViolation,
            )

            try:
                self._policy_engine.authorize(
                    _OPERATION_COMPONENT_RECOVERY,
                    {"component": component, **context},
                    audit_service=self._audit_service,
                )

            except GovernancePolicyViolation as exc:
                return self._abort(
                    plan,
                    started_at,
                    f"recovery denied by policy: {exc.decision.reason}",
                )

        self._publish(
            "recovery_started",
            component,
            {"strategy": plan.strategy},
        )

        success = False
        message = None
        attempts = 0

        for attempt in range(1, plan.max_attempts + 1):
            attempts = attempt

            try:
                outcome = entry.action(component, context)

            except Exception as exc:
                outcome = (False, str(exc))

            if isinstance(outcome, tuple):
                success, message = outcome

            else:
                success, message = bool(outcome), None

            if success:
                message = None
                break

            if attempt < plan.max_attempts:
                self._publish(
                    "recovery_retry",
                    component,
                    {"attempt": attempt, "message": message},
                )

                self._sleep(
                    plan.retry_delay_seconds * (2 ** (attempt - 1))
                )

        if not success and message is None:
            message = (
                f"recovery for '{component}' did not succeed after "
                f"{attempts} attempt(s)"
            )

        result = RecoveryResult(
            component=component,
            strategy=plan.strategy,
            success=success,
            attempts=attempts,
            started_at=started_at,
            completed_at=self._clock(),
            message=message,
        )

        self._history.append(result)

        event_type = "recovery_succeeded" if success else "recovery_failed"

        self._audit(event_type, result)
        self._publish(event_type, component, result.to_dict())

        return result

    def recover_all(
        self,
        context: "dict[str, Any] | None" = None,
    ) -> "tuple[RecoveryResult, ...]":
        """
        Attempt recovery for every registered plan, in deterministic
        (component name) order.
        """

        return tuple(
            self.recover(component, context)
            for component in sorted(self._plans)
        )

    def history(
        self,
        component: "str | None" = None,
        limit: int = 100,
    ) -> "tuple[RecoveryResult, ...]":
        """
        Return recorded recovery results, newest first, optionally
        filtered to one component, capped at limit.
        """

        results = [
            result
            for result in self._history
            if component is None or result.component == component
        ]

        results.reverse()

        return tuple(results[:limit])

    def status(self) -> "tuple[RecoveryPlan, ...]":
        """
        Return every registered recovery plan, ordered by component
        name for deterministic output.
        """

        return tuple(
            self._plans[name].definition for name in sorted(self._plans)
        )

    def clear_history(self) -> None:
        """
        Remove every recorded recovery result. Does not affect
        registered plans.
        """

        self._history.clear()

    def _abort(
        self,
        plan: RecoveryPlan,
        started_at: datetime,
        message: str,
    ) -> RecoveryResult:
        result = RecoveryResult(
            component=plan.component,
            strategy=plan.strategy,
            success=False,
            attempts=0,
            started_at=started_at,
            completed_at=self._clock(),
            message=message,
        )

        self._history.append(result)

        self._audit("recovery_aborted", result)

        self._publish("recovery_aborted", plan.component, result.to_dict())

        return result

    def _audit(self, action: str, result: RecoveryResult) -> None:
        if self._audit_service is None:
            return

        from .deployment_governance_audit import record_recovery_result

        record_recovery_result(self._audit_service, action, result)

    def _publish(
        self,
        event_type: str,
        component: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(event_type, source=component, payload=payload)

    def _built_in_strategies(
        self,
    ) -> "dict[str, Callable[[str, dict[str, Any]], RecoveryActionResult]]":
        return {
            "restart_component": self._strategy_restart_component,
            "reload_component": self._strategy_reload_component,
            "reinitialize_component": self._strategy_reinitialize_component,
            "no_op": self._strategy_no_op,
        }

    def _strategy_restart_component(
        self, component: str, context: "dict[str, Any]"
    ) -> RecoveryActionResult:
        if self._lifecycle_manager is None:
            return False, "no lifecycle manager configured for recovery"

        self._lifecycle_manager.restart_component(component)

        return True

    def _strategy_reload_component(
        self, component: str, context: "dict[str, Any]"
    ) -> RecoveryActionResult:
        if self._lifecycle_manager is None:
            return False, "no lifecycle manager configured for recovery"

        self._lifecycle_manager.reload_component(component)

        return True

    def _strategy_reinitialize_component(
        self, component: str, context: "dict[str, Any]"
    ) -> RecoveryActionResult:
        if self._lifecycle_manager is None:
            return False, "no lifecycle manager configured for recovery"

        self._lifecycle_manager.reinitialize_component(component)

        return True

    def _strategy_no_op(
        self, component: str, context: "dict[str, Any]"
    ) -> RecoveryActionResult:
        return True


def build_default_governance_recovery_manager() -> GovernanceRecoveryManager:
    """
    Build the process-wide governance recovery manager, wired to
    every other process-wide governance singleton, with a
    "restart_component" plan pre-registered for each of the lifecycle
    manager's default components.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_lifecycle import get_lifecycle_manager
    from .deployment_governance_policy import get_policy_engine

    manager = GovernanceRecoveryManager(
        lifecycle_manager=get_lifecycle_manager(),
        policy_engine=get_policy_engine(),
        audit_service=get_audit_service(),
        event_bus=get_event_bus(),
    )

    for component in (
        "provider_registry",
        "metrics_bootstrap",
        "logging_bootstrap",
        "delivery_runtime",
        "health_service",
        "readiness_service",
        "liveness_service",
        "diagnostics_service",
    ):
        manager.register(
            component,
            strategy="restart_component",
            max_attempts=3,
            retry_delay_seconds=1,
        )

    return manager


# Shared for the lifetime of the process: recovery plans registered
# through the API need to be visible to whatever triggers recovery
# (the health service, or a direct API caller).
_recovery_manager = build_default_governance_recovery_manager()


def get_recovery_manager() -> GovernanceRecoveryManager:
    """
    Return the process-wide governance recovery manager.
    """

    return _recovery_manager
