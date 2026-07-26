from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_scheduler import (
    DeploymentScheduler,
    InvalidScheduleStateError,
    ScheduledDeployment,
    SchedulePolicy,
    UnknownScheduleError,
    router as deployment_scheduler_router,
)
from backend.governance.deployment_workflow import (
    DeploymentWorkflowEngine,
    router as deployment_workflow_router,
)

BASE_TIME = datetime(2026, 7, 26, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def scheduler() -> DeploymentScheduler:
    return DeploymentScheduler()


def test_schedule_creates_pending_deployment(scheduler: DeploymentScheduler):
    deployment = scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)

    assert isinstance(deployment, ScheduledDeployment)
    assert deployment.status == "PENDING"
    assert deployment.pipeline == "svc-a"


def test_schedule_requires_pipeline(scheduler: DeploymentScheduler):
    with pytest.raises(ValueError):
        scheduler.schedule("", BASE_TIME)


def test_schedule_policy_rejects_invalid_window():
    with pytest.raises(ValueError):
        SchedulePolicy(window_start_hour=10, window_end_hour=5)


def test_schedule_policy_rejects_non_positive_recurrence():
    with pytest.raises(ValueError):
        SchedulePolicy(recurrence_seconds=0)


def test_reschedule_updates_run_at_and_priority(scheduler: DeploymentScheduler):
    deployment = scheduler.schedule("svc-a", BASE_TIME, priority=1, timestamp=BASE_TIME)
    later = BASE_TIME + timedelta(hours=1)

    updated = scheduler.reschedule(
        deployment.schedule_id, run_at=later, priority=5, timestamp=later
    )

    assert updated.run_at == later
    assert updated.priority == 5
    assert updated.status == "PENDING"


def test_reschedule_unknown_raises(scheduler: DeploymentScheduler):
    with pytest.raises(UnknownScheduleError):
        scheduler.reschedule("does-not-exist", run_at=BASE_TIME)


def test_reschedule_cancelled_raises(scheduler: DeploymentScheduler):
    deployment = scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)
    scheduler.cancel(deployment.schedule_id, timestamp=BASE_TIME)

    with pytest.raises(InvalidScheduleStateError):
        scheduler.reschedule(deployment.schedule_id, run_at=BASE_TIME)


def test_cancel_marks_cancelled(scheduler: DeploymentScheduler):
    deployment = scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)

    cancelled = scheduler.cancel(deployment.schedule_id, timestamp=BASE_TIME)

    assert cancelled.status == "CANCELLED"
    assert cancelled not in scheduler.pending()


def test_cancel_unknown_raises(scheduler: DeploymentScheduler):
    with pytest.raises(UnknownScheduleError):
        scheduler.cancel("does-not-exist")


def test_cancel_already_cancelled_raises(scheduler: DeploymentScheduler):
    deployment = scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)
    scheduler.cancel(deployment.schedule_id, timestamp=BASE_TIME)

    with pytest.raises(InvalidScheduleStateError):
        scheduler.cancel(deployment.schedule_id)


def test_pending_orders_by_priority_then_run_at(scheduler: DeploymentScheduler):
    low = scheduler.schedule("svc-low", BASE_TIME, priority=0, timestamp=BASE_TIME)
    high = scheduler.schedule("svc-high", BASE_TIME, priority=10, timestamp=BASE_TIME)
    earlier_low = scheduler.schedule(
        "svc-earlier", BASE_TIME - timedelta(minutes=5), priority=0, timestamp=BASE_TIME
    )

    ordered = [d.schedule_id for d in scheduler.pending()]

    assert ordered == [high.schedule_id, earlier_low.schedule_id, low.schedule_id]


def test_pending_excludes_cancelled_and_completed(scheduler: DeploymentScheduler):
    deployment = scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)
    scheduler.cancel(deployment.schedule_id, timestamp=BASE_TIME)

    assert scheduler.pending() == ()


def test_get_unknown_raises(scheduler: DeploymentScheduler):
    with pytest.raises(UnknownScheduleError):
        scheduler.get("does-not-exist")


def test_dispatch_due_starts_workflow_for_due_schedule():
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register("svc-a", [PipelineStage(name="build", action="build")])
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    scheduler = DeploymentScheduler(workflow_engine=wf_engine)
    scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)

    dispatched = scheduler.dispatch_due(now=BASE_TIME, workflow_engine=wf_engine)

    assert len(dispatched) == 1
    assert dispatched[0].status == "COMPLETED"
    assert len(wf_engine.executions_for("svc-a")) == 1


def test_dispatch_due_skips_future_schedules():
    scheduler = DeploymentScheduler()
    scheduler.schedule("svc-a", BASE_TIME + timedelta(hours=1), timestamp=BASE_TIME)

    dispatched = scheduler.dispatch_due(now=BASE_TIME)

    assert dispatched == ()


def test_dispatch_due_respects_execution_window():
    scheduler = DeploymentScheduler()
    scheduler.schedule(
        "svc-a",
        BASE_TIME,
        policy=SchedulePolicy(window_start_hour=13, window_end_hour=17),
        timestamp=BASE_TIME,
    )

    dispatched = scheduler.dispatch_due(now=BASE_TIME)

    assert dispatched == ()


def test_dispatch_due_reschedules_recurring_deployment():
    scheduler = DeploymentScheduler()
    scheduler.schedule(
        "svc-a",
        BASE_TIME,
        policy=SchedulePolicy(recurrence_seconds=3600),
        timestamp=BASE_TIME,
    )

    dispatched = scheduler.dispatch_due(now=BASE_TIME)

    assert dispatched[0].status == "PENDING"
    assert dispatched[0].run_at == BASE_TIME + timedelta(seconds=3600)
    assert dispatched[0].runs_completed == 1


def test_dispatch_due_completes_recurring_deployment_after_max_runs():
    scheduler = DeploymentScheduler()
    deployment = scheduler.schedule(
        "svc-a",
        BASE_TIME,
        policy=SchedulePolicy(recurrence_seconds=3600, max_runs=1),
        timestamp=BASE_TIME,
    )

    dispatched = scheduler.dispatch_due(now=BASE_TIME)

    assert dispatched[0].status == "COMPLETED"
    assert dispatched[0].runs_completed == 1


def test_dispatch_due_skips_pipeline_with_active_execution():
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register("svc-a", [PipelineStage(name="build", action="build")])
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    wf_engine.start("svc-a", timestamp=BASE_TIME)
    scheduler = DeploymentScheduler(workflow_engine=wf_engine)
    scheduler.schedule("svc-a", BASE_TIME, timestamp=BASE_TIME)

    dispatched = scheduler.dispatch_due(now=BASE_TIME)

    assert dispatched == ()
    assert len(scheduler.pending()) == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_workflow_router)
    app.include_router(deployment_scheduler_router)
    return TestClient(app)


def test_api_create_and_list_schedule(client: TestClient):
    create_response = client.post(
        "/governance/schedules",
        json={"pipeline": "svc-api-1", "run_at": BASE_TIME.isoformat()},
    )
    list_response = client.get("/governance/schedules")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert any(s["pipeline"] == "svc-api-1" for s in list_response.json())


def test_api_create_requires_pipeline_and_run_at(client: TestClient):
    response = client.post("/governance/schedules", json={})

    assert response.status_code == 422


def test_api_patch_reschedules(client: TestClient):
    create_response = client.post(
        "/governance/schedules",
        json={"pipeline": "svc-api-2", "run_at": BASE_TIME.isoformat()},
    )
    schedule_id = create_response.json()["schedule_id"]
    later = (BASE_TIME + timedelta(hours=2)).isoformat()

    response = client.patch(f"/governance/schedules/{schedule_id}", json={"run_at": later})

    assert response.status_code == 200
    assert response.json()["run_at"] == later


def test_api_patch_unknown_returns_404(client: TestClient):
    response = client.patch(
        "/governance/schedules/does-not-exist", json={"priority": 1}
    )

    assert response.status_code == 404


def test_api_delete_cancels_schedule(client: TestClient):
    create_response = client.post(
        "/governance/schedules",
        json={"pipeline": "svc-api-3", "run_at": BASE_TIME.isoformat()},
    )
    schedule_id = create_response.json()["schedule_id"]

    delete_response = client.delete(f"/governance/schedules/{schedule_id}")
    list_response = client.get("/governance/schedules")

    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "CANCELLED"
    assert all(s["schedule_id"] != schedule_id for s in list_response.json())


def test_api_delete_unknown_returns_404(client: TestClient):
    response = client.delete("/governance/schedules/does-not-exist")

    assert response.status_code == 404


def test_api_delete_already_cancelled_returns_409(client: TestClient):
    create_response = client.post(
        "/governance/schedules",
        json={"pipeline": "svc-api-4", "run_at": BASE_TIME.isoformat()},
    )
    schedule_id = create_response.json()["schedule_id"]
    client.delete(f"/governance/schedules/{schedule_id}")

    response = client.delete(f"/governance/schedules/{schedule_id}")

    assert response.status_code == 409
