from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_blue_green import (
    BlueGreenDeploymentEngine,
)
from backend.observability.deployment_governance_canary import (
    CanaryDeploymentEngine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_progressive_delivery import (  # noqa: E501
    ProgressiveDeliveryEngine,
    ProgressiveDeployment,
    ProgressiveStage,
    get_progressive_delivery_engine,
)
from backend.observability.deployment_governance_rolling import (
    RollingDeploymentEngine,
)
from backend.observability.deployment_governance_scheduler import (
    GovernanceScheduler,
    get_scheduler,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

VALID_CHECKSUM = "a" * 64

SIMPLE_PIPELINE = [
    ("stage-1", "HEALTH_VALIDATION", False),
    ("stage-2", "HEALTH_VALIDATION", False),
]

GATED_PIPELINE = [
    ("stage-1", "HEALTH_VALIDATION", False),
    ("approval", "MANUAL_APPROVAL", True),
    ("stage-3", "HEALTH_VALIDATION", False),
]


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> ProgressiveDeliveryEngine:
    return ProgressiveDeliveryEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The progressive delivery engine and scheduler are both
    process-wide singletons; most tests below construct their own
    fresh engine instead (see _engine), and only the singleton and
    API tests touch the shared instances, matching
    test_deployment_canary.py's own fixture.
    """

    def _reset():
        get_progressive_delivery_engine().clear()

        scheduler = get_scheduler()

        for job in scheduler.jobs():
            if job.name.startswith("progressive-stage-"):
                scheduler.unregister(job.job_id)

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestProgressiveStage:

    def test_rejects_empty_stage_id(self):
        with pytest.raises(ValueError, match="stage_id must not be empty"):
            ProgressiveStage(
                stage_id="", name="stage-1", strategy="CANARY",
                approval_required=False, completed=False,
            )

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            ProgressiveStage(
                stage_id="s-1", name="", strategy="CANARY",
                approval_required=False, completed=False,
            )

    def test_rejects_unknown_strategy(self):
        with pytest.raises(ValueError, match="strategy must be one of"):
            ProgressiveStage(
                stage_id="s-1", name="stage-1", strategy="BOGUS",
                approval_required=False, completed=False,
            )

    def test_to_dict(self):
        stage = ProgressiveStage(
            stage_id="s-1", name="stage-1", strategy="CANARY",
            approval_required=True, completed=False,
        )

        assert stage.to_dict() == {
            "stage_id": "s-1",
            "name": "stage-1",
            "strategy": "CANARY",
            "approval_required": True,
            "completed": False,
        }


class TestProgressiveDeployment:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            ProgressiveDeployment(
                deployment_id="", current_stage=0, total_stages=3,
                state="RUNNING", started_at=BASE_TIME,
            )

    def test_rejects_non_positive_total_stages(self):
        with pytest.raises(
            ValueError, match="total_stages must be greater than 0"
        ):
            ProgressiveDeployment(
                deployment_id="dep-1", current_stage=0, total_stages=0,
                state="RUNNING", started_at=BASE_TIME,
            )

    def test_rejects_current_stage_over_total(self):
        with pytest.raises(
            ValueError, match="current_stage must be between"
        ):
            ProgressiveDeployment(
                deployment_id="dep-1", current_stage=4, total_stages=3,
                state="RUNNING", started_at=BASE_TIME,
            )

    def test_rejects_unknown_state(self):
        with pytest.raises(ValueError, match="state must be one of"):
            ProgressiveDeployment(
                deployment_id="dep-1", current_stage=0, total_stages=3,
                state="BOGUS", started_at=BASE_TIME,
            )

    def test_rejects_naive_started_at(self):
        with pytest.raises(
            ValueError, match="started_at must be timezone-aware"
        ):
            ProgressiveDeployment(
                deployment_id="dep-1", current_stage=0, total_stages=3,
                state="RUNNING",
                started_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_to_dict(self):
        deployment = ProgressiveDeployment(
            deployment_id="dep-1", current_stage=1, total_stages=3,
            state="RUNNING", started_at=BASE_TIME,
        )

        assert deployment.to_dict() == {
            "deployment_id": "dep-1",
            "current_stage": 1,
            "total_stages": 3,
            "state": "RUNNING",
            "started_at": BASE_TIME.isoformat(),
        }


# --- Deployment initialization -------------------------------------------


class TestDeploy:

    def test_deploy_creates_a_record_at_stage_zero(self):
        engine = _engine()

        deployment = engine.deploy("dep-1", SIMPLE_PIPELINE)

        assert deployment.current_stage == 0
        assert deployment.total_stages == 2
        assert deployment.state == "RUNNING"

    def test_deploy_rejects_empty_pipeline(self):
        engine = _engine()

        with pytest.raises(ValueError, match="stages must not be empty"):
            engine.deploy("dep-1", [])

    def test_deploy_rejects_unknown_strategy(self):
        engine = _engine()

        with pytest.raises(ValueError, match="strategy must be one of"):
            engine.deploy("dep-1", [("stage-1", "BOGUS", False)])

    def test_deploy_rejects_duplicate_active_deployment(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        with pytest.raises(
            ValueError, match="already has an active progressive"
        ):
            engine.deploy("dep-1", SIMPLE_PIPELINE)

    def test_deploy_allows_reuse_after_rollback(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.rollback("dep-1")

        deployment = engine.deploy("dep-1", SIMPLE_PIPELINE)

        assert deployment.state == "RUNNING"

    def test_pipeline_reflects_configured_stages(self):
        engine = _engine()
        engine.deploy("dep-1", GATED_PIPELINE)

        stages = engine.pipeline("dep-1")

        assert [stage.name for stage in stages] == [
            "stage-1", "approval", "stage-3",
        ]
        assert stages[1].approval_required is True

    def test_deploy_publishes_progressive_started_and_stage_started(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        started_events = []
        stage_events = []
        bus.subscribe("progressive_started", started_events.append)
        bus.subscribe("stage_started", stage_events.append)

        engine.deploy("dep-1", SIMPLE_PIPELINE)

        assert len(started_events) == 1
        assert len(stage_events) == 1
        assert stage_events[0].payload["stage"]["name"] == "stage-1"

    def test_deploy_registers_a_scheduler_job(self):
        scheduler = GovernanceScheduler(clock=_clock)
        engine = _engine(scheduler=scheduler)

        engine.deploy("dep-1", SIMPLE_PIPELINE)

        names = {job.name for job in scheduler.jobs()}

        assert "progressive-stage-dep-1" in names


# --- Sequential stage execution -------------------------------------------


class TestAdvance:

    def test_advance_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.advance("dep-1")

    def test_advance_moves_to_the_next_stage(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        updated = engine.advance("dep-1")

        assert updated.current_stage == 1
        assert updated.state == "RUNNING"

    def test_advance_through_every_stage_completes(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        engine.advance("dep-1")
        final = engine.advance("dep-1")

        assert final.state == "COMPLETED"
        assert final.current_stage == 2

    def test_advance_records_completed_stages_in_history(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        engine.advance("dep-1")

        history = engine.history("dep-1")

        assert len(history) == 1
        assert history[0].name == "stage-1"
        assert history[0].completed is True

    def test_advance_on_completed_deployment_raises(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.advance("dep-1")
        engine.advance("dep-1")

        with pytest.raises(ValueError, match="is not active"):
            engine.advance("dep-1")

    def test_advance_blocked_while_paused(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.pause("dep-1")

        with pytest.raises(ValueError, match="is paused"):
            engine.advance("dep-1")

    def test_advance_publishes_stage_completed_and_stage_started(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        completed_events = []
        started_events = []
        bus.subscribe("stage_completed", completed_events.append)
        bus.subscribe("stage_started", started_events.append)

        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.advance("dep-1")

        assert len(completed_events) == 1
        # One stage_started from deploy() (stage-1), one from this
        # advance() entering stage-2.
        assert len(started_events) == 2

    def test_final_advance_publishes_progressive_completed(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        events = []
        bus.subscribe("progressive_completed", events.append)

        engine.advance("dep-1")
        engine.advance("dep-1")

        assert len(events) == 1

    def test_completion_unregisters_the_scheduler_job(self):
        scheduler = GovernanceScheduler(clock=_clock)
        engine = _engine(scheduler=scheduler)
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        engine.advance("dep-1")
        engine.advance("dep-1")

        names = {job.name for job in scheduler.jobs()}

        assert "progressive-stage-dep-1" not in names


# --- Approval gate handling ------------------------------------------------


class TestApprovalGate:

    def test_advance_into_a_gated_stage_blocks(self):
        engine = _engine()
        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")  # completes stage-1

        with pytest.raises(ValueError, match="requires approval"):
            engine.advance("dep-1")

        assert engine.status("dep-1").state == "AWAITING_APPROVAL"

    def test_advance_publishes_approval_requested_only_once(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")

        events = []
        bus.subscribe("approval_requested", events.append)

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        assert len(events) == 1

    def test_approve_unblocks_advance(self):
        engine = _engine()
        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        engine.approve("dep-1")

        updated = engine.advance("dep-1")

        assert updated.current_stage == 2

    def test_approve_without_pending_approval_raises(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        with pytest.raises(ValueError, match="no approval currently pending"):
            engine.approve("dep-1")

    def test_approve_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.approve("dep-1")

    def test_approve_publishes_approval_granted(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        events = []
        bus.subscribe("approval_granted", events.append)

        engine.approve("dep-1")

        assert len(events) == 1


# --- Stage rejection ------------------------------------------------------


class TestReject:

    def test_reject_rolls_back(self):
        engine = _engine()
        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        rejected = engine.reject("dep-1")

        assert rejected.state == "ROLLED_BACK"

    def test_reject_without_pending_approval_raises(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        with pytest.raises(ValueError, match="no approval currently pending"):
            engine.reject("dep-1")

    def test_reject_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.reject("dep-1")

    def test_reject_publishes_approval_rejected_and_progressive_rolled_back(
        self,
    ):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        rejected_events = []
        rolled_back_events = []
        bus.subscribe("approval_rejected", rejected_events.append)
        bus.subscribe("progressive_rolled_back", rolled_back_events.append)

        engine.reject("dep-1")

        assert len(rejected_events) == 1
        assert len(rolled_back_events) == 1

    def test_reject_frees_deployment_id_for_reuse(self):
        engine = _engine()
        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        engine.reject("dep-1")

        deployment = engine.deploy("dep-1", SIMPLE_PIPELINE)

        assert deployment.state == "RUNNING"


# --- Automatic rollback ---------------------------------------------------


class TestAutomaticRollback:

    def test_failed_check_rolls_back_automatically(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        result = engine.advance("dep-1", check=lambda: False)

        assert result.state == "ROLLED_BACK"

    def test_failed_check_publishes_progressive_failed_and_rolled_back(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        failed_events = []
        rolled_back_events = []
        bus.subscribe("progressive_failed", failed_events.append)
        bus.subscribe("progressive_rolled_back", rolled_back_events.append)

        engine.advance("dep-1", check=lambda: False)

        assert len(failed_events) == 1
        assert len(rolled_back_events) == 1

    def test_rollback_frees_deployment_id_for_reuse(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.advance("dep-1", check=lambda: False)

        deployment = engine.deploy("dep-1", SIMPLE_PIPELINE)

        assert deployment.state == "RUNNING"

    def test_manual_rollback_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.rollback("dep-1")

    def test_manual_rollback_on_inactive_deployment_raises(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.rollback("dep-1")

        with pytest.raises(ValueError, match="is not active"):
            engine.rollback("dep-1")


# --- Pipeline composition ---------------------------------------------


class TestPipelineComposition:

    def test_canary_stage_delegates_a_single_promote(self):
        canary = CanaryDeploymentEngine(clock=_clock)
        canary.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        canary.evaluate("dep-1")

        engine = _engine(canary_engine=canary)
        engine.deploy(
            "dep-1", [("canary-stage", "CANARY", False), ("done", "HEALTH_VALIDATION", False)],
        )

        engine.advance("dep-1")

        assert canary.status("dep-1").stage == 1

    def test_rolling_stage_delegates_a_single_next_batch(self):
        rolling = RollingDeploymentEngine(clock=_clock)
        rolling.deploy("dep-1", "1.1.0", 10, batch_size=3)

        engine = _engine(rolling_engine=rolling)
        engine.deploy(
            "dep-1", [("rolling-stage", "ROLLING", False), ("done", "HEALTH_VALIDATION", False)],
        )

        engine.advance("dep-1")

        assert rolling.status("dep-1").updated_instances == 3

    def test_blue_green_stage_delegates_a_single_switch(self):
        blue_green = BlueGreenDeploymentEngine(clock=_clock)
        blue_green.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        blue_green.validate("dep-1")

        engine = _engine(blue_green_engine=blue_green)
        engine.deploy(
            "dep-1", [("bg-stage", "BLUE_GREEN", False), ("done", "HEALTH_VALIDATION", False)],
        )

        engine.advance("dep-1")

        assert blue_green.status("dep-1").active_environment == "GREEN"

    def test_stage_with_no_matching_engine_wired_does_not_fail(self):
        engine = _engine()
        engine.deploy("dep-1", [("canary-stage", "CANARY", False)])

        updated = engine.advance("dep-1")

        assert updated.state == "COMPLETED"

    def test_manual_approval_and_health_validation_stages_compose(self):
        engine = _engine()
        engine.deploy("dep-1", GATED_PIPELINE)

        engine.advance("dep-1")  # completes stage-1, enters approval stage

        with pytest.raises(ValueError):
            engine.advance("dep-1")  # blocked on approval stage

        engine.approve("dep-1")
        engine.advance("dep-1")  # completes approval stage, enters stage-3
        final = engine.advance("dep-1")  # completes stage-3

        assert final.state == "COMPLETED"


# --- Status / history / list -----------------------------------------------


class TestStatus:

    def test_status_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.status("dep-1")


class TestHistory:

    def test_history_empty_before_any_stage_completes(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        assert engine.history("dep-1") == ()

    def test_history_empty_for_unknown_deployment(self):
        engine = _engine()

        assert engine.history("dep-1") == ()


class TestList:

    def test_list_orders_by_deployment_id(self):
        engine = _engine()
        engine.deploy("dep-b", SIMPLE_PIPELINE)
        engine.deploy("dep-a", SIMPLE_PIPELINE)

        listed = engine.list()

        assert [d.deployment_id for d in listed] == ["dep-a", "dep-b"]

    def test_list_empty_when_nothing_deployed(self):
        engine = _engine()

        assert engine.list() == ()


# --- Pause / resume ------------------------------------------------------


class TestPauseResume:

    def test_pause_is_idempotent(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        engine.pause("dep-1")
        record = engine.pause("dep-1")

        assert record.deployment_id == "dep-1"

    def test_resume_allows_advance_again(self):
        engine = _engine()
        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.pause("dep-1")
        engine.resume("dep-1")

        updated = engine.advance("dep-1")

        assert updated.current_stage == 1

    def test_pause_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.pause("dep-1")


# --- Event publication -----------------------------------------------------


class TestEventPublication:

    def test_no_event_bus_is_safe(self):
        engine = _engine(event_bus=None)

        engine.deploy("dep-1", GATED_PIPELINE)
        engine.advance("dep-1")

        with pytest.raises(ValueError):
            engine.advance("dep-1")

        engine.approve("dep-1")
        engine.advance("dep-1")
        engine.pause("dep-1")
        engine.resume("dep-1")


# --- Scheduler integration ---------------------------------------------


class TestSchedulerIntegration:

    def test_no_scheduler_wired_is_safe(self):
        engine = _engine(scheduler=None)

        engine.deploy("dep-1", SIMPLE_PIPELINE)
        engine.advance("dep-1")
        engine.advance("dep-1")

    def test_scheduler_job_removed_on_rollback(self):
        scheduler = GovernanceScheduler(clock=_clock)
        engine = _engine(scheduler=scheduler)
        engine.deploy("dep-1", SIMPLE_PIPELINE)

        engine.rollback("dep-1")

        names = {job.name for job in scheduler.jobs()}

        assert "progressive-stage-dep-1" not in names


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_progressive_delivery_engine_returns_same_instance(self):
        assert (
            get_progressive_delivery_engine()
            is get_progressive_delivery_engine()
        )

    def test_singleton_is_wired_to_singleton_scheduler(self):
        get_progressive_delivery_engine().deploy(
            "dep-singleton-wiring", SIMPLE_PIPELINE
        )

        names = {job.name for job in get_scheduler().jobs()}

        assert "progressive-stage-dep-singleton-wiring" in names


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


def _deploy_via_api(client: TestClient, deployment_id: str, pipeline):
    return client.post(
        f"/governance/progressive/{deployment_id}/deploy",
        params={
            "stage_names": [name for name, _, _ in pipeline],
            "stage_strategies": [strategy for _, strategy, _ in pipeline],
            "stage_approval_required": [
                required for _, _, required in pipeline
            ],
        },
    )


class TestGovernanceProgressiveApi:

    def test_post_deploy(self, client):
        response = _deploy_via_api(client, "dep-api-1", SIMPLE_PIPELINE)

        assert response.status_code == 200

        payload = response.json()

        assert payload["total_stages"] == 2
        assert payload["current_stage"] == 0

    def test_post_deploy_unknown_strategy_returns_409(self, client):
        response = _deploy_via_api(
            client, "dep-api-2", [("stage-1", "BOGUS", False)]
        )

        assert response.status_code == 409

    def test_get_deployment(self, client):
        _deploy_via_api(client, "dep-api-3", SIMPLE_PIPELINE)

        response = client.get("/governance/progressive/dep-api-3")

        assert response.status_code == 200
        assert response.json()["current_stage"] == 0

    def test_get_unknown_deployment_returns_404(self, client):
        response = client.get("/governance/progressive/does-not-exist")

        assert response.status_code == 404

    def test_list_deployments(self, client):
        _deploy_via_api(client, "dep-api-4", SIMPLE_PIPELINE)

        response = client.get("/governance/progressive")

        assert response.status_code == 200
        assert any(
            d["deployment_id"] == "dep-api-4" for d in response.json()
        )

    def test_advance(self, client):
        _deploy_via_api(client, "dep-api-5", SIMPLE_PIPELINE)

        response = client.post(
            "/governance/progressive/dep-api-5/advance"
        )

        assert response.status_code == 200
        assert response.json()["current_stage"] == 1

    def test_advance_into_gated_stage_returns_409(self, client):
        _deploy_via_api(client, "dep-api-6", GATED_PIPELINE)
        client.post("/governance/progressive/dep-api-6/advance")

        response = client.post(
            "/governance/progressive/dep-api-6/advance"
        )

        assert response.status_code == 409

    def test_approve_then_advance_succeeds(self, client):
        _deploy_via_api(client, "dep-api-7", GATED_PIPELINE)
        client.post("/governance/progressive/dep-api-7/advance")
        client.post("/governance/progressive/dep-api-7/advance")

        approve_response = client.post(
            "/governance/progressive/dep-api-7/approve"
        )
        assert approve_response.status_code == 200

        response = client.post(
            "/governance/progressive/dep-api-7/advance"
        )

        assert response.status_code == 200
        assert response.json()["current_stage"] == 2

    def test_approve_without_pending_returns_409(self, client):
        _deploy_via_api(client, "dep-api-8", SIMPLE_PIPELINE)

        response = client.post(
            "/governance/progressive/dep-api-8/approve"
        )

        assert response.status_code == 409

    def test_reject_rolls_back(self, client):
        _deploy_via_api(client, "dep-api-9", GATED_PIPELINE)
        client.post("/governance/progressive/dep-api-9/advance")
        client.post("/governance/progressive/dep-api-9/advance")

        response = client.post(
            "/governance/progressive/dep-api-9/reject"
        )

        assert response.status_code == 200
        assert response.json()["state"] == "ROLLED_BACK"

    def test_rollback(self, client):
        _deploy_via_api(client, "dep-api-10", SIMPLE_PIPELINE)

        response = client.post(
            "/governance/progressive/dep-api-10/rollback"
        )

        assert response.status_code == 200
        assert response.json()["state"] == "ROLLED_BACK"

    def test_rollback_unknown_deployment_returns_404(self, client):
        response = client.post(
            "/governance/progressive/does-not-exist/rollback"
        )

        assert response.status_code == 404

    def test_deploy_mismatched_stage_list_lengths_returns_422(self, client):
        response = client.post(
            "/governance/progressive/dep-api-11/deploy",
            params={
                "stage_names": ["stage-1", "stage-2"],
                "stage_strategies": ["HEALTH_VALIDATION"],
                "stage_approval_required": [False, False],
            },
        )

        assert response.status_code == 422
