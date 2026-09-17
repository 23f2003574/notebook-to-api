from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.artifact_registry import (
    ArtifactMetadata,
    ArtifactRegistry,
    router as artifact_registry_router,
)
from backend.governance.artifact_versioning import (
    ArtifactVersionManager,
    router as artifact_versioning_router,
)
from backend.governance.artifact_promotion import (
    ArtifactPromotionEngine,
    router as artifact_promotion_router,
)
from backend.governance.release_manager import (
    ReleaseManager,
    UnknownReleaseError,
    router as release_manager_router,
)
from backend.governance.release_channels import (
    ChannelAlreadyExistsError,
    ChannelAssignment,
    ChannelPolicyError,
    NoAssignmentError,
    NoDefaultChannelError,
    ReleaseChannel,
    ReleaseChannelManager,
    UnknownChannelError,
    router as release_channels_router,
)

BASE_TIME = datetime(2026, 7, 27, 14, 0, 0, tzinfo=timezone.utc)


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
def version_manager(registry: ArtifactRegistry) -> ArtifactVersionManager:
    return ArtifactVersionManager(registry=registry)


@pytest.fixture
def promotion_engine(version_manager: ArtifactVersionManager) -> ArtifactPromotionEngine:
    return ArtifactPromotionEngine(version_manager=version_manager)


@pytest.fixture
def release_manager(promotion_engine: ArtifactPromotionEngine) -> ReleaseManager:
    return ReleaseManager(promotion_engine=promotion_engine)


@pytest.fixture
def channel_manager(release_manager: ReleaseManager) -> ReleaseChannelManager:
    return ReleaseChannelManager(release_manager=release_manager)


def _make_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    name: str = "svc-a",
    version: str = "1.0.0",
) -> str:
    registry.publish(
        name, version, location=f"loc-{name}-{version}", metadata=_metadata(), timestamp=BASE_TIME
    )
    version_manager.create(name, version, timestamp=BASE_TIME)
    promotion_engine.promote(name, version, "Staging", timestamp=BASE_TIME)
    promotion_engine.promote(name, version, "Production", timestamp=BASE_TIME)
    release = release_manager.create(
        "release-1", [{"name": name, "version": version}], timestamp=BASE_TIME
    )
    return release.release_id


def test_create_channel(channel_manager: ReleaseChannelManager):
    channel = channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)

    assert isinstance(channel, ReleaseChannel)
    assert channel.kind == "Alpha"
    assert channel.is_default is False


def test_create_channel_requires_name(channel_manager: ReleaseChannelManager):
    with pytest.raises(ValueError):
        channel_manager.create_channel("", "Alpha")


def test_create_channel_rejects_unknown_kind(channel_manager: ReleaseChannelManager):
    with pytest.raises(ValueError):
        channel_manager.create_channel("nightly", "Canary")


def test_create_channel_duplicate_name_raises(channel_manager: ReleaseChannelManager):
    channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)

    with pytest.raises(ChannelAlreadyExistsError):
        channel_manager.create_channel("nightly", "Beta", timestamp=BASE_TIME)


def test_create_channel_default_supersedes_previous_default(channel_manager: ReleaseChannelManager):
    first = channel_manager.create_channel("nightly", "Alpha", is_default=True, timestamp=BASE_TIME)
    channel_manager.create_channel("edge", "Alpha", is_default=True, timestamp=BASE_TIME)

    channels = {channel.name: channel for channel in channel_manager.list()}

    assert channels["nightly"].is_default is False
    assert channels["edge"].is_default is True
    assert first.is_default is True  # original snapshot is immutable


def test_assign_to_named_channel(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)

    assignment = channel_manager.assign(release_id, "nightly", timestamp=BASE_TIME)

    assert isinstance(assignment, ChannelAssignment)
    assert assignment.kind == "Alpha"
    assert release_manager.get(release_id).channel_id == assignment.channel_id


def test_assign_uses_default_channel_when_none_given(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    channel_manager.create_channel("nightly", "Alpha", is_default=True, timestamp=BASE_TIME)

    assignment = channel_manager.assign(release_id, timestamp=BASE_TIME)

    assert assignment.kind == "Alpha"


def test_assign_without_default_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    with pytest.raises(NoDefaultChannelError):
        channel_manager.assign(release_id)


def test_assign_unknown_channel_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    with pytest.raises(UnknownChannelError):
        channel_manager.assign(release_id, "does-not-exist")


def test_assign_unknown_release_raises(channel_manager: ReleaseChannelManager):
    channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)

    with pytest.raises(UnknownReleaseError):
        channel_manager.assign("does-not-exist", "nightly")


def test_promote_advances_to_next_tier_channel(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)
    channel_manager.create_channel("preview", "Beta", timestamp=BASE_TIME)
    channel_manager.assign(release_id, "nightly", timestamp=BASE_TIME)

    assignment = channel_manager.promote(release_id, timestamp=BASE_TIME)

    assert assignment.kind == "Beta"


def test_promote_without_assignment_raises(channel_manager: ReleaseChannelManager):
    with pytest.raises(NoAssignmentError):
        channel_manager.promote("does-not-exist")


def test_promote_with_no_channel_at_next_tier_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)
    channel_manager.assign(release_id, "nightly", timestamp=BASE_TIME)

    with pytest.raises(UnknownChannelError):
        channel_manager.promote(release_id)


def test_promote_rejects_skipping_a_tier(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)
    channel_manager.create_channel("ga", "Stable", timestamp=BASE_TIME)
    channel_manager.assign(release_id, "nightly", timestamp=BASE_TIME)

    with pytest.raises(ChannelPolicyError):
        channel_manager.promote(release_id, target_channel="ga")


def test_promote_past_lts_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    channel_manager.create_channel("longterm", "LTS", timestamp=BASE_TIME)
    channel_manager.assign(release_id, "longterm", timestamp=BASE_TIME)

    with pytest.raises(ChannelPolicyError):
        channel_manager.promote(release_id)


def test_list_returns_all_channels_in_creation_order(channel_manager: ReleaseChannelManager):
    channel_manager.create_channel("nightly", "Alpha", timestamp=BASE_TIME)
    channel_manager.create_channel("preview", "Beta", timestamp=BASE_TIME)

    channels = channel_manager.list()

    assert [channel.name for channel in channels] == ["nightly", "preview"]


def test_list_empty_initially(channel_manager: ReleaseChannelManager):
    assert channel_manager.list() == []


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    app.include_router(release_manager_router)
    app.include_router(release_channels_router)
    return TestClient(app)


def _publish_promote_release_via_api(client: TestClient, name: str, version: str, release_name: str) -> str:
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
    client.post(f"/governance/artifacts/{name}/versions", json={"version": version})
    client.post(
        f"/governance/artifacts/{name}/promote",
        json={"version": version, "target_environment": "Staging"},
    )
    client.post(
        f"/governance/artifacts/{name}/promote",
        json={"version": version, "target_environment": "Production"},
    )
    create_response = client.post(
        "/governance/releases",
        json={"name": release_name, "artifacts": [{"name": name, "version": version}]},
    )
    return create_response.json()["release_id"]


def test_api_create_and_list_channels(client: TestClient):
    create_response = client.post(
        "/governance/release-channels", json={"name": "nightly-api", "kind": "Alpha"}
    )
    list_response = client.get("/governance/release-channels")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert any(channel["name"] == "nightly-api" for channel in list_response.json())


def test_api_create_channel_missing_fields_returns_422(client: TestClient):
    response = client.post("/governance/release-channels", json={})

    assert response.status_code == 422


def test_api_create_channel_duplicate_returns_409(client: TestClient):
    client.post("/governance/release-channels", json={"name": "dup-api", "kind": "Alpha"})
    response = client.post("/governance/release-channels", json={"name": "dup-api", "kind": "Beta"})

    assert response.status_code == 409


def test_api_assign_release_to_channel(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-chan-a", "1.0.0", "release-chan-api-1")
    client.post("/governance/release-channels", json={"name": "nightly-assign", "kind": "Alpha"})

    response = client.post(
        f"/governance/releases/{release_id}/channel", json={"channel": "nightly-assign"}
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "Alpha"


def test_api_promote_release_channel(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-chan-b", "1.0.0", "release-chan-api-2")
    client.post("/governance/release-channels", json={"name": "nightly-promote", "kind": "Alpha"})
    client.post("/governance/release-channels", json={"name": "preview-promote", "kind": "Beta"})
    client.post(
        f"/governance/releases/{release_id}/channel", json={"channel": "nightly-promote"}
    )

    response = client.post(
        f"/governance/releases/{release_id}/channel", json={"promote": True}
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "Beta"


def test_api_assign_unknown_release_returns_404(client: TestClient):
    client.post("/governance/release-channels", json={"name": "nightly-404", "kind": "Alpha"})

    response = client.post(
        "/governance/releases/does-not-exist/channel", json={"channel": "nightly-404"}
    )

    assert response.status_code == 404


def test_api_assign_unknown_channel_returns_404(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-chan-c", "1.0.0", "release-chan-api-3")

    response = client.post(
        f"/governance/releases/{release_id}/channel", json={"channel": "does-not-exist"}
    )

    assert response.status_code == 404
