from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pipeline.etl_engine import ETLWorkflowEngine, UnknownWorkflowError, get_etl_workflow_engine
from backend.pipeline.pipeline_scheduler import (
    InvalidTriggerError,
    PipelineSchedule,
    PipelineScheduler,
    ScheduleTrigger,
    TriggerType,
    UnknownScheduleError,
    get_pipeline_scheduler,
    router as pipeline_scheduler_router,
)


@pytest.fixture
def workflows() -> ETLWorkflowEngine:
    engine = ETLWorkflowEngine()
    engine.register_workflow("orders-etl", "orders-db", [], "warehouse.orders")
    return engine


@pytest.fixture
def scheduler() -> PipelineScheduler:
    return PipelineScheduler()


@pytest.fixture
def client(scheduler: PipelineScheduler, workflows: ETLWorkflowEngine) -> TestClient:
    app = FastAPI()
    app.include_router(pipeline_scheduler_router)
    app.dependency_overrides[get_pipeline_scheduler] = lambda: scheduler
    app.dependency_overrides[get_etl_workflow_engine] = lambda: workflows
    return TestClient(app)


def test_workflow_exists_reflects_registration(workflows: ETLWorkflowEngine):
    assert workflows.workflow_exists("orders-etl") is True
    assert workflows.workflow_exists("does-not-exist") is False


def test_schedule_interval_creates_active_schedule(scheduler: PipelineScheduler, workflows: ETLWorkflowEngine):
    trigger = ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)

    schedule = scheduler.schedule("orders-etl", trigger, workflows=workflows)

    assert isinstance(schedule, PipelineSchedule)
    assert schedule.status == "active"
    assert schedule.next_run_at is not None


def test_schedule_rejects_unknown_workflow(scheduler: PipelineScheduler, workflows: ETLWorkflowEngine):
    trigger = ScheduleTrigger(trigger_type=TriggerType.MANUAL)

    with pytest.raises(UnknownWorkflowError):
        scheduler.schedule("does-not-exist", trigger, workflows=workflows)


def test_schedule_manual_has_no_next_run(scheduler: PipelineScheduler):
    trigger = ScheduleTrigger(trigger_type=TriggerType.MANUAL)

    schedule = scheduler.schedule("orders-etl", trigger)

    assert schedule.next_run_at is None


def test_schedule_one_time_uses_run_at(scheduler: PipelineScheduler):
    run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    trigger = ScheduleTrigger(trigger_type=TriggerType.ONE_TIME, run_at=run_at)

    schedule = scheduler.schedule("orders-etl", trigger)

    assert schedule.next_run_at == run_at


def test_schedule_one_time_rejects_past_run_at(scheduler: PipelineScheduler):
    run_at = datetime.now(timezone.utc) - timedelta(hours=1)
    trigger = ScheduleTrigger(trigger_type=TriggerType.ONE_TIME, run_at=run_at)

    with pytest.raises(InvalidTriggerError):
        scheduler.schedule("orders-etl", trigger)


def test_schedule_interval_requires_positive_seconds(scheduler: PipelineScheduler):
    trigger = ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=0)

    with pytest.raises(InvalidTriggerError):
        scheduler.schedule("orders-etl", trigger)


def test_schedule_cron_every_n_minutes(scheduler: PipelineScheduler):
    trigger = ScheduleTrigger(trigger_type=TriggerType.CRON, expression="*/15 * * * *")

    schedule = scheduler.schedule("orders-etl", trigger)

    assert schedule.next_run_at.minute % 15 == 0


def test_schedule_cron_daily_at_time(scheduler: PipelineScheduler):
    trigger = ScheduleTrigger(trigger_type=TriggerType.CRON, expression="30 9 * * *")

    schedule = scheduler.schedule("orders-etl", trigger)

    assert schedule.next_run_at.hour == 9
    assert schedule.next_run_at.minute == 30
    assert schedule.next_run_at > datetime.now(timezone.utc)


def test_schedule_cron_rejects_day_field(scheduler: PipelineScheduler):
    trigger = ScheduleTrigger(trigger_type=TriggerType.CRON, expression="0 9 1 * *")

    with pytest.raises(InvalidTriggerError):
        scheduler.schedule("orders-etl", trigger)


def test_reschedule_updates_trigger(scheduler: PipelineScheduler):
    original = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60))

    updated = scheduler.reschedule(
        original.schedule_id, ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=120)
    )

    assert updated.trigger.interval_seconds == 120
    assert updated.schedule_id == original.schedule_id


def test_reschedule_unknown_schedule_raises(scheduler: PipelineScheduler):
    with pytest.raises(UnknownScheduleError):
        scheduler.reschedule("does-not-exist", ScheduleTrigger(trigger_type=TriggerType.MANUAL))


def test_cancel_marks_schedule_cancelled(scheduler: PipelineScheduler):
    schedule = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60))

    cancelled = scheduler.cancel(schedule.schedule_id)

    assert cancelled.status == "cancelled"
    assert cancelled.next_run_at is None


def test_cancel_unknown_schedule_raises(scheduler: PipelineScheduler):
    with pytest.raises(UnknownScheduleError):
        scheduler.cancel("does-not-exist")


def test_upcoming_excludes_cancelled_and_manual(scheduler: PipelineScheduler):
    active = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60))
    manual = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.MANUAL))
    cancel_me = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=120))
    scheduler.cancel(cancel_me.schedule_id)

    upcoming_ids = {schedule.schedule_id for schedule in scheduler.upcoming()}

    assert upcoming_ids == {active.schedule_id}
    assert manual.schedule_id not in upcoming_ids


def test_upcoming_sorted_by_next_run_and_respects_limit(scheduler: PipelineScheduler):
    later = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=3600))
    sooner = scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60))

    upcoming = scheduler.upcoming(limit=1)

    assert len(upcoming) == 1
    assert upcoming[0].schedule_id == sooner.schedule_id


def test_get_unknown_schedule_raises(scheduler: PipelineScheduler):
    with pytest.raises(UnknownScheduleError):
        scheduler.get("does-not-exist")


def test_list_schedules_returns_all(scheduler: PipelineScheduler):
    scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.MANUAL))
    scheduler.schedule("orders-etl", ScheduleTrigger(trigger_type=TriggerType.INTERVAL, interval_seconds=60))

    assert len(scheduler.list_schedules()) == 2


def test_api_create_schedule(client: TestClient):
    response = client.post(
        "/pipelines/schedules",
        json={"workflow_name": "orders-etl", "trigger": {"trigger_type": "interval", "interval_seconds": 60}},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "active"


def test_api_create_schedule_unknown_workflow_returns_404(client: TestClient):
    response = client.post(
        "/pipelines/schedules",
        json={"workflow_name": "does-not-exist", "trigger": {"trigger_type": "manual"}},
    )

    assert response.status_code == 404


def test_api_list_schedules(client: TestClient):
    client.post(
        "/pipelines/schedules",
        json={"workflow_name": "orders-etl", "trigger": {"trigger_type": "interval", "interval_seconds": 60}},
    )

    response = client.get("/pipelines/schedules")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_list_upcoming_excludes_manual(client: TestClient):
    client.post("/pipelines/schedules", json={"workflow_name": "orders-etl", "trigger": {"trigger_type": "manual"}})
    client.post(
        "/pipelines/schedules",
        json={"workflow_name": "orders-etl", "trigger": {"trigger_type": "interval", "interval_seconds": 60}},
    )

    response = client.get("/pipelines/schedules", params={"upcoming": True})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_reschedule_updates_trigger(client: TestClient):
    created = client.post(
        "/pipelines/schedules",
        json={"workflow_name": "orders-etl", "trigger": {"trigger_type": "interval", "interval_seconds": 60}},
    )
    schedule_id = created.json()["schedule_id"]

    response = client.put(
        f"/pipelines/schedules/{schedule_id}",
        json={"trigger": {"trigger_type": "interval", "interval_seconds": 300}},
    )

    assert response.status_code == 200
    assert response.json()["trigger"]["interval_seconds"] == 300


def test_api_reschedule_unknown_returns_404(client: TestClient):
    response = client.put(
        "/pipelines/schedules/does-not-exist",
        json={"trigger": {"trigger_type": "manual"}},
    )

    assert response.status_code == 404


def test_api_cancel_schedule(client: TestClient):
    created = client.post(
        "/pipelines/schedules",
        json={"workflow_name": "orders-etl", "trigger": {"trigger_type": "interval", "interval_seconds": 60}},
    )
    schedule_id = created.json()["schedule_id"]

    response = client.delete(f"/pipelines/schedules/{schedule_id}")

    assert response.status_code == 204
    assert client.get("/pipelines/schedules").json()[0]["status"] == "cancelled"


def test_api_cancel_unknown_returns_404(client: TestClient):
    response = client.delete("/pipelines/schedules/does-not-exist")

    assert response.status_code == 404
