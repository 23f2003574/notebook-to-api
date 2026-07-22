from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_job_registry import GovernanceJobRegistry

# The trigger types this engine understands natively. A custom
# trigger_type not in this tuple may still be registered by passing an
# explicit evaluator callable to register(), the same way
# GovernanceRecoveryManager accepts a custom action for a strategy
# name outside BUILT_IN_RECOVERY_STRATEGIES — this is the plug-in
# point for future trigger types (cron, event-based, calendar) without
# changing this engine's own code.
BUILT_IN_TRIGGER_TYPES: "tuple[str, ...]" = (
    "interval",
    "one_shot",
    "manual",
    "immediate",
)

TriggerEvaluator = Callable[["TriggerDefinition", datetime], bool]


@dataclass(frozen=True)
class TriggerDefinition:
    """
    A single trigger's identity and current scheduling state: which
    job it fires, what kind of trigger it is, and (for time-based
    types) when it is next due.
    """

    trigger_id: str

    job_id: str

    trigger_type: str

    enabled: bool

    next_run: "datetime | None"

    def __post_init__(self) -> None:
        if not self.trigger_id:
            raise ValueError("trigger_id must not be empty")

        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if not self.trigger_type:
            raise ValueError("trigger_type must not be empty")

        if self.next_run is not None and self.next_run.tzinfo is None:
            raise ValueError(
                "next_run must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger_id": self.trigger_id,
            "job_id": self.job_id,
            "trigger_type": self.trigger_type,
            "enabled": self.enabled,
            "next_run": (
                self.next_run.isoformat()
                if self.next_run is not None
                else None
            ),
        }


@dataclass(frozen=True)
class TriggerEvaluation:
    """
    The immutable outcome of evaluating one trigger at one point in
    time.
    """

    trigger_id: str

    should_run: bool

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.trigger_id:
            raise ValueError("trigger_id must not be empty")

        if self.evaluated_at.tzinfo is None:
            raise ValueError(
                "evaluated_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger_id": self.trigger_id,
            "should_run": self.should_run,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class GovernanceTriggerEngine:
    """
    Computes when registered jobs are eligible to run, decoupled from
    the Scheduler's own orchestration: the Scheduler drives ticks and
    execution, this engine only answers "is this trigger due right
    now?" for each of the four built-in trigger types —

    - interval / one_shot: due once evaluated_at reaches next_run;
      unlike interval, a one_shot trigger is expected to be removed
      (or otherwise not rescheduled) by the caller once it has fired,
      since this engine's evaluate()/evaluate_all() never mutate a
      trigger as a side effect of evaluating it.
    - manual: never due automatically — evaluate() always reports
      should_run=False for it, by design; something outside this
      engine's own time-based evaluation is what runs a manual
      trigger.
    - immediate: always due while enabled, regardless of next_run —
      "run once on startup" is enforced by the caller removing or
      disabling it after the first eligible evaluation, not by this
      engine tracking whether it has already fired.

    If constructed with a job_registry, register() validates that
    job_id actually names a registered job before accepting a new
    trigger for it — enforced here (not in the job registry itself,
    which stays independent of execution state) since it is this
    engine's own referential-integrity concern.

    Thread-safe: every mutation is guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        job_registry: "GovernanceJobRegistry | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._triggers: "dict[str, TriggerDefinition]" = {}

        self._evaluators: "dict[str, TriggerEvaluator]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._job_registry = job_registry

    def register(
        self,
        job_id: str,
        *,
        trigger_type: str,
        next_run: "datetime | None" = None,
        enabled: bool = True,
        evaluator: "TriggerEvaluator | None" = None,
    ) -> TriggerDefinition:
        """
        Register a new trigger for job_id under a fresh, unique
        trigger_id.

        If evaluator is omitted, trigger_type must be one of
        BUILT_IN_TRIGGER_TYPES; if evaluator is given, it is used
        regardless of what trigger_type is named (a custom pluggable
        trigger type).

        Raises ValueError if job_id is not a registered job (only
        checked when this engine was constructed with a job_registry)
        or if no evaluator was given and trigger_type is not a
        built-in.
        """

        if (
            self._job_registry is not None
            and not self._job_registry.exists(job_id)
        ):
            raise ValueError(
                f"job '{job_id}' is not registered"
            )

        if evaluator is None:
            evaluator = self._built_in_evaluators().get(trigger_type)

            if evaluator is None:
                raise ValueError(
                    f"unknown trigger type '{trigger_type}'; pass an "
                    "explicit evaluator for a custom type"
                )

        trigger = TriggerDefinition(
            trigger_id=str(uuid4()),
            job_id=job_id,
            trigger_type=trigger_type,
            enabled=enabled,
            next_run=next_run,
        )

        with self._lock:
            self._triggers[trigger.trigger_id] = trigger
            self._evaluators[trigger.trigger_id] = evaluator

        self._publish(
            "trigger_registered",
            trigger.trigger_id,
            {"job_id": job_id, "trigger_type": trigger_type},
        )

        return trigger

    def remove(self, trigger_id: str) -> None:
        """
        Remove a registered trigger.

        Raises KeyError if trigger_id is not registered.
        """

        with self._lock:
            trigger = self._triggers.pop(trigger_id, None)

            if trigger is None:
                raise KeyError(
                    f"trigger '{trigger_id}' is not registered"
                )

            self._evaluators.pop(trigger_id, None)

        self._publish(
            "trigger_removed", trigger_id, {"job_id": trigger.job_id}
        )

    def evaluate(
        self, trigger_id: str, *, at: "datetime | None" = None
    ) -> TriggerEvaluation:
        """
        Evaluate whether trigger_id is due to run at at (default now).

        Publishes "trigger_fired" when the result is should_run=True.

        Raises KeyError if trigger_id is not registered.
        """

        at = at or self._clock()

        with self._lock:
            trigger = self._triggers.get(trigger_id)

            if trigger is None:
                raise KeyError(
                    f"trigger '{trigger_id}' is not registered"
                )

            evaluator = self._evaluators[trigger_id]

        should_run = self._evaluate_trigger(trigger, evaluator, at)

        if should_run:
            self._publish(
                "trigger_fired", trigger_id, {"job_id": trigger.job_id}
            )

        return TriggerEvaluation(
            trigger_id=trigger_id, should_run=should_run, evaluated_at=at
        )

    def evaluate_all(
        self, *, at: "datetime | None" = None
    ) -> "tuple[TriggerEvaluation, ...]":
        """
        Evaluate every registered trigger at at (default now), in
        deterministic (next_run, trigger_id) order.

        Publishes "trigger_fired" for each trigger whose result is
        should_run=True.
        """

        at = at or self._clock()

        with self._lock:
            triggers = list(self._triggers.values())
            evaluators = dict(self._evaluators)

        evaluations = []

        for trigger in self._ordered(triggers):
            should_run = self._evaluate_trigger(
                trigger, evaluators[trigger.trigger_id], at
            )

            if should_run:
                self._publish(
                    "trigger_fired",
                    trigger.trigger_id,
                    {"job_id": trigger.job_id},
                )

            evaluations.append(
                TriggerEvaluation(
                    trigger_id=trigger.trigger_id,
                    should_run=should_run,
                    evaluated_at=at,
                )
            )

        return tuple(evaluations)

    def next_execution(self) -> "datetime | None":
        """
        Return the soonest next_run across every currently enabled
        trigger that has one set, or None if there isn't one.
        """

        with self._lock:
            pending = [
                trigger.next_run
                for trigger in self._triggers.values()
                if trigger.enabled and trigger.next_run is not None
            ]

        return min(pending) if pending else None

    def reschedule(
        self, trigger_id: str, next_run: datetime
    ) -> TriggerDefinition:
        """
        Set trigger_id's next_run and publish "trigger_rescheduled".

        Raises KeyError if trigger_id is not registered.
        """

        with self._lock:
            trigger = self._triggers.get(trigger_id)

            if trigger is None:
                raise KeyError(
                    f"trigger '{trigger_id}' is not registered"
                )

            updated = replace(trigger, next_run=next_run)
            self._triggers[trigger_id] = updated

        self._publish(
            "trigger_rescheduled",
            trigger_id,
            {"next_run": next_run.isoformat()},
        )

        return updated

    def list(self) -> "tuple[TriggerDefinition, ...]":
        """
        Return every registered trigger, ordered by next_run (triggers
        with no next_run sort last) and then trigger_id, for
        deterministic output.
        """

        with self._lock:
            triggers = list(self._triggers.values())

        return self._ordered(triggers)

    def clear(self) -> None:
        """
        Remove every registered trigger.
        """

        with self._lock:
            self._triggers.clear()
            self._evaluators.clear()

    def _ordered(
        self, triggers: "list[TriggerDefinition]"
    ) -> "tuple[TriggerDefinition, ...]":
        sentinel = datetime.max.replace(tzinfo=timezone.utc)

        return tuple(
            sorted(
                triggers,
                key=lambda trigger: (
                    trigger.next_run or sentinel,
                    trigger.trigger_id,
                ),
            )
        )

    def _evaluate_trigger(
        self,
        trigger: TriggerDefinition,
        evaluator: TriggerEvaluator,
        at: datetime,
    ) -> bool:
        if not trigger.enabled:
            return False

        return evaluator(trigger, at)

    def _built_in_evaluators(self) -> "dict[str, TriggerEvaluator]":
        return {
            "interval": self._evaluate_interval,
            "one_shot": self._evaluate_interval,
            "manual": self._evaluate_manual,
            "immediate": self._evaluate_immediate,
        }

    def _evaluate_interval(
        self, trigger: TriggerDefinition, at: datetime
    ) -> bool:
        return trigger.next_run is not None and at >= trigger.next_run

    def _evaluate_manual(
        self, trigger: TriggerDefinition, at: datetime
    ) -> bool:
        return False

    def _evaluate_immediate(
        self, trigger: TriggerDefinition, at: datetime
    ) -> bool:
        return True

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_trigger_engine() -> GovernanceTriggerEngine:
    """
    Build the process-wide governance trigger engine, wired to the
    process-wide governance event bus and job registry.
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_job_registry import get_job_registry

    return GovernanceTriggerEngine(
        event_bus=get_event_bus(), job_registry=get_job_registry()
    )


# Shared for the lifetime of the process: triggers registered through
# the scheduler need to be visible to whatever queries the engine
# directly, which a persistence runtime built fresh per request cannot
# provide on its own.
_trigger_engine = build_default_governance_trigger_engine()


def get_trigger_engine() -> GovernanceTriggerEngine:
    """
    Return the process-wide governance trigger engine.
    """

    return _trigger_engine
