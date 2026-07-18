from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.observability.deployment_governance_delivery_engine import (
    GovernanceIntegrityDeliveryResult,
    GovernanceIntegrityDeliveryStatus,
)
from backend.observability.deployment_governance_delivery_scheduler import (
    GovernanceIntegrityDeliveryScheduler,
    GovernanceIntegrityDispatchState,
    InMemoryGovernanceIntegrityDeliveryScheduleRepository,
)
from backend.observability.deployment_governance_delivery_worker import (
    GovernanceIntegrityDeliveryWorker,
    GovernanceIntegrityWorkerRunSummary,
)
from backend.observability.deployment_governance_retry_orchestrator import (
    GovernanceIntegrityRetryDecision,
    GovernanceIntegrityRetryOrchestrator,
)

BASE_TIME = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _real_scheduler() -> GovernanceIntegrityDeliveryScheduler:
    return GovernanceIntegrityDeliveryScheduler(
        InMemoryGovernanceIntegrityDeliveryScheduleRepository(),
        clock=lambda: BASE_TIME,
    )


def _success_result(dispatch_id: str) -> GovernanceIntegrityDeliveryResult:
    return GovernanceIntegrityDeliveryResult(
        dispatch_id=dispatch_id,
        channel_name="email",
        status=GovernanceIntegrityDeliveryStatus.SUCCESS,
        delivered_at=BASE_TIME,
        error=None,
    )


def _failed_result(dispatch_id: str) -> GovernanceIntegrityDeliveryResult:
    return GovernanceIntegrityDeliveryResult(
        dispatch_id=dispatch_id,
        channel_name="email",
        status=GovernanceIntegrityDeliveryStatus.FAILED,
        delivered_at=BASE_TIME,
        error="boom",
    )


# --- Model -----------------------------------------------------------------


def test_run_summary_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="started_at must be timezone-aware"):
        GovernanceIntegrityWorkerRunSummary(
            started_at=datetime(2026, 7, 18, 12, 0, 0),
            finished_at=BASE_TIME,
            processed=0,
            succeeded=0,
            failed=0,
            retried=0,
        )


def test_run_summary_rejects_negative_counters() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityWorkerRunSummary(
            started_at=BASE_TIME,
            finished_at=BASE_TIME,
            processed=-1,
            succeeded=0,
            failed=0,
            retried=0,
        )


# --- Empty queue -------------------------------------------------------


def test_empty_queue_produces_zeroed_summary() -> None:
    worker = GovernanceIntegrityDeliveryWorker(
        _real_scheduler(),
        Mock(),
        Mock(),
        clock=lambda: BASE_TIME,
    )

    summary = worker.run_once()

    assert summary.processed == 0
    assert summary.succeeded == 0
    assert summary.failed == 0
    assert summary.retried == 0


# --- Successful dispatch -------------------------------------------------


def test_successful_dispatch_marks_completed_and_counts() -> None:
    scheduler = _real_scheduler()
    dispatch_id = uuid4()
    scheduler.schedule(dispatch_id)

    delivery_engine = Mock()
    delivery_engine.deliver.return_value = _success_result(str(dispatch_id))

    worker = GovernanceIntegrityDeliveryWorker(
        scheduler,
        delivery_engine,
        Mock(),
        clock=lambda: BASE_TIME,
    )

    summary = worker.run_once()

    assert summary.processed == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.retried == 0

    assert (
        scheduler.get(dispatch_id).state
        is GovernanceIntegrityDispatchState.COMPLETED
    )


# --- Retryable failure ---------------------------------------------------


def test_retryable_failure_schedules_retry_exactly_once() -> None:
    scheduler = Mock()
    dispatch = Mock(dispatch_id=uuid4(), attempt=0)
    scheduler.ready_dispatches.return_value = (dispatch,)

    delivery_engine = Mock()
    delivery_engine.deliver.return_value = _failed_result(
        str(dispatch.dispatch_id)
    )

    retry_orchestrator = Mock()
    retry_orchestrator.evaluate_delivery_result.return_value = (
        GovernanceIntegrityRetryDecision(
            should_retry=True,
            retry_attempt=1,
            next_retry_at=BASE_TIME,
            delay_seconds=30,
            reason=None,
        )
    )

    worker = GovernanceIntegrityDeliveryWorker(
        scheduler,
        delivery_engine,
        retry_orchestrator,
        clock=lambda: BASE_TIME,
    )

    summary = worker.run_once()

    scheduler.schedule_retry.assert_called_once_with(
        dispatch.dispatch_id, attempt=1, delay_seconds=30
    )

    assert summary.retried == 1
    assert summary.failed == 0
    assert summary.succeeded == 0


# --- Non-retryable failure -------------------------------------------------


def test_non_retryable_failure_completes_without_retry() -> None:
    scheduler = Mock()
    dispatch = Mock(dispatch_id=uuid4(), attempt=2)
    scheduler.ready_dispatches.return_value = (dispatch,)

    delivery_engine = Mock()
    delivery_engine.deliver.return_value = _failed_result(
        str(dispatch.dispatch_id)
    )

    retry_orchestrator = Mock()
    retry_orchestrator.evaluate_delivery_result.return_value = (
        GovernanceIntegrityRetryDecision(
            should_retry=False,
            retry_attempt=2,
            next_retry_at=None,
            delay_seconds=None,
            reason="maximum retry attempts reached",
        )
    )

    worker = GovernanceIntegrityDeliveryWorker(
        scheduler,
        delivery_engine,
        retry_orchestrator,
        clock=lambda: BASE_TIME,
    )

    summary = worker.run_once()

    scheduler.schedule_retry.assert_not_called()
    scheduler.mark_completed.assert_called_once_with(dispatch.dispatch_id)

    assert summary.failed == 1
    assert summary.retried == 0
    assert summary.succeeded == 0


# --- Multiple dispatches -------------------------------------------------


def test_multiple_dispatches_all_processed() -> None:
    scheduler = Mock()
    dispatches = [
        Mock(dispatch_id=uuid4(), attempt=0) for _ in range(3)
    ]
    scheduler.ready_dispatches.return_value = tuple(dispatches)

    delivery_engine = Mock()
    delivery_engine.deliver.side_effect = [
        _success_result(str(dispatches[0].dispatch_id)),
        _failed_result(str(dispatches[1].dispatch_id)),
        _success_result(str(dispatches[2].dispatch_id)),
    ]

    retry_orchestrator = Mock()
    retry_orchestrator.evaluate_delivery_result.return_value = (
        GovernanceIntegrityRetryDecision(
            should_retry=False,
            retry_attempt=0,
            next_retry_at=None,
            delay_seconds=None,
            reason="delivery failure is not retryable",
        )
    )

    worker = GovernanceIntegrityDeliveryWorker(
        scheduler,
        delivery_engine,
        retry_orchestrator,
        clock=lambda: BASE_TIME,
    )

    summary = worker.run_once()

    assert summary.processed == 3
    assert summary.succeeded == 2
    assert summary.failed == 1
    assert delivery_engine.deliver.call_count == 3


# --- Unexpected exception --------------------------------------------------


def test_unexpected_exception_is_captured_and_processing_continues() -> None:
    scheduler = Mock()
    dispatches = [
        Mock(dispatch_id=uuid4(), attempt=0) for _ in range(2)
    ]
    scheduler.ready_dispatches.return_value = tuple(dispatches)

    delivery_engine = Mock()
    delivery_engine.deliver.side_effect = [
        RuntimeError("provider exploded"),
        _success_result(str(dispatches[1].dispatch_id)),
    ]

    worker = GovernanceIntegrityDeliveryWorker(
        scheduler,
        delivery_engine,
        Mock(),
        clock=lambda: BASE_TIME,
    )

    summary = worker.run_once()

    assert summary.processed == 2
    assert summary.failed == 1
    assert summary.succeeded == 1
    assert isinstance(summary, GovernanceIntegrityWorkerRunSummary)


# --- Real evaluate_delivery_result integration --------------------------


def test_real_retry_orchestrator_evaluate_delivery_result() -> None:
    orchestrator = GovernanceIntegrityRetryOrchestrator(
        clock=lambda: BASE_TIME
    )

    decision = orchestrator.evaluate_delivery_result(
        _failed_result(str(uuid4())), 0
    )

    assert decision.should_retry is True
    assert decision.retry_attempt == 1

    exhausted_decision = orchestrator.evaluate_delivery_result(
        _failed_result(str(uuid4())), 3
    )

    assert exhausted_decision.should_retry is False

    success_decision = orchestrator.evaluate_delivery_result(
        _success_result(str(uuid4())), 0
    )

    assert success_decision.should_retry is False


# --- Runtime -----------------------------------------------------------


def test_runtime_builds_working_worker() -> None:
    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
    )

    runtime = build_deployment_governance_persistence()

    worker = runtime.build_integrity_delivery_worker()

    summary = worker.run_once()

    assert summary.processed == 0
    assert worker.summary() is summary
