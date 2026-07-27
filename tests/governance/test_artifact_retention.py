from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.artifact_registry import (
    ArtifactMetadata,
    ArtifactRegistry,
    router as artifact_registry_router,
)
from backend.governance.artifact_retention import (
    ArtifactRetentionManager,
    RetentionPolicy,
    RetentionPolicyAlreadyExistsError,
    RetentionResult,
    UnknownRetentionPolicyError,
    router as artifact_retention_router,
)

BASE_TIME = datetime(2026, 7, 27, 16, 0, 0, tzinfo=timezone.utc)


def _metadata() -> ArtifactMetadata:
    return ArtifactMetadata(
        content_type="application/octet-stream",
        size_bytes=1024,
        checksum="a" * 64,
        checksum_algorithm="sha256",
    )


@pytest.fixture
def registry() -> ArtifactRegistry:
    return ArtifactRegistry()


@pytest.fixture
def manager(registry: ArtifactRegistry) -> ArtifactRetentionManager:
    return ArtifactRetentionManager(registry=registry)


def _publish(registry: ArtifactRegistry, name: str, version: str, created_at: datetime):
    return registry.publish(
        name, version, location=f"loc-{name}-{version}", metadata=_metadata(), timestamp=created_at
    )


def test_register_policy(manager: ArtifactRetentionManager):
    policy = manager.register_policy("svc-a", max_versions=3, timestamp=BASE_TIME)

    assert isinstance(policy, RetentionPolicy)
    assert policy.max_versions == 3


def test_register_requires_name(manager: ArtifactRetentionManager):
    with pytest.raises(ValueError):
        manager.register_policy("", max_versions=3)


def test_register_requires_a_limit(manager: ArtifactRetentionManager):
    with pytest.raises(ValueError):
        manager.register_policy("svc-a")


def test_register_rejects_non_positive_max_age(manager: ArtifactRetentionManager):
    with pytest.raises(ValueError):
        manager.register_policy("svc-a", max_age_seconds=0)


def test_register_rejects_non_positive_max_versions(manager: ArtifactRetentionManager):
    with pytest.raises(ValueError):
        manager.register_policy("svc-a", max_versions=0)


def test_register_duplicate_raises(manager: ArtifactRetentionManager):
    manager.register_policy("svc-a", max_versions=3, timestamp=BASE_TIME)

    with pytest.raises(RetentionPolicyAlreadyExistsError):
        manager.register_policy("svc-a", max_versions=5, timestamp=BASE_TIME)


def test_policies_lists_registered_policies(manager: ArtifactRetentionManager):
    manager.register_policy("svc-a", max_versions=3, timestamp=BASE_TIME)
    manager.register_policy("svc-b", max_versions=5, timestamp=BASE_TIME)

    names = {policy.name for policy in manager.policies()}
    assert names == {"svc-a", "svc-b"}


def test_apply_requires_registered_policy(manager: ArtifactRetentionManager):
    with pytest.raises(UnknownRetentionPolicyError):
        manager.apply("svc-a")


def test_apply_archives_versions_beyond_max_versions(
    registry: ArtifactRegistry, manager: ArtifactRetentionManager
):
    for i in range(5):
        _publish(registry, "svc-a", f"1.{i}.0", BASE_TIME + timedelta(days=i))
    manager.register_policy("svc-a", max_versions=2, timestamp=BASE_TIME)

    result = manager.apply("svc-a", timestamp=BASE_TIME + timedelta(days=10))

    assert isinstance(result, RetentionResult)
    assert len(result.archived) == 3
    assert len(result.retained) == 2
    for artifact_id in result.archived:
        assert registry.get(artifact_id).archived_at is not None


def test_apply_archives_versions_beyond_max_age(
    registry: ArtifactRegistry, manager: ArtifactRetentionManager
):
    old = _publish(registry, "svc-a", "1.0.0", BASE_TIME)
    recent = _publish(registry, "svc-a", "2.0.0", BASE_TIME + timedelta(days=9))
    manager.register_policy("svc-a", max_age_seconds=timedelta(days=7).total_seconds(), timestamp=BASE_TIME)

    result = manager.apply("svc-a", timestamp=BASE_TIME + timedelta(days=10))

    assert old.artifact_id in result.archived
    assert recent.artifact_id in result.retained


def test_apply_does_not_delete_artifacts(
    registry: ArtifactRegistry, manager: ArtifactRetentionManager
):
    published = _publish(registry, "svc-a", "1.0.0", BASE_TIME)
    manager.register_policy("svc-a", max_age_seconds=1, timestamp=BASE_TIME)

    manager.apply("svc-a", timestamp=BASE_TIME + timedelta(days=1))

    assert registry.get(published.artifact_id) is not None


def test_archive_delegates_to_registry(
    registry: ArtifactRegistry, manager: ArtifactRetentionManager
):
    published = _publish(registry, "svc-a", "1.0.0", BASE_TIME)

    manager.archive(published.artifact_id, timestamp=BASE_TIME)

    assert registry.get(published.artifact_id).archived_at == BASE_TIME


def test_cleanup_removes_archived_artifacts(
    registry: ArtifactRegistry, manager: ArtifactRetentionManager
):
    for i in range(5):
        _publish(registry, "svc-a", f"1.{i}.0", BASE_TIME + timedelta(days=i))
    manager.register_policy("svc-a", max_versions=2, timestamp=BASE_TIME)
    manager.apply("svc-a", timestamp=BASE_TIME + timedelta(days=10))

    results = manager.cleanup("svc-a", timestamp=BASE_TIME + timedelta(days=10))

    assert len(results) == 1
    result = results[0]
    assert len(result.deleted) == 3
    assert len(result.retained) == 2
    assert len(registry.search(name="svc-a")) == 2


def test_cleanup_without_name_sweeps_all_policies(
    registry: ArtifactRegistry, manager: ArtifactRetentionManager
):
    for i in range(3):
        _publish(registry, "svc-a", f"1.{i}.0", BASE_TIME + timedelta(days=i))
        _publish(registry, "svc-b", f"1.{i}.0", BASE_TIME + timedelta(days=i))
    manager.register_policy("svc-a", max_versions=1, timestamp=BASE_TIME)
    manager.register_policy("svc-b", max_versions=1, timestamp=BASE_TIME)
    manager.apply("svc-a", timestamp=BASE_TIME + timedelta(days=10))
    manager.apply("svc-b", timestamp=BASE_TIME + timedelta(days=10))

    results = manager.cleanup(timestamp=BASE_TIME + timedelta(days=10))

    names = {result.name for result in results}
    assert names == {"svc-a", "svc-b"}


def test_cleanup_without_archived_artifacts_deletes_nothing(
    registry: ArtifactRegistry, manager: ArtifactRetentionManager
):
    _publish(registry, "svc-a", "1.0.0", BASE_TIME)
    manager.register_policy("svc-a", max_versions=5, timestamp=BASE_TIME)

    results = manager.cleanup("svc-a", timestamp=BASE_TIME)

    assert results[0].deleted == ()
    assert len(registry.search(name="svc-a")) == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    # artifact_retention_router must be registered before artifact_registry_router:
    # its literal "/artifacts/retention" path would otherwise be shadowed by
    # artifact_registry's "/artifacts/{artifact}" wildcard route.
    app.include_router(artifact_retention_router)
    app.include_router(artifact_registry_router)
    return TestClient(app)


def _publish_via_api(client: TestClient, name: str, version: str) -> None:
    client.post(
        "/governance/artifacts",
        json={
            "name": name,
            "version": version,
            "location": f"loc-{name}-{version}",
            "metadata": {
                "content_type": "application/octet-stream",
                "size_bytes": 1024,
                "checksum": "a" * 64,
                "checksum_algorithm": "sha256",
            },
        },
    )


def test_api_register_policy(client: TestClient):
    response = client.post(
        "/governance/artifact-retention/policies",
        json={"name": "svc-ret-api-a", "max_versions": 2},
    )

    assert response.status_code == 200
    assert response.json()["max_versions"] == 2


def test_api_register_missing_name_returns_422(client: TestClient):
    response = client.post("/governance/artifact-retention/policies", json={"max_versions": 2})

    assert response.status_code == 422


def test_api_register_missing_limits_returns_422(client: TestClient):
    response = client.post(
        "/governance/artifact-retention/policies", json={"name": "svc-ret-api-b"}
    )

    assert response.status_code == 422


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post(
        "/governance/artifact-retention/policies",
        json={"name": "svc-ret-api-c", "max_versions": 2},
    )
    response = client.post(
        "/governance/artifact-retention/policies",
        json={"name": "svc-ret-api-c", "max_versions": 3},
    )

    assert response.status_code == 409


def test_api_list_retention_policies(client: TestClient):
    client.post(
        "/governance/artifact-retention/policies",
        json={"name": "svc-ret-api-d", "max_versions": 2},
    )

    response = client.get("/governance/artifacts/retention")

    assert response.status_code == 200
    assert any(policy["name"] == "svc-ret-api-d" for policy in response.json())


def test_api_cleanup_requires_name(client: TestClient):
    response = client.post("/governance/artifacts/cleanup", json={})

    assert response.status_code == 422


def test_api_cleanup_requires_registered_policy(client: TestClient):
    response = client.post(
        "/governance/artifacts/cleanup", json={"name": "svc-ret-api-unregistered"}
    )

    assert response.status_code == 404


def test_api_cleanup_end_to_end(client: TestClient):
    for i in range(3):
        _publish_via_api(client, "svc-ret-api-e", f"1.{i}.0")
    client.post(
        "/governance/artifact-retention/policies",
        json={"name": "svc-ret-api-e", "max_versions": 1},
    )

    response = client.post("/governance/artifacts/cleanup", json={"name": "svc-ret-api-e"})

    assert response.status_code == 200
    result = response.json()[0]
    assert len(result["deleted"]) == 2
    assert len(result["retained"]) == 1
