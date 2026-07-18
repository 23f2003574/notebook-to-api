from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.observability.deployment_governance_delivery_scheduler import (
    GovernanceIntegrityDeliveryScheduleAlreadyExistsError,
    GovernanceIntegrityDeliveryScheduler,
    GovernanceIntegrityDispatchState,
    GovernanceIntegrityScheduledDispatch,
    InMemoryGovernanceIntegrityDeliveryScheduleRepository,
)
from backend.observability.sqlite_deployment_governance_delivery_scheduler import (
    SQLiteGovernanceIntegrityDeliveryScheduleRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def _scheduler(clock=lambda: BASE_TIME) -> GovernanceIntegrityDeliveryScheduler:
    return GovernanceIntegrityDeliveryScheduler(
        InMemoryGovernanceIntegrityDeliveryScheduleRepository(), clock=clock
    )


# --- Model ---------------------------------------------------------------


def test_scheduled_dispatch_rejects_naive_scheduled_at() -> None:
    with pytest.raises(ValueError, match="scheduled_at must be timezone-aware"):
        GovernanceIntegrityScheduledDispatch(
            dispatch_id=uuid4(),
            scheduled_at=datetime(2026, 7, 15, 23, 0, 0),
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=0,
        )


def test_scheduled_dispatch_rejects_negative_attempt() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityScheduledDispatch(
            dispatch_id=uuid4(),
            scheduled_at=BASE_TIME,
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=-1,
        )


# --- Immediate schedule ------------------------------------------------


def test_immediate_schedule_appears_in_ready_dispatches() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)

    ready = scheduler.ready_dispatches(BASE_TIME)

    assert [d.dispatch_id for d in ready] == [dispatch_id]


# --- Future schedule -----------------------------------------------------


def test_future_schedule_remains_pending_until_due() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    future = BASE_TIME + timedelta(hours=1)

    scheduler.schedule(dispatch_id, scheduled_at=future)

    assert scheduler.ready_dispatches(BASE_TIME) == ()
    assert [d.dispatch_id for d in scheduler.pending_dispatches()] == [
        dispatch_id
    ]
    assert [
        d.dispatch_id for d in scheduler.ready_dispatches(future)
    ] == [dispatch_id]


# --- Retry schedule ------------------------------------------------------


def test_retry_preserves_dispatch_id_and_increments_attempt() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)

    updated = scheduler.schedule_retry(
        dispatch_id, attempt=1, delay_seconds=30
    )

    assert updated.dispatch_id == dispatch_id
    assert updated.attempt == 1
    assert updated.state is GovernanceIntegrityDispatchState.PENDING
    assert updated.scheduled_at == BASE_TIME + timedelta(seconds=30)


def test_retry_raises_for_unscheduled_dispatch() -> None:
    scheduler = _scheduler()

    with pytest.raises(LookupError):
        scheduler.schedule_retry(uuid4(), attempt=1, delay_seconds=30)


def test_retry_raises_for_completed_dispatch() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)
    scheduler.mark_completed(dispatch_id)

    with pytest.raises(ValueError):
        scheduler.schedule_retry(dispatch_id, attempt=1, delay_seconds=30)


# --- Cancel --------------------------------------------------------------


def test_cancelled_dispatch_never_appears_ready() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)
    scheduler.cancel(dispatch_id)

    assert scheduler.ready_dispatches(BASE_TIME) == ()

    scheduled = scheduler.get(dispatch_id)
    assert scheduled.state is GovernanceIntegrityDispatchState.CANCELLED


def test_cancel_raises_for_unscheduled_dispatch() -> None:
    scheduler = _scheduler()

    with pytest.raises(LookupError):
        scheduler.cancel(uuid4())


# --- Duplicate schedule ------------------------------------------------


def test_duplicate_schedule_rejected() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)

    with pytest.raises(
        GovernanceIntegrityDeliveryScheduleAlreadyExistsError
    ):
        scheduler.schedule(dispatch_id)


def test_duplicate_schedule_is_a_value_error() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)

    with pytest.raises(ValueError):
        scheduler.schedule(dispatch_id)


# --- Running / completed ---------------------------------------------


def test_mark_running_then_completed() -> None:
    scheduler = _scheduler()
    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)
    scheduler.mark_running(dispatch_id)

    assert (
        scheduler.get(dispatch_id).state
        is GovernanceIntegrityDispatchState.RUNNING
    )

    scheduler.mark_completed(dispatch_id)

    assert (
        scheduler.get(dispatch_id).state
        is GovernanceIntegrityDispatchState.COMPLETED
    )


def test_mark_running_raises_for_unscheduled_dispatch() -> None:
    scheduler = _scheduler()

    with pytest.raises(LookupError):
        scheduler.mark_running(uuid4())


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "delivery-scheduler.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityDeliveryScheduleRepository(
        database
    )

    dispatch_id = uuid4()

    repository.save(
        GovernanceIntegrityScheduledDispatch(
            dispatch_id=dispatch_id,
            scheduled_at=BASE_TIME,
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=0,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = (
        SQLiteGovernanceIntegrityDeliveryScheduleRepository(
            reloaded_database
        )
    )

    pending = reloaded_repository.list_pending()

    assert len(pending) == 1
    assert pending[0].dispatch_id == dispatch_id


def test_sqlite_repository_update_and_delete(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "delivery-scheduler-crud.db"
        )
    )

    repository = SQLiteGovernanceIntegrityDeliveryScheduleRepository(
        database
    )

    dispatch_id = uuid4()

    repository.save(
        GovernanceIntegrityScheduledDispatch(
            dispatch_id=dispatch_id,
            scheduled_at=BASE_TIME,
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=0,
        )
    )

    repository.update(
        GovernanceIntegrityScheduledDispatch(
            dispatch_id=dispatch_id,
            scheduled_at=BASE_TIME,
            state=GovernanceIntegrityDispatchState.CANCELLED,
            attempt=0,
        )
    )

    assert (
        repository.get(dispatch_id).state
        is GovernanceIntegrityDispatchState.CANCELLED
    )

    repository.delete(dispatch_id)

    assert repository.get(dispatch_id) is None


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_scheduler() -> None:
    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
    )

    runtime = build_deployment_governance_persistence()

    scheduler = runtime.build_integrity_delivery_scheduler()

    dispatch_id = uuid4()

    scheduler.schedule(dispatch_id)

    assert scheduler.get(dispatch_id) is not None
