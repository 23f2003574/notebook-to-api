from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.artifact_registry import (
    Artifact,
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactRegistry,
    UnknownArtifactError,
    router as artifact_registry_router,
)

BASE_TIME = datetime(2026, 7, 27, 9, 0, 0, tzinfo=timezone.utc)


def _metadata(**overrides) -> ArtifactMetadata:
    defaults = dict(
        content_type="application/octet-stream",
        size_bytes=1024,
        checksum="a" * 64,
        checksum_algorithm="sha256",
    )
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)


@pytest.fixture
def registry() -> ArtifactRegistry:
    return ArtifactRegistry()


def test_publish_creates_artifact(registry: ArtifactRegistry):
    artifact = registry.publish(
        "svc-a",
        "1.0.0",
        location="s3://artifacts/svc-a-1.0.0.tar.gz",
        metadata=_metadata(),
        tags=("stable",),
        timestamp=BASE_TIME,
    )

    assert isinstance(artifact, Artifact)
    assert artifact.name == "svc-a"
    assert artifact.version == "1.0.0"
    assert artifact.tags == ("stable",)


def test_publish_requires_name(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish("", "1.0.0", location="loc", metadata=_metadata())


def test_publish_requires_version(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish("svc-a", "", location="loc", metadata=_metadata())


def test_publish_requires_location(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish("svc-a", "1.0.0", location="", metadata=_metadata())


def test_publish_duplicate_name_version_raises(registry: ArtifactRegistry):
    registry.publish("svc-a", "1.0.0", location="loc-1", metadata=_metadata(), timestamp=BASE_TIME)

    with pytest.raises(ArtifactAlreadyExistsError):
        registry.publish("svc-a", "1.0.0", location="loc-2", metadata=_metadata(), timestamp=BASE_TIME)


def test_publish_rejects_missing_content_type(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish(
            "svc-a", "1.0.0", location="loc", metadata=_metadata(content_type="")
        )


def test_publish_rejects_negative_size(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish(
            "svc-a", "1.0.0", location="loc", metadata=_metadata(size_bytes=-1)
        )


def test_publish_rejects_unsupported_checksum_algorithm(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish(
            "svc-a",
            "1.0.0",
            location="loc",
            metadata=_metadata(checksum_algorithm="crc32"),
        )


def test_publish_rejects_wrong_checksum_length(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish(
            "svc-a", "1.0.0", location="loc", metadata=_metadata(checksum="abc123")
        )


def test_publish_rejects_non_hex_checksum(registry: ArtifactRegistry):
    with pytest.raises(ValueError):
        registry.publish(
            "svc-a", "1.0.0", location="loc", metadata=_metadata(checksum="z" * 64)
        )


def test_get_returns_published_artifact(registry: ArtifactRegistry):
    artifact = registry.publish(
        "svc-a", "1.0.0", location="loc", metadata=_metadata(), timestamp=BASE_TIME
    )

    assert registry.get(artifact.artifact_id) == artifact


def test_get_unknown_raises(registry: ArtifactRegistry):
    with pytest.raises(UnknownArtifactError):
        registry.get("does-not-exist")


def test_remove_deletes_artifact(registry: ArtifactRegistry):
    artifact = registry.publish(
        "svc-a", "1.0.0", location="loc", metadata=_metadata(), timestamp=BASE_TIME
    )

    registry.remove(artifact.artifact_id)

    with pytest.raises(UnknownArtifactError):
        registry.get(artifact.artifact_id)


def test_remove_unknown_raises(registry: ArtifactRegistry):
    with pytest.raises(UnknownArtifactError):
        registry.remove("does-not-exist")


def test_remove_frees_name_version_for_republish(registry: ArtifactRegistry):
    artifact = registry.publish(
        "svc-a", "1.0.0", location="loc", metadata=_metadata(), timestamp=BASE_TIME
    )
    registry.remove(artifact.artifact_id)

    republished = registry.publish(
        "svc-a", "1.0.0", location="loc-2", metadata=_metadata(), timestamp=BASE_TIME
    )

    assert republished.location == "loc-2"


def test_search_by_name(registry: ArtifactRegistry):
    registry.publish("svc-a", "1.0.0", location="loc-a", metadata=_metadata(), timestamp=BASE_TIME)
    registry.publish("svc-b", "1.0.0", location="loc-b", metadata=_metadata(), timestamp=BASE_TIME)

    results = registry.search(name="svc-a")

    assert len(results) == 1
    assert results[0].name == "svc-a"


def test_search_by_tag(registry: ArtifactRegistry):
    registry.publish(
        "svc-a", "1.0.0", location="loc-a", metadata=_metadata(), tags=("canary",), timestamp=BASE_TIME
    )
    registry.publish(
        "svc-a", "2.0.0", location="loc-a2", metadata=_metadata(), tags=("stable",), timestamp=BASE_TIME
    )

    results = registry.search(tag="canary")

    assert len(results) == 1
    assert results[0].version == "1.0.0"


def test_search_with_no_filters_returns_all(registry: ArtifactRegistry):
    registry.publish("svc-a", "1.0.0", location="loc-a", metadata=_metadata(), timestamp=BASE_TIME)
    registry.publish("svc-b", "1.0.0", location="loc-b", metadata=_metadata(), timestamp=BASE_TIME)

    assert len(registry.search()) == 2


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    return TestClient(app)


def _publish_payload(**overrides) -> dict:
    payload = {
        "name": "svc-a",
        "version": "1.0.0",
        "location": "s3://artifacts/svc-a-1.0.0.tar.gz",
        "metadata": {
            "content_type": "application/octet-stream",
            "size_bytes": 1024,
            "checksum": "a" * 64,
            "checksum_algorithm": "sha256",
        },
        "tags": ["stable"],
    }
    payload.update(overrides)
    return payload


def test_api_publish_and_get(client: TestClient):
    publish_response = client.post("/governance/artifacts", json=_publish_payload())
    artifact_id = publish_response.json()["artifact_id"]

    get_response = client.get(f"/governance/artifacts/{artifact_id}")

    assert publish_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "svc-a"


def test_api_publish_requires_fields(client: TestClient):
    response = client.post("/governance/artifacts", json={})

    assert response.status_code == 422


def test_api_publish_duplicate_returns_409(client: TestClient):
    client.post("/governance/artifacts", json=_publish_payload())
    response = client.post("/governance/artifacts", json=_publish_payload())

    assert response.status_code == 409


def test_api_publish_invalid_metadata_returns_422(client: TestClient):
    response = client.post(
        "/governance/artifacts", json=_publish_payload(metadata={"checksum": "bad"})
    )

    assert response.status_code == 422


def test_api_search_filters_by_name(client: TestClient):
    client.post("/governance/artifacts", json=_publish_payload())
    client.post("/governance/artifacts", json=_publish_payload(name="svc-b"))

    response = client.get("/governance/artifacts", params={"name": "svc-b"})

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "svc-b"


def test_api_get_unknown_returns_404(client: TestClient):
    response = client.get("/governance/artifacts/does-not-exist")

    assert response.status_code == 404
