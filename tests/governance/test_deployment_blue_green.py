from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_blue_green import (
    BlueGreenDeployment,
    BlueGreenDeploymentEngine,
    BlueGreenSwitchResult,
    get_blue_green_engine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_version_registry import (
    DeploymentVersionRegistry,
    get_version_registry,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

VALID_CHECKSUM = "a" * 64


def _clock():
    return BASE_TIME


def _engine(event_bus=None, version_registry=None) -> BlueGreenDeploymentEngine:
    return BlueGreenDeploymentEngine(
        clock=_clock, event_bus=event_bus, version_registry=version_registry,
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The Blue/Green engine and version registry are both process-wide
    singletons; most tests below construct their own fresh engine
    instead (see _engine), and only the singleton and API tests touch
    the shared instances, matching test_deployment_rollout_manager.py's
    own fixture.
    """

    def _reset():
        get_blue_green_engine().clear()
        get_version_registry().clear()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestBlueGreenDeployment:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            BlueGreenDeployment(
                deployment_id="", blue_version="1.0.0",
                green_version="1.1.0", active_environment="BLUE",
                created_at=BASE_TIME,
            )

    def test_rejects_invalid_blue_version(self):
        with pytest.raises(ValueError, match="blue_version"):
            BlueGreenDeployment(
                deployment_id="dep-1", blue_version="bogus",
                green_version="1.1.0", active_environment="BLUE",
                created_at=BASE_TIME,
            )

    def test_rejects_invalid_green_version(self):
        with pytest.raises(ValueError, match="green_version"):
            BlueGreenDeployment(
                deployment_id="dep-1", blue_version="1.0.0",
                green_version="bogus", active_environment="BLUE",
                created_at=BASE_TIME,
            )

    def test_rejects_unknown_active_environment(self):
        with pytest.raises(
            ValueError, match="active_environment must be one of"
        ):
            BlueGreenDeployment(
                deployment_id="dep-1", blue_version="1.0.0",
                green_version="1.1.0", active_environment="PURPLE",
                created_at=BASE_TIME,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            BlueGreenDeployment(
                deployment_id="dep-1", blue_version="1.0.0",
                green_version="1.1.0", active_environment="BLUE",
                created_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_to_dict(self):
        deployment = BlueGreenDeployment(
            deployment_id="dep-1", blue_version="1.0.0",
            green_version="1.1.0", active_environment="BLUE",
            created_at=BASE_TIME,
        )

        assert deployment.to_dict() == {
            "deployment_id": "dep-1",
            "blue_version": "1.0.0",
            "green_version": "1.1.0",
            "active_environment": "BLUE",
            "created_at": BASE_TIME.isoformat(),
        }


class TestBlueGreenSwitchResult:

    def test_rejects_unknown_previous_environment(self):
        with pytest.raises(
            ValueError, match="previous_environment must be one of"
        ):
            BlueGreenSwitchResult(
                deployment_id="dep-1", previous_environment="PURPLE",
                active_environment="GREEN", switched_at=BASE_TIME,
                success=True,
            )

    def test_rejects_naive_switched_at(self):
        with pytest.raises(
            ValueError, match="switched_at must be timezone-aware"
        ):
            BlueGreenSwitchResult(
                deployment_id="dep-1", previous_environment="BLUE",
                active_environment="GREEN",
                switched_at=datetime(2026, 7, 23, 12, 0, 0),
                success=True,
            )


# --- Deployment initialization ----------------------------------------


class TestDeploy:

    def test_deploy_creates_a_blue_active_record(self):
        engine = _engine()

        deployment = engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        assert deployment.blue_version == "1.0.0"
        assert deployment.green_version == "1.1.0"
        assert deployment.active_environment == "BLUE"

    def test_deploy_resolves_blue_version_from_registry(self):
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        engine = _engine(version_registry=registry)

        deployment = engine.deploy("dep-1", "1.1.0")

        assert deployment.blue_version == "1.0.0"
        assert deployment.green_version == "1.1.0"

    def test_deploy_without_blue_version_or_registry_raises(self):
        engine = _engine()

        with pytest.raises(ValueError, match="blue_version must be provided"):
            engine.deploy("dep-1", "1.1.0")

    def test_deploy_without_registry_entry_raises_key_error(self):
        registry = DeploymentVersionRegistry(clock=_clock)
        engine = _engine(version_registry=registry)

        with pytest.raises(KeyError):
            engine.deploy("dep-1", "1.1.0")

    def test_deploy_rejects_invalid_green_version(self):
        engine = _engine()

        with pytest.raises(ValueError, match="green_version"):
            engine.deploy("dep-1", "bogus", blue_version="1.0.0")

    def test_redeploy_replaces_idle_environment_and_resets_validation(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")

        deployment = engine.deploy("dep-1", "1.2.0", blue_version="1.0.0")

        assert deployment.green_version == "1.2.0"
        assert deployment.active_environment == "BLUE"

        with pytest.raises(ValueError, match="has not been validated"):
            engine.switch("dep-1")

    def test_redeploy_after_switch_targets_the_new_idle_blue_slot(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")
        engine.switch("dep-1")

        deployment = engine.deploy("dep-1", "1.2.0")

        assert deployment.active_environment == "GREEN"
        assert deployment.green_version == "1.1.0"
        assert deployment.blue_version == "1.2.0"

    def test_deploy_publishes_blue_green_started(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)

        events = []
        bus.subscribe("blue_green_started", events.append)

        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        assert len(events) == 1
        assert events[0].source == "dep-1"
        assert events[0].payload["green_version"] == "1.1.0"


# --- Environment validation -------------------------------------------


class TestValidate:

    def test_validate_with_no_check_succeeds(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        assert engine.validate("dep-1") is True

    def test_validate_with_passing_check(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        assert engine.validate("dep-1", check=lambda: True) is True

    def test_validate_with_failing_check(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        assert engine.validate("dep-1", check=lambda: False) is False

    def test_validate_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.validate("dep-1")

    def test_validate_publishes_green_environment_ready_only_on_success(
        self,
    ):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        events = []
        bus.subscribe("green_environment_ready", events.append)

        engine.validate("dep-1", check=lambda: False)
        assert events == []

        engine.validate("dep-1", check=lambda: True)
        assert len(events) == 1


# --- Successful switch / atomic switch guarantee ------------------------


class TestSwitch:

    def test_switch_requires_validation(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        with pytest.raises(ValueError, match="has not been validated"):
            engine.switch("dep-1")

    def test_switch_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.switch("dep-1")

    def test_successful_switch_flips_active_environment(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")

        result = engine.switch("dep-1")

        assert result.previous_environment == "BLUE"
        assert result.active_environment == "GREEN"
        assert result.success is True
        assert engine.status("dep-1").active_environment == "GREEN"

    def test_switch_back_and_forth(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")
        engine.switch("dep-1")

        engine.validate("dep-1")
        result = engine.switch("dep-1")

        assert result.previous_environment == "GREEN"
        assert result.active_environment == "BLUE"

    def test_switch_requires_revalidation_after_a_prior_switch(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")
        engine.switch("dep-1")

        with pytest.raises(ValueError, match="has not been validated"):
            engine.switch("dep-1")

    def test_switch_appends_to_history(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")

        engine.switch("dep-1")

        history = engine.history("dep-1")

        assert len(history) == 1
        assert history[0].active_environment == "GREEN"

    def test_atomic_switch_guarantee_exactly_one_active_environment(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")

        before = engine.status("dep-1").active_environment
        engine.switch("dep-1")
        after = engine.status("dep-1").active_environment

        assert before != after
        assert after in ("BLUE", "GREEN")

    def test_switch_publishes_traffic_switched_and_blue_green_completed(
        self,
    ):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")

        switched_events = []
        completed_events = []
        bus.subscribe("traffic_switched", switched_events.append)
        bus.subscribe("blue_green_completed", completed_events.append)

        engine.switch("dep-1")

        assert len(switched_events) == 1
        assert len(completed_events) == 1


# --- Rollback ------------------------------------------------------------


class TestRollback:

    def test_rollback_without_prior_switch_raises(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        with pytest.raises(ValueError, match="nothing to roll back"):
            engine.rollback("dep-1")

    def test_rollback_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.rollback("dep-1")

    def test_rollback_restores_the_previous_active_environment(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")
        engine.switch("dep-1")

        result = engine.rollback("dep-1")

        assert result.active_environment == "BLUE"
        assert engine.status("dep-1").active_environment == "BLUE"

    def test_rollback_appends_to_history(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")
        engine.switch("dep-1")

        engine.rollback("dep-1")

        assert len(engine.history("dep-1")) == 2

    def test_rollback_publishes_blue_green_rollback(self):
        bus = GovernanceEventBus(clock=_clock)
        engine = _engine(event_bus=bus)
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")
        engine.switch("dep-1")

        events = []
        bus.subscribe("blue_green_rollback", events.append)

        engine.rollback("dep-1")

        assert len(events) == 1
        assert events[0].source == "dep-1"


# --- Status retrieval --------------------------------------------------


class TestStatus:

    def test_status_unknown_deployment_raises_key_error(self):
        engine = _engine()

        with pytest.raises(KeyError):
            engine.status("dep-1")

    def test_status_reflects_current_state(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        status = engine.status("dep-1")

        assert status.deployment_id == "dep-1"
        assert status.active_environment == "BLUE"


class TestHistory:

    def test_history_empty_before_any_switch(self):
        engine = _engine()
        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        assert engine.history("dep-1") == ()

    def test_history_empty_for_unknown_deployment(self):
        engine = _engine()

        assert engine.history("dep-1") == ()


class TestList:

    def test_list_orders_by_deployment_id(self):
        engine = _engine()
        engine.deploy("dep-b", "1.1.0", blue_version="1.0.0")
        engine.deploy("dep-a", "1.1.0", blue_version="1.0.0")

        listed = engine.list()

        assert [d.deployment_id for d in listed] == ["dep-a", "dep-b"]

    def test_list_empty_when_nothing_deployed(self):
        engine = _engine()

        assert engine.list() == ()


# --- Event publication -----------------------------------------------------


class TestEventPublication:

    def test_no_event_bus_is_safe(self):
        engine = _engine(event_bus=None)

        engine.deploy("dep-1", "1.1.0", blue_version="1.0.0")
        engine.validate("dep-1")
        engine.switch("dep-1")
        engine.rollback("dep-1")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_blue_green_engine_returns_same_instance(self):
        assert get_blue_green_engine() is get_blue_green_engine()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceBlueGreenApi:

    def test_post_deploy(self, client):
        response = client.post(
            "/governance/blue-green/dep-api-1/deploy",
            params={"green_version": "1.1.0", "blue_version": "1.0.0"},
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["blue_version"] == "1.0.0"
        assert payload["green_version"] == "1.1.0"
        assert payload["active_environment"] == "BLUE"

    def test_post_deploy_invalid_version_returns_409(self, client):
        response = client.post(
            "/governance/blue-green/dep-api-2/deploy",
            params={"green_version": "bogus", "blue_version": "1.0.0"},
        )

        assert response.status_code == 409

    def test_get_deployment(self, client):
        client.post(
            "/governance/blue-green/dep-api-3/deploy",
            params={"green_version": "1.1.0", "blue_version": "1.0.0"},
        )

        response = client.get("/governance/blue-green/dep-api-3")

        assert response.status_code == 200
        assert response.json()["active_environment"] == "BLUE"

    def test_get_unknown_deployment_returns_404(self, client):
        response = client.get("/governance/blue-green/does-not-exist")

        assert response.status_code == 404

    def test_list_deployments(self, client):
        client.post(
            "/governance/blue-green/dep-api-4/deploy",
            params={"green_version": "1.1.0", "blue_version": "1.0.0"},
        )

        response = client.get("/governance/blue-green")

        assert response.status_code == 200
        assert any(
            d["deployment_id"] == "dep-api-4" for d in response.json()
        )

    def test_switch_without_validation_returns_409(self, client):
        client.post(
            "/governance/blue-green/dep-api-5/deploy",
            params={"green_version": "1.1.0", "blue_version": "1.0.0"},
        )

        response = client.post("/governance/blue-green/dep-api-5/switch")

        assert response.status_code == 409

    def test_validate_then_switch_succeeds(self, client):
        client.post(
            "/governance/blue-green/dep-api-6/deploy",
            params={"green_version": "1.1.0", "blue_version": "1.0.0"},
        )
        validate_response = client.post(
            "/governance/blue-green/dep-api-6/validate"
        )
        assert validate_response.json()["validated"] is True

        response = client.post("/governance/blue-green/dep-api-6/switch")

        assert response.status_code == 200
        assert response.json()["active_environment"] == "GREEN"

    def test_switch_unknown_deployment_returns_404(self, client):
        response = client.post(
            "/governance/blue-green/does-not-exist/switch"
        )

        assert response.status_code == 404

    def test_rollback_restores_previous_environment(self, client):
        client.post(
            "/governance/blue-green/dep-api-7/deploy",
            params={"green_version": "1.1.0", "blue_version": "1.0.0"},
        )
        client.post("/governance/blue-green/dep-api-7/validate")
        client.post("/governance/blue-green/dep-api-7/switch")

        response = client.post(
            "/governance/blue-green/dep-api-7/rollback"
        )

        assert response.status_code == 200
        assert response.json()["active_environment"] == "BLUE"

    def test_rollback_without_switch_returns_409(self, client):
        client.post(
            "/governance/blue-green/dep-api-8/deploy",
            params={"green_version": "1.1.0", "blue_version": "1.0.0"},
        )

        response = client.post(
            "/governance/blue-green/dep-api-8/rollback"
        )

        assert response.status_code == 409

    def test_rollback_unknown_deployment_returns_404(self, client):
        response = client.post(
            "/governance/blue-green/does-not-exist/rollback"
        )

        assert response.status_code == 404
