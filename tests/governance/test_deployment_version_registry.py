from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_version_registry import (
    DeploymentRevision,
    DeploymentVersion,
    DeploymentVersionRegistry,
    get_version_registry,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

VALID_CHECKSUM = "a" * 64
OTHER_CHECKSUM = "b" * 64


def _clock():
    return BASE_TIME


def _registry(event_bus=None) -> DeploymentVersionRegistry:
    return DeploymentVersionRegistry(clock=_clock, event_bus=event_bus)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The version registry is a process-wide singleton; most tests below
    construct their own fresh registry instead (see _registry), and
    only the singleton and API tests touch the shared instance,
    matching test_deployment_rollout_manager.py's own fixture.
    """

    def _reset():
        get_version_registry().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestDeploymentVersion:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            DeploymentVersion(
                deployment_id="", version="1.0.0", artifact="a.tar.gz",
                checksum=VALID_CHECKSUM, created_at=BASE_TIME,
                metadata={},
            )

    def test_rejects_invalid_semantic_version(self):
        with pytest.raises(ValueError, match="not a valid semantic version"):
            DeploymentVersion(
                deployment_id="dep-1", version="v1", artifact="a.tar.gz",
                checksum=VALID_CHECKSUM, created_at=BASE_TIME,
                metadata={},
            )

    def test_accepts_prerelease_and_build_metadata_versions(self):
        version = DeploymentVersion(
            deployment_id="dep-1", version="2.0.0-rc.1+build.5",
            artifact="a.tar.gz", checksum=VALID_CHECKSUM,
            created_at=BASE_TIME, metadata={},
        )

        assert version.version == "2.0.0-rc.1+build.5"

    def test_rejects_empty_artifact(self):
        with pytest.raises(ValueError, match="artifact must not be empty"):
            DeploymentVersion(
                deployment_id="dep-1", version="1.0.0", artifact="",
                checksum=VALID_CHECKSUM, created_at=BASE_TIME,
                metadata={},
            )

    def test_rejects_malformed_checksum(self):
        with pytest.raises(
            ValueError, match="checksum must be a 64-character hex string"
        ):
            DeploymentVersion(
                deployment_id="dep-1", version="1.0.0", artifact="a.tar.gz",
                checksum="not-a-checksum", created_at=BASE_TIME,
                metadata={},
            )

    def test_rejects_checksum_of_wrong_length(self):
        with pytest.raises(
            ValueError, match="checksum must be a 64-character hex string"
        ):
            DeploymentVersion(
                deployment_id="dep-1", version="1.0.0", artifact="a.tar.gz",
                checksum="a" * 63, created_at=BASE_TIME, metadata={},
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            DeploymentVersion(
                deployment_id="dep-1", version="1.0.0", artifact="a.tar.gz",
                checksum=VALID_CHECKSUM,
                created_at=datetime(2026, 7, 23, 12, 0, 0), metadata={},
            )

    def test_metadata_is_immutable(self):
        original = {"owner": "team-a"}
        version = DeploymentVersion(
            deployment_id="dep-1", version="1.0.0", artifact="a.tar.gz",
            checksum=VALID_CHECKSUM, created_at=BASE_TIME,
            metadata=original,
        )

        original["owner"] = "mutated"

        assert version.metadata["owner"] == "team-a"

        with pytest.raises(TypeError):
            version.metadata["owner"] = "mutated-again"

    def test_to_dict(self):
        version = DeploymentVersion(
            deployment_id="dep-1", version="1.0.0", artifact="a.tar.gz",
            checksum=VALID_CHECKSUM, created_at=BASE_TIME,
            metadata={"owner": "team-a"},
        )

        assert version.to_dict() == {
            "deployment_id": "dep-1",
            "version": "1.0.0",
            "artifact": "a.tar.gz",
            "checksum": VALID_CHECKSUM,
            "created_at": BASE_TIME.isoformat(),
            "metadata": {"owner": "team-a"},
        }


class TestDeploymentRevision:

    def test_rejects_unknown_state(self):
        with pytest.raises(ValueError, match="state must be one of"):
            DeploymentRevision(
                revision_id="r-1", deployment_id="dep-1", version="1.0.0",
                state="BOGUS", created_at=BASE_TIME,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            DeploymentRevision(
                revision_id="r-1", deployment_id="dep-1", version="1.0.0",
                state="REGISTERED",
                created_at=datetime(2026, 7, 23, 12, 0, 0),
            )


# --- Registration ----------------------------------------------------------


class TestRegister:

    def test_register_returns_the_stored_version(self):
        registry = _registry()

        version = registry.register(
            "dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM
        )

        assert version.deployment_id == "dep-1"
        assert version.version == "1.0.0"
        assert version.artifact == "a.tar.gz"
        assert version.checksum == VALID_CHECKSUM

    def test_register_defaults_metadata_to_empty(self):
        registry = _registry()

        version = registry.register(
            "dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM
        )

        assert dict(version.metadata) == {}

    def test_register_rejects_duplicate_deployment_id(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        with pytest.raises(ValueError, match="already registered"):
            registry.register("dep-1", "1.1.0", "a2.tar.gz", VALID_CHECKSUM)

    def test_register_rejects_invalid_semantic_version(self):
        registry = _registry()

        with pytest.raises(ValueError, match="not a valid semantic version"):
            registry.register("dep-1", "not-a-version", "a.tar.gz", VALID_CHECKSUM)

    def test_register_rejects_malformed_checksum(self):
        registry = _registry()

        with pytest.raises(ValueError, match="checksum"):
            registry.register("dep-1", "1.0.0", "a.tar.gz", "bad-checksum")

    def test_register_allows_reuse_after_removal(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.remove("dep-1")

        version = registry.register(
            "dep-1", "2.0.0", "a2.tar.gz", OTHER_CHECKSUM
        )

        assert version.version == "2.0.0"

    def test_register_creates_initial_revision(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        history = registry.history("dep-1")

        assert len(history) == 1
        assert history[0].state == "REGISTERED"
        assert history[0].version == "1.0.0"

    def test_register_publishes_deployment_registered_and_revision_created(
        self,
    ):
        bus = GovernanceEventBus(clock=_clock)
        registry = _registry(event_bus=bus)

        registered_events = []
        revision_events = []
        bus.subscribe("deployment_registered", registered_events.append)
        bus.subscribe(
            "deployment_revision_created", revision_events.append
        )

        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        assert len(registered_events) == 1
        assert registered_events[0].source == "dep-1"
        assert registered_events[0].payload["version"] == "1.0.0"

        assert len(revision_events) == 1
        assert revision_events[0].payload["state"] == "REGISTERED"


# --- Update ------------------------------------------------------------


class TestUpdate:

    def test_update_replaces_the_current_version(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        updated = registry.update(
            "dep-1", "1.1.0", "a2.tar.gz", OTHER_CHECKSUM
        )

        assert updated.version == "1.1.0"
        assert registry.get("dep-1").version == "1.1.0"

    def test_update_unregistered_deployment_raises_key_error(self):
        registry = _registry()

        with pytest.raises(KeyError):
            registry.update("dep-1", "1.1.0", "a.tar.gz", VALID_CHECKSUM)

    def test_update_rejects_invalid_semantic_version(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        with pytest.raises(ValueError, match="not a valid semantic version"):
            registry.update("dep-1", "bad", "a2.tar.gz", VALID_CHECKSUM)

    def test_update_appends_to_history_without_removing_prior_entries(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.update("dep-1", "1.1.0", "a2.tar.gz", OTHER_CHECKSUM)

        history = registry.history("dep-1")

        assert [revision.state for revision in history] == [
            "REGISTERED", "UPDATED",
        ]
        assert [revision.version for revision in history] == [
            "1.0.0", "1.1.0",
        ]

    def test_update_publishes_deployment_updated_and_revision_created(self):
        bus = GovernanceEventBus(clock=_clock)
        registry = _registry(event_bus=bus)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        updated_events = []
        bus.subscribe("deployment_updated", updated_events.append)

        registry.update("dep-1", "1.1.0", "a2.tar.gz", OTHER_CHECKSUM)

        assert len(updated_events) == 1
        assert updated_events[0].payload["version"] == "1.1.0"


# --- Remove --------------------------------------------------------------


class TestRemove:

    def test_remove_clears_the_active_registration(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        registry.remove("dep-1")

        assert registry.exists("dep-1") is False

        with pytest.raises(KeyError):
            registry.get("dep-1")

    def test_remove_unregistered_deployment_raises_key_error(self):
        registry = _registry()

        with pytest.raises(KeyError):
            registry.remove("dep-1")

    def test_remove_preserves_history(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        registry.remove("dep-1")

        history = registry.history("dep-1")

        assert [revision.state for revision in history] == [
            "REGISTERED", "REMOVED",
        ]

    def test_remove_publishes_deployment_removed(self):
        bus = GovernanceEventBus(clock=_clock)
        registry = _registry(event_bus=bus)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        removed_events = []
        bus.subscribe("deployment_removed", removed_events.append)

        registry.remove("dep-1")

        assert len(removed_events) == 1
        assert removed_events[0].source == "dep-1"


# --- Get / latest / exists ------------------------------------------------


class TestGetLatestExists:

    def test_get_unregistered_deployment_raises_key_error(self):
        registry = _registry()

        with pytest.raises(KeyError):
            registry.get("dep-1")

    def test_latest_matches_get(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        assert registry.latest("dep-1") == registry.get("dep-1")

    def test_latest_reflects_the_most_recent_update(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.update("dep-1", "2.0.0", "a2.tar.gz", OTHER_CHECKSUM)

        assert registry.latest("dep-1").version == "2.0.0"

    def test_exists_false_before_registration(self):
        registry = _registry()

        assert registry.exists("dep-1") is False

    def test_exists_true_after_registration(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        assert registry.exists("dep-1") is True

    def test_exists_false_after_removal(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.remove("dep-1")

        assert registry.exists("dep-1") is False


# --- History ---------------------------------------------------------------


class TestHistory:

    def test_history_empty_for_never_registered_deployment(self):
        registry = _registry()

        assert registry.history("dep-1") == ()

    def test_history_is_append_only_and_ordered(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.update("dep-1", "1.1.0", "a2.tar.gz", OTHER_CHECKSUM)
        registry.update("dep-1", "1.2.0", "a3.tar.gz", VALID_CHECKSUM)
        registry.remove("dep-1")

        history = registry.history("dep-1")

        assert [revision.version for revision in history] == [
            "1.0.0", "1.1.0", "1.2.0", "1.2.0",
        ]
        assert [revision.state for revision in history] == [
            "REGISTERED", "UPDATED", "UPDATED", "REMOVED",
        ]

    def test_history_survives_reregistration(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.remove("dep-1")
        registry.register("dep-1", "2.0.0", "a2.tar.gz", OTHER_CHECKSUM)

        history = registry.history("dep-1")

        assert [revision.state for revision in history] == [
            "REGISTERED", "REMOVED", "REGISTERED",
        ]


# --- List ------------------------------------------------------------------


class TestList:

    def test_list_orders_by_deployment_id(self):
        registry = _registry()
        registry.register("dep-b", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.register("dep-a", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        listed = registry.list()

        assert [version.deployment_id for version in listed] == [
            "dep-a", "dep-b",
        ]

    def test_list_excludes_removed_deployments(self):
        registry = _registry()
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.remove("dep-1")

        assert registry.list() == ()

    def test_list_empty_when_nothing_registered(self):
        registry = _registry()

        assert registry.list() == ()


# --- Event publication -----------------------------------------------------


class TestEventPublication:

    def test_no_event_bus_is_safe(self):
        registry = _registry(event_bus=None)

        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.update("dep-1", "1.1.0", "a2.tar.gz", OTHER_CHECKSUM)
        registry.remove("dep-1")


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_version_registry_returns_same_instance(self):
        assert get_version_registry() is get_version_registry()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceDeploymentApi:

    def test_post_registers_deployment(self, client):
        response = client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-1",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["deployment_id"] == "dep-api-1"
        assert payload["version"] == "1.0.0"

    def test_post_with_metadata(self, client):
        response = client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-2",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
                "metadata": '{"owner": "team-a"}',
            },
        )

        assert response.status_code == 200
        assert response.json()["metadata"] == {"owner": "team-a"}

    def test_post_duplicate_returns_409(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-3",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        response = client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-3",
                "version": "1.1.0",
                "artifact": "a2.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        assert response.status_code == 409

    def test_post_invalid_version_returns_409(self, client):
        response = client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-4",
                "version": "not-a-version",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        assert response.status_code == 409

    def test_post_invalid_metadata_json_returns_422(self, client):
        response = client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-5",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
                "metadata": "not-json",
            },
        )

        assert response.status_code == 422

    def test_get_deployment(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-6",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        response = client.get("/governance/deployments/dep-api-6")

        assert response.status_code == 200
        assert response.json()["version"] == "1.0.0"

    def test_get_unknown_deployment_returns_404(self, client):
        response = client.get("/governance/deployments/does-not-exist")

        assert response.status_code == 404

    def test_list_deployments(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-7",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        response = client.get("/governance/deployments")

        assert response.status_code == 200
        assert any(
            version["deployment_id"] == "dep-api-7"
            for version in response.json()
        )

    def test_patch_updates_deployment(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-8",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        response = client.patch(
            "/governance/deployments/dep-api-8",
            params={
                "version": "2.0.0",
                "artifact": "a2.tar.gz",
                "checksum": OTHER_CHECKSUM,
            },
        )

        assert response.status_code == 200
        assert response.json()["version"] == "2.0.0"

    def test_patch_unknown_deployment_returns_404(self, client):
        response = client.patch(
            "/governance/deployments/does-not-exist",
            params={
                "version": "2.0.0",
                "artifact": "a2.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        assert response.status_code == 404

    def test_get_history(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-9",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )
        client.patch(
            "/governance/deployments/dep-api-9",
            params={
                "version": "2.0.0",
                "artifact": "a2.tar.gz",
                "checksum": OTHER_CHECKSUM,
            },
        )

        response = client.get(
            "/governance/deployments/dep-api-9/history"
        )

        assert response.status_code == 200

        payload = response.json()

        assert [entry["state"] for entry in payload] == [
            "REGISTERED", "UPDATED",
        ]

    def test_get_history_empty_for_unknown_deployment(self, client):
        response = client.get(
            "/governance/deployments/does-not-exist/history"
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_delete_removes_deployment(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-10",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        response = client.delete("/governance/deployments/dep-api-10")

        assert response.status_code == 200
        assert response.json() == {"removed": "dep-api-10"}

        get_response = client.get("/governance/deployments/dep-api-10")
        assert get_response.status_code == 404

    def test_delete_unknown_deployment_returns_404(self, client):
        response = client.delete("/governance/deployments/does-not-exist")

        assert response.status_code == 404

    def test_delete_then_history_still_available(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-11",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )
        client.delete("/governance/deployments/dep-api-11")

        response = client.get(
            "/governance/deployments/dep-api-11/history"
        )

        assert response.status_code == 200
        assert [entry["state"] for entry in response.json()] == [
            "REGISTERED", "REMOVED",
        ]
