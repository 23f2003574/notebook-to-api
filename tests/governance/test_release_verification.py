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
from backend.governance.artifact_replication import (
    ArtifactReplicationService,
    router as artifact_replication_router,
)
from backend.governance.release_manager import (
    ReleaseManager,
    UnknownReleaseError,
    router as release_manager_router,
)
from backend.governance.release_notes import router as release_notes_router
from backend.governance.release_channels import (
    ReleaseChannelManager,
    router as release_channels_router,
)
from backend.governance.release_verification import (
    NoVerificationError,
    ReleaseVerificationEngine,
    UnknownProfileError,
    VerificationReport,
    router as release_verification_router,
)

BASE_TIME = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


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
def replication_service(registry: ArtifactRegistry) -> ArtifactReplicationService:
    return ArtifactReplicationService(registry=registry)


@pytest.fixture
def channel_manager(release_manager: ReleaseManager) -> ReleaseChannelManager:
    return ReleaseChannelManager(release_manager=release_manager)


@pytest.fixture
def engine(
    release_manager: ReleaseManager,
    registry: ArtifactRegistry,
    replication_service: ArtifactReplicationService,
) -> ReleaseVerificationEngine:
    return ReleaseVerificationEngine(
        release_manager=release_manager, registry=registry, replication_service=replication_service
    )


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


def test_run_checks_reports_failures_on_bare_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    checks = engine.run_checks(release_id)

    by_name = {check.name: check for check in checks}
    assert by_name["Artifact Exists"].passed is True
    assert by_name["Checksum Valid"].passed is True
    assert by_name["Policy Passed"].passed is False
    assert by_name["Release Metadata Complete"].passed is False


def test_run_checks_quick_profile_runs_subset(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    checks = engine.run_checks(release_id, profile="quick")

    assert {check.name for check in checks} == {"Artifact Exists", "Policy Passed"}


def test_run_checks_unknown_profile_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    with pytest.raises(UnknownProfileError):
        engine.run_checks(release_id, profile="does-not-exist")


def test_run_checks_requires_known_release(engine: ReleaseVerificationEngine):
    with pytest.raises(UnknownReleaseError):
        engine.run_checks("does-not-exist")


def test_checksum_valid_fails_when_replica_unverified(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    replication_service: ArtifactReplicationService,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    artifact = registry.find("svc-a", "1.0.0")
    replication_service._transport = lambda artifact, target: "corrupted"
    replication_service.replicate(
        artifact.artifact_id, "us-west", "s3://us-west/bucket", max_attempts=1
    )

    checks = engine.run_checks(release_id)

    by_name = {check.name: check for check in checks}
    assert by_name["Checksum Valid"].passed is False


def test_verify_produces_report_and_updates_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    report = engine.verify(release_id, timestamp=BASE_TIME)

    assert isinstance(report, VerificationReport)
    assert report.passed is False
    assert len(report.checks) == 4
    assert release_manager.get(release_id).verified is False


def test_verify_passes_once_release_is_complete(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    channel_manager: ReleaseChannelManager,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    release_manager.attach_notes(release_id, "notes-1")
    channel_manager.create_channel("stable-chan", "Stable", timestamp=BASE_TIME)
    channel_manager.assign(release_id, "stable-chan", timestamp=BASE_TIME)
    release_manager.mark_policy_result(release_id, True)

    report = engine.verify(release_id, timestamp=BASE_TIME)

    assert report.passed is True
    assert release_manager.get(release_id).verified is True


def test_result_returns_latest_report(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    engine.verify(release_id, timestamp=BASE_TIME)
    second = engine.verify(release_id, profile="quick", timestamp=BASE_TIME)

    assert engine.result(release_id).report_id == second.report_id


def test_result_without_verification_raises(engine: ReleaseVerificationEngine):
    with pytest.raises(NoVerificationError):
        engine.result("does-not-exist")


def test_history_accumulates_reports(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleaseVerificationEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    engine.verify(release_id, timestamp=BASE_TIME)
    engine.verify(release_id, timestamp=BASE_TIME)

    assert len(engine.history(release_id)) == 2


def test_history_empty_when_never_verified(engine: ReleaseVerificationEngine):
    assert engine.history("does-not-exist") == []


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    app.include_router(artifact_replication_router)
    app.include_router(release_manager_router)
    app.include_router(release_notes_router)
    app.include_router(release_channels_router)
    app.include_router(release_verification_router)
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


def test_api_verify_and_get_result(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-ver-a", "1.0.0", "release-ver-api-1")

    verify_response = client.post(f"/governance/releases/{release_id}/verify", json={})
    get_response = client.get(f"/governance/releases/{release_id}/verification")

    assert verify_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["passed"] is False


def test_api_verify_unknown_release_returns_404(client: TestClient):
    response = client.post("/governance/releases/does-not-exist/verify", json={})

    assert response.status_code == 404


def test_api_verify_unknown_profile_returns_422(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-ver-b", "1.0.0", "release-ver-api-2")

    response = client.post(
        f"/governance/releases/{release_id}/verify", json={"profile": "does-not-exist"}
    )

    assert response.status_code == 422


def test_api_get_result_before_verification_returns_404(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-ver-c", "1.0.0", "release-ver-api-3")

    response = client.get(f"/governance/releases/{release_id}/verification")

    assert response.status_code == 404


def test_api_get_history(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-ver-d", "1.0.0", "release-ver-api-4")
    client.post(f"/governance/releases/{release_id}/verify", json={})
    client.post(f"/governance/releases/{release_id}/verify", json={})

    response = client.get(f"/governance/releases/{release_id}/verification/history")

    assert response.status_code == 200
    assert len(response.json()) == 2
