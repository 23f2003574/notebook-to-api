from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Protocol, runtime_checkable
from uuid import UUID


class GovernanceIntegrityDispatchState(
    str,
    Enum,
):
    """
    Where one scheduled dispatch currently sits in the delivery
    scheduling lifecycle.
    """

    PENDING = "pending"

    READY = "ready"

    RUNNING = "running"

    COMPLETED = "completed"

    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GovernanceIntegrityScheduledDispatch:
    """
    One dispatch's position in the delivery scheduler's queue.
    """

    dispatch_id: UUID

    scheduled_at: datetime

    state: GovernanceIntegrityDispatchState

    attempt: int

    def __post_init__(self) -> None:
        if self.scheduled_at.tzinfo is None:
            raise ValueError(
                "scheduled_at must be timezone-aware"
            )

        if self.attempt < 0:
            raise ValueError(
                "attempt must be greater than or equal to zero"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "dispatch_id": str(self.dispatch_id),
            "scheduled_at": self.scheduled_at.isoformat(),
            "state": self.state.value,
            "attempt": self.attempt,
        }


class GovernanceIntegrityDeliveryScheduleAlreadyExistsError(ValueError):
    """
    Raised when a schedule for the same dispatch ID already exists.
    """


@runtime_checkable
class GovernanceIntegrityDeliveryScheduleRepository(Protocol):
    """
    Persistence contract for scheduled governance audit dispatches.
    """

    def save(
        self,
        scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    ) -> GovernanceIntegrityScheduledDispatch:
        """
        Persist one scheduled dispatch. Raises if a schedule for this
        dispatch ID already exists.
        """

    def get(
        self,
        dispatch_id: UUID,
    ) -> GovernanceIntegrityScheduledDispatch | None:
        """
        Return one scheduled dispatch by ID, or None if it does not
        exist.
        """

    def update(
        self,
        scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    ) -> GovernanceIntegrityScheduledDispatch:
        """
        Replace an existing scheduled dispatch's stored state. Raises
        KeyError if it does not exist.
        """

    def delete(
        self,
        dispatch_id: UUID,
    ) -> None:
        """
        Delete one scheduled dispatch by ID. Raises KeyError if it
        does not exist.
        """

    def list_pending(
        self,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        """
        Return every dispatch currently in the PENDING state, ordered
        by scheduled_at.
        """

    def list_ready(
        self,
        now: datetime,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        """
        Return every PENDING dispatch scheduled at or before now,
        ordered by scheduled_at.
        """


class InMemoryGovernanceIntegrityDeliveryScheduleRepository:
    """
    Thread-safe in-memory implementation of governance audit delivery
    schedule storage.
    """

    def __init__(self) -> None:
        self._schedules: dict[
            UUID, GovernanceIntegrityScheduledDispatch
        ] = {}

        self._lock = RLock()

    def save(
        self,
        scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    ) -> GovernanceIntegrityScheduledDispatch:
        with self._lock:
            if scheduled_dispatch.dispatch_id in self._schedules:
                raise GovernanceIntegrityDeliveryScheduleAlreadyExistsError(
                    "a schedule for dispatch "
                    f"'{scheduled_dispatch.dispatch_id}' already exists"
                )

            self._schedules[
                scheduled_dispatch.dispatch_id
            ] = scheduled_dispatch

            return scheduled_dispatch

    def get(
        self,
        dispatch_id: UUID,
    ) -> GovernanceIntegrityScheduledDispatch | None:
        with self._lock:
            return self._schedules.get(dispatch_id)

    def update(
        self,
        scheduled_dispatch: GovernanceIntegrityScheduledDispatch,
    ) -> GovernanceIntegrityScheduledDispatch:
        with self._lock:
            if scheduled_dispatch.dispatch_id not in self._schedules:
                raise KeyError(
                    "no schedule found for dispatch "
                    f"'{scheduled_dispatch.dispatch_id}'"
                )

            self._schedules[
                scheduled_dispatch.dispatch_id
            ] = scheduled_dispatch

            return scheduled_dispatch

    def delete(
        self,
        dispatch_id: UUID,
    ) -> None:
        with self._lock:
            if dispatch_id not in self._schedules:
                raise KeyError(
                    f"no schedule found for dispatch '{dispatch_id}'"
                )

            del self._schedules[dispatch_id]

    def list_pending(
        self,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        scheduled_dispatch
                        for scheduled_dispatch in self._schedules.values()
                        if scheduled_dispatch.state
                        is GovernanceIntegrityDispatchState.PENDING
                    ),
                    key=lambda scheduled_dispatch: (
                        scheduled_dispatch.scheduled_at,
                        str(scheduled_dispatch.dispatch_id),
                    ),
                )
            )

    def list_ready(
        self,
        now: datetime,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        scheduled_dispatch
                        for scheduled_dispatch in self._schedules.values()
                        if scheduled_dispatch.state
                        is GovernanceIntegrityDispatchState.PENDING
                        and scheduled_dispatch.scheduled_at <= now
                    ),
                    key=lambda scheduled_dispatch: (
                        scheduled_dispatch.scheduled_at,
                        str(scheduled_dispatch.dispatch_id),
                    ),
                )
            )


class GovernanceIntegrityDeliveryScheduler:
    """
    Manages immediate, delayed, and retry dispatches through a single
    unified queue of scheduled dispatches.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityDeliveryScheduleRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def schedule(
        self,
        dispatch_id: UUID,
        *,
        scheduled_at: datetime | None = None,
    ) -> GovernanceIntegrityScheduledDispatch:
        """
        Schedule a new dispatch for the first time.

        scheduled_at defaults to now (an immediate schedule). Raises
        GovernanceIntegrityDeliveryScheduleAlreadyExistsError if this
        dispatch ID is already scheduled.
        """

        scheduled_dispatch = GovernanceIntegrityScheduledDispatch(
            dispatch_id=dispatch_id,
            scheduled_at=scheduled_at or self._clock(),
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=0,
        )

        return self._repository.save(scheduled_dispatch)

    def schedule_retry(
        self,
        dispatch_id: UUID,
        *,
        attempt: int,
        delay_seconds: int,
    ) -> GovernanceIntegrityScheduledDispatch:
        """
        Reschedule an existing dispatch as a retry, preserving its
        dispatch ID.

        Raises LookupError if no schedule exists for this dispatch
        ID, and ValueError if it has already completed.
        """

        from datetime import timedelta

        existing = self._repository.get(dispatch_id)

        if existing is None:
            raise LookupError(
                f"no schedule found for dispatch '{dispatch_id}'"
            )

        if existing.state is GovernanceIntegrityDispatchState.COMPLETED:
            raise ValueError(
                f"dispatch '{dispatch_id}' has already completed and "
                "cannot be rescheduled"
            )

        updated = GovernanceIntegrityScheduledDispatch(
            dispatch_id=dispatch_id,
            scheduled_at=(
                self._clock() + timedelta(seconds=delay_seconds)
            ),
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=attempt,
        )

        return self._repository.update(updated)

    def cancel(
        self,
        dispatch_id: UUID,
    ) -> GovernanceIntegrityScheduledDispatch:
        """
        Cancel a scheduled dispatch. Raises LookupError if no
        schedule exists for this dispatch ID.
        """

        existing = self._repository.get(dispatch_id)

        if existing is None:
            raise LookupError(
                f"no schedule found for dispatch '{dispatch_id}'"
            )

        updated = GovernanceIntegrityScheduledDispatch(
            dispatch_id=dispatch_id,
            scheduled_at=existing.scheduled_at,
            state=GovernanceIntegrityDispatchState.CANCELLED,
            attempt=existing.attempt,
        )

        return self._repository.update(updated)

    def mark_running(
        self,
        dispatch_id: UUID,
    ) -> GovernanceIntegrityScheduledDispatch:
        """
        Mark a scheduled dispatch as currently running. Raises
        LookupError if no schedule exists for this dispatch ID.
        """

        return self._transition(
            dispatch_id, GovernanceIntegrityDispatchState.RUNNING
        )

    def mark_completed(
        self,
        dispatch_id: UUID,
    ) -> GovernanceIntegrityScheduledDispatch:
        """
        Mark a scheduled dispatch as completed (terminal). Raises
        LookupError if no schedule exists for this dispatch ID.
        """

        return self._transition(
            dispatch_id, GovernanceIntegrityDispatchState.COMPLETED
        )

    def ready_dispatches(
        self,
        now: datetime | None = None,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        """
        Return every dispatch ready to run at or before now (defaults
        to the current time).
        """

        return self._repository.list_ready(now or self._clock())

    def pending_dispatches(
        self,
    ) -> tuple[GovernanceIntegrityScheduledDispatch, ...]:
        """
        Return every dispatch currently in the PENDING state,
        including ones not yet ready.
        """

        return self._repository.list_pending()

    def get(
        self,
        dispatch_id: UUID,
    ) -> GovernanceIntegrityScheduledDispatch | None:
        return self._repository.get(dispatch_id)

    def _transition(
        self,
        dispatch_id: UUID,
        state: GovernanceIntegrityDispatchState,
    ) -> GovernanceIntegrityScheduledDispatch:
        existing = self._repository.get(dispatch_id)

        if existing is None:
            raise LookupError(
                f"no schedule found for dispatch '{dispatch_id}'"
            )

        updated = GovernanceIntegrityScheduledDispatch(
            dispatch_id=dispatch_id,
            scheduled_at=existing.scheduled_at,
            state=state,
            attempt=existing.attempt,
        )

        return self._repository.update(updated)
