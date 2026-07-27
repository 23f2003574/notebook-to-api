from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.artifact_registry import (
    Artifact,
    ArtifactMetadata,
    ArtifactRegistry,
    UnknownArtifactError,
    router as artifact_registry_router,
)
from backend.governance.artifact_replication import (
    ArtifactReplicationService,
    ReplicationRecord,
    UnknownReplicaError,
    router as artifact_replication_router,
)

BASE_TIME = datetime(2026, 7, 27, 17, 0, 0, tzinfo=timezone.utc)


def _metadata(checksum: str = "a" * 64) -> ArtifactMetadata:
    return ArtifactMetadata(
        content_type="application/octet-stream",
        size_bytes=1024,
        checksum=checksum,
        checksum_algorithm="sha256",
    )


@pytest.fixture
def registry() -> ArtifactRegistry:
    return ArtifactRegistry()


@pytest.fixture
def artifact(registry: ArtifactRegistry) -> Artifact:
    return registry.publish(
        "svc-a", "1.0.0", location="loc-svc-a-1.0.0", metadata=_metadata(), timestamp=BASE_TIME
    )


def _flaky_transport(failures: int, checksum: str):
    calls = {"count": 0}

    def transport(artifact, target):
        calls["count"] += 1
        if calls["count"] <= failures:
            raise ConnectionError("transient network error")
        return checksum

    transport.calls = calls
    return transport


def test_replicate_succeeds_with_matching_checksum(
    registry: ArtifactRegistry, artifact: Artifact
):
    service = ArtifactReplicationService(registry=registry)

    record = service.replicate(
        artifact.artifact_id, "us-west", "s3://us-west/bucket", timestamp=BASE_TIME
    )

    assert isinstance(record, ReplicationRecord)
    assert record.status == "SUCCEEDED"
    assert record.checksum_verified is True
    assert record.attempts == 1


def test_replicate_requires_known_artifact(registry: ArtifactRegistry):
    service = ArtifactReplicationService(registry=registry)

    with pytest.raises(UnknownArtifactError):
        service.replicate("does-not-exist", "us-west", "s3://us-west/bucket")


def test_replicate_requires_target_name(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)

    with pytest.raises(ValueError):
        service.replicate(artifact.artifact_id, "", "s3://us-west/bucket")


def test_replicate_requires_endpoint(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)

    with pytest.raises(ValueError):
        service.replicate(artifact.artifact_id, "us-west", "")


def test_replicate_retries_transient_failures(registry: ArtifactRegistry, artifact: Artifact):
    transport = _flaky_transport(failures=2, checksum=artifact.metadata.checksum)
    service = ArtifactReplicationService(registry=registry, transport=transport)

    record = service.replicate(
        artifact.artifact_id, "us-west", "s3://us-west/bucket", max_attempts=3, timestamp=BASE_TIME
    )

    assert record.status == "SUCCEEDED"
    assert record.attempts == 3


def test_replicate_gives_up_after_max_attempts(registry: ArtifactRegistry, artifact: Artifact):
    transport = _flaky_transport(failures=5, checksum=artifact.metadata.checksum)
    service = ArtifactReplicationService(registry=registry, transport=transport)

    record = service.replicate(
        artifact.artifact_id, "us-west", "s3://us-west/bucket", max_attempts=3, timestamp=BASE_TIME
    )

    assert record.status == "FAILED"
    assert record.attempts == 3
    assert record.last_error == "transient network error"


def test_replicate_detects_checksum_mismatch(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(
        registry=registry, transport=lambda artifact, target: "corrupted-checksum"
    )

    record = service.replicate(
        artifact.artifact_id, "us-west", "s3://us-west/bucket", max_attempts=1, timestamp=BASE_TIME
    )

    assert record.status == "FAILED"
    assert record.checksum_verified is False
    assert record.last_error == "checksum mismatch"


def test_sync_retries_a_previously_failed_replica(
    registry: ArtifactRegistry, artifact: Artifact
):
    transport = _flaky_transport(failures=1, checksum=artifact.metadata.checksum)
    service = ArtifactReplicationService(registry=registry, transport=transport)
    service.replicate(
        artifact.artifact_id, "us-west", "s3://us-west/bucket", max_attempts=1, timestamp=BASE_TIME
    )

    record = service.sync(artifact.artifact_id, "us-west", max_attempts=2, timestamp=BASE_TIME)

    assert record.status == "SUCCEEDED"
    assert record.attempts == 2  # 1 from replicate() + 1 from sync()


def test_sync_requires_existing_replica(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)

    with pytest.raises(UnknownReplicaError):
        service.sync(artifact.artifact_id, "us-west")


def test_status_returns_single_replica(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)
    service.replicate(artifact.artifact_id, "us-west", "s3://us-west/bucket", timestamp=BASE_TIME)

    record = service.status(artifact.artifact_id, "us-west")

    assert record.target.name == "us-west"


def test_status_single_replica_unknown_raises(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)

    with pytest.raises(UnknownReplicaError):
        service.status(artifact.artifact_id, "does-not-exist")


def test_status_returns_all_replicas_for_artifact(
    registry: ArtifactRegistry, artifact: Artifact
):
    service = ArtifactReplicationService(registry=registry)
    service.replicate(artifact.artifact_id, "us-west", "s3://us-west/bucket", timestamp=BASE_TIME)
    service.replicate(artifact.artifact_id, "eu-central", "s3://eu-central/bucket", timestamp=BASE_TIME)

    records = service.status(artifact.artifact_id)

    names = {record.target.name for record in records}
    assert names == {"us-west", "eu-central"}


def test_status_empty_when_never_replicated(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)

    assert service.status(artifact.artifact_id) == []


def test_remove_replica(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)
    service.replicate(artifact.artifact_id, "us-west", "s3://us-west/bucket", timestamp=BASE_TIME)

    service.remove_replica(artifact.artifact_id, "us-west")

    with pytest.raises(UnknownReplicaError):
        service.status(artifact.artifact_id, "us-west")


def test_remove_replica_unknown_raises(registry: ArtifactRegistry, artifact: Artifact):
    service = ArtifactReplicationService(registry=registry)

    with pytest.raises(UnknownReplicaError):
        service.remove_replica(artifact.artifact_id, "does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_replication_router)
    app.include_router(artifact_registry_router)
    return TestClient(app)


def _publish_via_api(client: TestClient, name: str, version: str) -> str:
    response = client.post(
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
    return response.json()["artifact_id"]


def test_api_replicate_and_get_status(client: TestClient):
    artifact_id = _publish_via_api(client, "svc-rep-api-a", "1.0.0")

    replicate_response = client.post(
        f"/governance/artifacts/{artifact_id}/replicate",
        json={"target_name": "us-west", "endpoint": "s3://us-west/bucket"},
    )
    status_response = client.get(f"/governance/artifacts/{artifact_id}/replication")

    assert replicate_response.status_code == 200
    assert replicate_response.json()["status"] == "SUCCEEDED"
    assert status_response.status_code == 200
    assert len(status_response.json()) == 1


def test_api_replicate_missing_fields_returns_422(client: TestClient):
    artifact_id = _publish_via_api(client, "svc-rep-api-b", "1.0.0")

    response = client.post(f"/governance/artifacts/{artifact_id}/replicate", json={})

    assert response.status_code == 422


def test_api_replicate_unknown_artifact_returns_404(client: TestClient):
    response = client.post(
        "/governance/artifacts/does-not-exist/replicate",
        json={"target_name": "us-west", "endpoint": "s3://us-west/bucket"},
    )

    assert response.status_code == 404


def test_api_delete_replica(client: TestClient):
    artifact_id = _publish_via_api(client, "svc-rep-api-c", "1.0.0")
    client.post(
        f"/governance/artifacts/{artifact_id}/replicate",
        json={"target_name": "us-west", "endpoint": "s3://us-west/bucket"},
    )

    delete_response = client.delete(f"/governance/artifacts/{artifact_id}/replicas/us-west")
    status_response = client.get(f"/governance/artifacts/{artifact_id}/replication")

    assert delete_response.status_code == 200
    assert status_response.json() == []


def test_api_delete_unknown_replica_returns_404(client: TestClient):
    artifact_id = _publish_via_api(client, "svc-rep-api-d", "1.0.0")

    response = client.delete(f"/governance/artifacts/{artifact_id}/replicas/does-not-exist")

    assert response.status_code == 404
