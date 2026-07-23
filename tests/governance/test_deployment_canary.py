from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_canary import (
    DEFAULT_STAGES,
    CanaryDeployment,
    CanaryDeploymentEngine,
    CanaryEvaluation,
    get_canary_engine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_scheduler import (
    GovernanceScheduler,
    get_scheduler,
)
from backend.observability.deployment_governance_scheduler_metrics import (
    GovernanceSchedulerMetrics,
)
from backend.observability.deployment_governance_version_registry import (
    DeploymentVersionRegistry,
    get_version_registry,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

VALID_CHECKSUM = "a" * 64


def _clock():
    return BASE_TIME


def _engine(**kwargs) -> CanaryDeploymentEngine:
    return CanaryDeploymentEngine(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The canary engine, scheduler, and version registry are all
    process-wide singletons; most tests below construct their own
    fresh engine instead (see _engine), and only the singleton and API
    tests touch the shared instances, matching
    test_deployment_blue_green.py's own fixture.
    """

    def _reset():
        get_canary_engine().clear()
        get_version_registry().clear()

        scheduler = get_scheduler()

        for job in scheduler.jobs():
            if job.name.startswith("canary-evaluation-"):
                scheduler.unregister(job.job_id)

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestCanaryDeployment:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            CanaryDeployment(
                deployment_id="", stable_version="1.0.0",
                canary_version="1.1.0", traffic_percentage=0, stage=0,
                created_at=BASE_TIME,
            )

    def test_rejects_invalid_stable_version(self):
        with pytest.raises(ValueError, match="stable_version"):
            CanaryDeployment(
                deployment_id="dep-1", stable_version="bogus",
                canary_version="1.1.0", traffic_percentage=0, stage=0,
                created_at=BASE_TIME,
            )

    def test_rejects_invalid_canary_version(self):
        with pytest.raises(ValueError, match="canary_version"):
            CanaryDeployment(
                deployment_id="dep-1", stable_version="1.0.0",
                canary_version="bogus", traffic_percentage=0, stage=0,
                created_at=BASE_TIME,
            )

    def test_rejects_traffic_percentage_out_of_range(self):
        with pytest.raises(
            ValueError, match="traffic_percentage must be between"
        ):
            CanaryDeployment(
                deployment_id="dep-1", stable_version="1.0.0",
                canary_version="1.1.0", traffic_percentage=101, stage=0,
                created_at=BASE_TIME,
            )

    def test_rejects_negative_stage(self):
        with pytest.raises(ValueError, match="stage must be >= 0"):
            CanaryDeployment(
                deployment_id="dep-1", stable_version="1.0.0",
                canary_version="1.1.0", traffic_percentage=0, stage=-1,
                created_at=BASE_TIME,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            CanaryDeployment(
                deployment_id="dep-1", stable_version="1.0.0",
                canary_version="1.1.0", traffic_percentage=0, stage=0,
                created_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_to_dict(self):
        deployment = CanaryDeployment(
            deployment_id="dep-1", stable_version="1.0.0",
            canary_version="1.1.0", traffic_percentage=5, stage=1,
            created_at=BASE_TIME,
        )

        assert deployment.to_dict() == {
            "deployment_id": "dep-1",
            "stable_version": "1.0.0",
            "canary_version": "1.1.0",
            "traffic_percentage": 5,
            "stage": 1,
            "created_at": BASE_TIME.isoformat(),
        }


class TestCanaryEvaluation:

    def test_rejects_traffic_percentage_out_of_range(self):
        with pytest.raises(
            ValueError, match="traffic_percentage must be between"
        ):
            CanaryEvaluation(
                deployment_id="dep-1", healthy=True,
                traffic_percentage=-1, evaluated_at=BASE_TIME,
            )

    def test_rejects_naive_evaluated_at(self):
        with pytest.raises(
            ValueError, match="evaluated_at must be timezone-aware"
        ):
            CanaryEvaluation(
                deployment_id="dep-1", healthy=True,
                traffic_percentage=5,
                evaluated_at=datetime(2026, 7, 23, 12, 0, 0),
            )


# --- Canary initialization -----------------------------------------------


class TestDeploy:

    def test_deploy_creates_a_record_at_first_stage(self):
        engine = _engine()

        deployment = engine.deploy(
            "dep-1", "1.1.0", stable_version="1.0.0"
        )

        assert deployment.stable_version == "1.0.0"
        assert deployment.canary_version == "1.1.0"
        assert deployment.stage == 0
        assert deployment.traffic_percentage == DEFAULT_STAGES[0]

    def test_deploy_resolves_stable_version_from_registry(self):
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        engine = _engine(version_registry=registry)

        deployment = engine.deploy("dep-1", "1.1.0")

        assert deployment.stable_version == "1.0.0"

    def test_deploy_without_stable_version_or_registry_raises(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="stable_version must be provided"
        ):
            engine.deploy("dep-1", "1.1.0")

    def test_deploy_rejects_invalid_canary_version(self):
        engine = _engine()

        with pytest.raises(ValueError, match="canary_version"):
            engine.deploy("dep-1", "bogus", stable_version="1.0.0")

    def test_deploy_rejects_duplicate_active_canary(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        with pytest.raises(ValueError, match="already has an active canary"):
            engine.deploy("dep-1", "1.2.0", stable_version="1.0.0")

    def test_deploy_allows_reuse_after_rollback(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.rollback("dep-1")

        deployment = engine.deploy(
            "dep-1", "1.2.0", stable_version="1.0.0"
        )

        assert deployment.canary_version == "1.2.0"

    def test_deploy_accepts_custom_stages(self):
        engine = _engine()

        deployment = engine.deploy(
            "dep-1", "1.1.0", stable_version="1.0.0",
            stages=(0, 50, 100),
        )

        assert deployment.traffic_percentage == 0

        engine.evaluate("dep-1")
        promoted = engine.promote("dep-1")

        assert promoted.traffic_percentage == 50

    def test_deploy_rejects_stages_not_starting_at_zero(self):
        engine = _engine()

        with pytest.raises(ValueError, match="stages must start at 0"):
            engine.deploy(
                "dep-1", "1.1.0", stable_version="1.0.0",
                stages=(10, 100),
            )

    def test_deploy_rejects_stages_not_ending_at_100(self):
        engine = _engine()

        with pytest.raises(ValueError, match="stages must end at 100"):
            engine.deploy(
                "dep-1", "1.1.0", stable_version="1.0.0",
                stages=(0, 50),
            )

    def test_deploy_rejects_non_increasing_stages(self):
        engine = _engine()

        with pytest.raises(
            ValueError, match="stages must be strictly increasing"
        ):
            engine.deploy(
                "dep-1", "1.1.0", stable_version="1.0.0",
                stages=(0, 50, 50, 100),
            )

    def test_deploy_publishes_canary_started(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("canary_started", events.append)

        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        assert len(events) == 1
        assert events[0].source == "dep-1"
        assert events[0].payload["canary_version"] == "1.1.0"

    def test_deploy_registers_a_scheduler_job(self):
        scheduler = GovernanceScheduler(clock=_clock)
        engine = _engine(scheduler=scheduler)

        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        names = {job.name for job in scheduler.jobs()}

        assert "canary-evaluation-dep-1" in names


# --- Traffic progression / promotion -------------------------------------


class TestPromote:

    def test_promote_requires_evaluation(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        with pytest.raises(ValueError, match="has not passed a health"):
            engine.promote("dep-1")

    def test_promote_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.promote("dep-1")

    def test_promote_after_evaluation_advances_stage(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")

        promoted = engine.promote("dep-1")

        assert promoted.stage == 1
        assert promoted.traffic_percentage == DEFAULT_STAGES[1]

    def test_promote_requires_revaluation_after_a_prior_promotion(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")
        engine.promote("dep-1")

        with pytest.raises(ValueError, match="has not passed a health"):
            engine.promote("dep-1")

    def test_full_progression_through_every_stage_completes(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        for _ in range(len(DEFAULT_STAGES) - 1):
            engine.evaluate("dep-1")
            engine.promote("dep-1")

        status = engine.status("dep-1")

        assert status.traffic_percentage == 100
        assert status.stage == len(DEFAULT_STAGES) - 1

    def test_promote_publishes_canary_promoted(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")

        events = []
        bus.subscribe("canary_promoted", events.append)

        engine.promote("dep-1")

        assert len(events) == 1


# --- Pause / resume ------------------------------------------------------


class TestPauseResume:

    def test_pause_blocks_promotion(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")
        engine.pause("dep-1")

        with pytest.raises(ValueError, match="is paused"):
            engine.promote("dep-1")

    def test_resume_allows_promotion_again(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")
        engine.pause("dep-1")
        engine.resume("dep-1")

        promoted = engine.promote("dep-1")

        assert promoted.stage == 1

    def test_pause_is_idempotent(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        engine.pause("dep-1")
        record = engine.pause("dep-1")

        assert record.deployment_id == "dep-1"

    def test_resume_is_idempotent(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        record = engine.resume("dep-1")

        assert record.deployment_id == "dep-1"

    def test_pause_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.pause("dep-1")

    def test_pause_publishes_only_on_first_pause(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        events = []
        bus.subscribe("canary_paused", events.append)

        engine.pause("dep-1")
        engine.pause("dep-1")

        assert len(events) == 1

    def test_resume_publishes_only_when_previously_paused(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.pause("dep-1")

        events = []
        bus.subscribe("canary_resumed", events.append)

        engine.resume("dep-1")
        engine.resume("dep-1")

        assert len(events) == 1


# --- Successful completion ------------------------------------------------


class TestCompletion:

    def test_completion_frees_deployment_id_for_reuse(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        for _ in range(len(DEFAULT_STAGES) - 1):
            engine.evaluate("dep-1")
            engine.promote("dep-1")

        deployment = engine.deploy(
            "dep-1", "1.3.0", stable_version="1.1.0"
        )

        assert deployment.canary_version == "1.3.0"

    def test_completion_publishes_canary_completed(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        events = []
        bus.subscribe("canary_completed", events.append)

        for _ in range(len(DEFAULT_STAGES) - 1):
            engine.evaluate("dep-1")
            engine.promote("dep-1")

        assert len(events) == 1

    def test_completion_unregisters_the_scheduler_job(self):
        scheduler = GovernanceScheduler(clock=_clock)
        engine = _engine(scheduler=scheduler)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        for _ in range(len(DEFAULT_STAGES) - 1):
            engine.evaluate("dep-1")
            engine.promote("dep-1")

        names = {job.name for job in scheduler.jobs()}

        assert "canary-evaluation-dep-1" not in names

    def test_promote_past_final_stage_raises(self):
        engine = _engine(default_stages=(0, 100))
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")

        # This promote() completes the canary and frees deployment_id
        # for reuse; a second promote() on the now-terminal record
        # (never redeployed) must be rejected as not-active.
        engine.promote("dep-1")

        with pytest.raises(ValueError, match="is not active"):
            engine.promote("dep-1")


# --- Automatic rollback ---------------------------------------------------


class TestAutomaticRollback:

    def test_failed_evaluation_rolls_back_automatically(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")
        engine.promote("dep-1")

        engine.evaluate("dep-1", check=lambda: False)

        status = engine.status("dep-1")

        assert status.stage == 0
        assert status.traffic_percentage == 0

    def test_rollback_frees_deployment_id_for_reuse(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        engine.evaluate("dep-1", check=lambda: False)

        deployment = engine.deploy(
            "dep-1", "1.2.0", stable_version="1.0.0"
        )

        assert deployment.canary_version == "1.2.0"

    def test_manual_rollback_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.rollback("dep-1")

    def test_rollback_on_inactive_canary_raises(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.rollback("dep-1")

        with pytest.raises(ValueError, match="is not active"):
            engine.rollback("dep-1")

    def test_failed_evaluation_publishes_canary_failed_and_rolled_back(
        self,
    ):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        failed_events = []
        rolled_back_events = []
        bus.subscribe("canary_failed", failed_events.append)
        bus.subscribe("canary_rolled_back", rolled_back_events.append)

        engine.evaluate("dep-1", check=lambda: False)

        assert len(failed_events) == 1
        assert len(rolled_back_events) == 1

    def test_manual_rollback_publishes_canary_rolled_back(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        events = []
        bus.subscribe("canary_rolled_back", events.append)

        engine.rollback("dep-1")

        assert len(events) == 1


# --- Status / history ------------------------------------------------------


class TestStatus:

    def test_status_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.status("dep-1")


class TestHistory:

    def test_history_records_every_evaluation(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        engine.evaluate("dep-1")
        engine.promote("dep-1")
        engine.evaluate("dep-1")

        history = engine.history("dep-1")

        assert len(history) == 2
        assert all(entry.healthy for entry in history)

    def test_history_empty_for_unknown_deployment(self):
        engine = _engine()

        assert engine.history("dep-1") == ()

    def test_evaluate_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.evaluate("dep-1")

    def test_evaluate_on_inactive_canary_raises(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.rollback("dep-1")

        with pytest.raises(ValueError, match="is not active"):
            engine.evaluate("dep-1")


class TestList:

    def test_list_orders_by_deployment_id(self):
        engine = _engine()
        engine.deploy("dep-b", "1.1.0", stable_version="1.0.0")
        engine.deploy("dep-a", "1.1.0", stable_version="1.0.0")

        listed = engine.list()

        assert [d.deployment_id for d in listed] == ["dep-a", "dep-b"]

    def test_list_empty_when_nothing_deployed(self):
        engine = _engine()

        assert engine.list() == ()


# --- Metrics integration -----------------------------------------------


class TestMetricsIntegration:

    def test_healthy_evaluation_records_completion(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        engine = _engine(metrics=metrics)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        engine.evaluate("dep-1")

        assert metrics.snapshot().jobs_completed == 1

    def test_failed_evaluation_records_failure(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        engine = _engine(metrics=metrics)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        engine.evaluate("dep-1", check=lambda: False)

        assert metrics.snapshot().jobs_failed == 1

    def test_no_metrics_wired_is_safe(self):
        engine = _engine(metrics=None)
        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")

        engine.evaluate("dep-1")


# --- Event publication -----------------------------------------------------


class TestEventPublication:

    def test_no_event_bus_is_safe(self):
        engine = _engine(event_bus=None)

        engine.deploy("dep-1", "1.1.0", stable_version="1.0.0")
        engine.evaluate("dep-1")
        engine.pause("dep-1")
        engine.resume("dep-1")
        engine.promote("dep-1")
        engine.rollback("dep-1")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_canary_engine_returns_same_instance(self):
        assert get_canary_engine() is get_canary_engine()

    def test_singleton_is_wired_to_singleton_scheduler_and_metrics(self):
        get_version_registry().register(
            "dep-singleton-wiring", "1.0.0", "a.tar.gz", VALID_CHECKSUM,
        )

        get_canary_engine().deploy("dep-singleton-wiring", "1.1.0")

        names = {job.name for job in get_scheduler().jobs()}

        assert "canary-evaluation-dep-singleton-wiring" in names


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceCanaryApi:

    def test_post_deploy(self, client):
        response = client.post(
            "/governance/canary/dep-api-1/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["stable_version"] == "1.0.0"
        assert payload["canary_version"] == "1.1.0"
        assert payload["stage"] == 0

    def test_post_deploy_invalid_version_returns_409(self, client):
        response = client.post(
            "/governance/canary/dep-api-2/deploy",
            params={"canary_version": "bogus", "stable_version": "1.0.0"},
        )

        assert response.status_code == 409

    def test_get_deployment(self, client):
        client.post(
            "/governance/canary/dep-api-3/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )

        response = client.get("/governance/canary/dep-api-3")

        assert response.status_code == 200
        assert response.json()["stage"] == 0

    def test_get_unknown_deployment_returns_404(self, client):
        response = client.get("/governance/canary/does-not-exist")

        assert response.status_code == 404

    def test_list_deployments(self, client):
        client.post(
            "/governance/canary/dep-api-4/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )

        response = client.get("/governance/canary")

        assert response.status_code == 200
        assert any(
            d["deployment_id"] == "dep-api-4" for d in response.json()
        )

    def test_promote_without_evaluation_returns_409(self, client):
        client.post(
            "/governance/canary/dep-api-5/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )

        response = client.post("/governance/canary/dep-api-5/promote")

        assert response.status_code == 409

    def test_evaluate_then_promote_succeeds(self, client):
        client.post(
            "/governance/canary/dep-api-6/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )
        evaluate_response = client.post(
            "/governance/canary/dep-api-6/evaluate"
        )
        assert evaluate_response.json()["healthy"] is True

        response = client.post("/governance/canary/dep-api-6/promote")

        assert response.status_code == 200
        assert response.json()["stage"] == 1

    def test_pause_then_promote_returns_409(self, client):
        client.post(
            "/governance/canary/dep-api-7/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )
        client.post("/governance/canary/dep-api-7/evaluate")
        client.post("/governance/canary/dep-api-7/pause")

        response = client.post("/governance/canary/dep-api-7/promote")

        assert response.status_code == 409

    def test_resume_then_promote_succeeds(self, client):
        client.post(
            "/governance/canary/dep-api-8/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )
        client.post("/governance/canary/dep-api-8/evaluate")
        client.post("/governance/canary/dep-api-8/pause")
        client.post("/governance/canary/dep-api-8/resume")

        response = client.post("/governance/canary/dep-api-8/promote")

        assert response.status_code == 200

    def test_rollback(self, client):
        client.post(
            "/governance/canary/dep-api-9/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )

        response = client.post("/governance/canary/dep-api-9/rollback")

        assert response.status_code == 200
        assert response.json()["stage"] == 0

    def test_rollback_unknown_deployment_returns_404(self, client):
        response = client.post(
            "/governance/canary/does-not-exist/rollback"
        )

        assert response.status_code == 404

    def test_evaluate_defaults_to_healthy_over_the_api(self, client):
        client.post(
            "/governance/canary/dep-api-10/deploy",
            params={"canary_version": "1.1.0", "stable_version": "1.0.0"},
        )

        response = client.post("/governance/canary/dep-api-10/evaluate")

        assert response.status_code == 200
        assert response.json()["healthy"] is True

    def test_evaluate_unknown_deployment_returns_404(self, client):
        response = client.post(
            "/governance/canary/does-not-exist/evaluate"
        )

        assert response.status_code == 404
