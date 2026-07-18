from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_delivery_engine import (
        GovernanceIntegrityDeliveryEngine,
    )
    from .deployment_governance_delivery_scheduler import (
        GovernanceIntegrityDeliveryScheduler,
        GovernanceIntegrityScheduledDispatch,
    )
    from .deployment_governance_retry_orchestrator import (
        GovernanceIntegrityRetryOrchestrator,
    )


@dataclass(frozen=True)
class GovernanceIntegrityWorkerRunSummary:
    """
    Aggregate outcome of one delivery worker run over the ready
    dispatch queue.
    """

    started_at: datetime

    finished_at: datetime

    processed: int

    succeeded: int

    failed: int

    retried: int

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None:
            raise ValueError(
                "started_at must be timezone-aware"
            )

        if self.finished_at.tzinfo is None:
            raise ValueError(
                "finished_at must be timezone-aware"
            )

        for name, value in (
            ("processed", self.processed),
            ("succeeded", self.succeeded),
            ("failed", self.failed),
            ("retried", self.retried),
        ):
            if value < 0:
                raise ValueError(
                    f"{name} must be greater than or equal to zero"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "retried": self.retried,
        }


class GovernanceIntegrityDeliveryWorker:
    """
    Continuously consumes the delivery scheduler's ready queue,
    executes each dispatch through the delivery engine, and
    coordinates retries through the retry orchestrator and scheduler.

    Independent of transport: the scheduler, delivery engine, and
    retry orchestrator are all injected, so this worker never opens a
    socket, thread, or process of its own. Running it repeatedly (a
    poll loop, a cron job, a CLI invocation) is the caller's concern.
    """

    def __init__(
        self,
        scheduler: "GovernanceIntegrityDeliveryScheduler",
        delivery_engine: "GovernanceIntegrityDeliveryEngine",
        retry_orchestrator: "GovernanceIntegrityRetryOrchestrator",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._scheduler = scheduler

        self._delivery_engine = delivery_engine

        self._retry_orchestrator = retry_orchestrator

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._last_summary: GovernanceIntegrityWorkerRunSummary | None = (
            None
        )

    def process_dispatch(
        self,
        dispatch: "GovernanceIntegrityScheduledDispatch",
    ) -> str:
        """
        Execute one ready dispatch and return its outcome as one of
        "succeeded", "retried", or "failed".

        Marks the dispatch RUNNING before execution. A successful
        delivery, or a failure that exhausts retries, marks it
        COMPLETED. A retryable failure schedules a retry instead and
        leaves it PENDING. Any unexpected exception raised by the
        delivery engine itself is treated as a non-retryable failure
        rather than propagated, so the caller can keep processing the
        remaining dispatches.
        """

        self._scheduler.mark_running(dispatch.dispatch_id)

        try:
            result = self._delivery_engine.deliver(
                str(dispatch.dispatch_id)
            )

        except Exception:
            self._scheduler.mark_completed(dispatch.dispatch_id)

            return "failed"

        if result.status == "success":
            self._scheduler.mark_completed(dispatch.dispatch_id)

            return "succeeded"

        decision = self._retry_orchestrator.evaluate_delivery_result(
            result, dispatch.attempt
        )

        if decision.should_retry:
            self._scheduler.schedule_retry(
                dispatch.dispatch_id,
                attempt=decision.retry_attempt,
                delay_seconds=decision.delay_seconds,
            )

            return "retried"

        self._scheduler.mark_completed(dispatch.dispatch_id)

        return "failed"

    def process_ready_dispatches(
        self,
    ) -> GovernanceIntegrityWorkerRunSummary:
        """
        Process every dispatch currently ready to run, oldest first,
        and record the resulting run summary.
        """

        started_at = self._clock()

        processed = 0

        succeeded = 0

        failed = 0

        retried = 0

        for dispatch in self._scheduler.ready_dispatches():
            processed += 1

            outcome = self.process_dispatch(dispatch)

            if outcome == "succeeded":
                succeeded += 1

            elif outcome == "retried":
                retried += 1

            else:
                failed += 1

        summary = GovernanceIntegrityWorkerRunSummary(
            started_at=started_at,
            finished_at=self._clock(),
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            retried=retried,
        )

        self._last_summary = summary

        return summary

    def run_once(self) -> GovernanceIntegrityWorkerRunSummary:
        """
        Run a single pass over the ready dispatch queue.
        """

        return self.process_ready_dispatches()

    def summary(self) -> GovernanceIntegrityWorkerRunSummary | None:
        """
        Return the most recently recorded run summary, or None if
        this worker has never run.
        """

        return self._last_summary
