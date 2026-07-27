from datetime import datetime, timedelta, timezone

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
    router as release_manager_router,
)
from backend.governance.release_verification import (
    ReleaseVerificationEngine,
    router as release_verification_router,
)
from backend.governance.release_analytics import (
    ReleaseAnalyticsService,
    router as release_analytics_router,
)
from backend.governance.release_dashboard import (
    ReleaseDashboardAPI,
    router as release_dashboard_router,
)

BASE_TIME = datetime(2026, 7, 27, 20, 0, 0, tzinfo=timezone.utc)


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
def verification_engine(
    release_manager: ReleaseManager, registry: ArtifactRegistry
) -> ReleaseVerificationEngine:
    return ReleaseVerificationEngine(release_manager=release_manager, registry=registry)


@pytest.fixture
def analytics_service(release_manager: ReleaseManager) -> ReleaseAnalyticsService:
    return ReleaseAnalyticsService(release_manager=release_manager)


@pytest.fixture
def dashboard(
    release_manager: ReleaseManager,
    registry: ArtifactRegistry,
    verification_engine: ReleaseVerificationEngine,
    analytics_service: ReleaseAnalyticsService,
) -> ReleaseDashboardAPI:
    return ReleaseDashboardAPI(
        release_manager=release_manager,
        registry=registry,
        verification_engine=verification_engine,
        analytics_service=analytics_service,
    )


def _make_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    name: str,
    version: str,
    release_name: str,
    created_at: datetime = BASE_TIME,
) -> str:
    registry.publish(
        name, version, location=f"loc-{name}-{version}", metadata=_metadata(), timestamp=created_at
    )
    version_manager.create(name, version, timestamp=created_at)
    promotion_engine.promote(name, version, "Staging", timestamp=created_at)
    promotion_engine.promote(name, version, "Production", timestamp=created_at)
    release = release_manager.create(
        release_name, [{"name": name, "version": version}], timestamp=created_at
    )
    return release.release_id


def test_release_status_reports_counts_and_latest(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    dashboard: ReleaseDashboardAPI,
):
    _make_release(registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-a")
    second_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-b", "1.0.0", "release-b"
    )
    release_manager.fail(second_id, reason="oops", timestamp=BASE_TIME)

    status = dashboard.release_status()

    assert status["total"] == 2
    assert status["by_state"]["DRAFT"] == 1
    assert status["by_state"]["FAILED"] == 1
    assert status["latest"]["release_id"] == second_id


def test_release_status_empty(dashboard: ReleaseDashboardAPI):
    status = dashboard.release_status()

    assert status["total"] == 0
    assert status["latest"] is None


def test_artifact_status_reports_archived_and_active(
    registry: ArtifactRegistry, dashboard: ReleaseDashboardAPI
):
    kept = registry.publish(
        "svc-a", "1.0.0", location="loc-1", metadata=_metadata(), timestamp=BASE_TIME
    )
    archived = registry.publish(
        "svc-a", "2.0.0", location="loc-2", metadata=_metadata(), timestamp=BASE_TIME
    )
    registry.archive(archived.artifact_id, timestamp=BASE_TIME)

    status = dashboard.artifact_status()

    assert status["total"] == 2
    assert status["archived"] == 1
    assert status["active"] == 1
    assert status["distinct_names"] == 1


def test_verification_summary_counts_pass_fail_and_unverified(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    verification_engine: ReleaseVerificationEngine,
    dashboard: ReleaseDashboardAPI,
):
    verified_release = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-a"
    )
    verification_engine.verify(verified_release, timestamp=BASE_TIME)
    _make_release(registry, version_manager, promotion_engine, release_manager, "svc-b", "1.0.0", "release-b")

    summary = dashboard.verification_summary()

    assert summary["total_releases"] == 2
    assert summary["verified"] == 1
    assert summary["failed"] == 1
    assert summary["not_verified"] == 1


def test_analytics_delegates_to_analytics_service(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    analytics_service: ReleaseAnalyticsService,
    dashboard: ReleaseDashboardAPI,
):
    release_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-a"
    )
    analytics_service.record_release(release_id)

    analytics = dashboard.analytics()

    assert analytics["releases_created"] == 1


def test_overview_combines_all_sections(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    analytics_service: ReleaseAnalyticsService,
    dashboard: ReleaseDashboardAPI,
):
    release_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-a"
    )
    analytics_service.record_release(release_id)

    overview = dashboard.overview()

    assert set(overview.keys()) == {
        "releases",
        "artifacts",
        "verifications",
        "analytics",
        "recent_activity",
    }
    assert overview["releases"]["total"] == 1
    assert len(overview["recent_activity"]) == 1


def test_overview_recent_activity_respects_limit_and_order(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    analytics_service: ReleaseAnalyticsService,
    dashboard: ReleaseDashboardAPI,
):
    older_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-a", BASE_TIME
    )
    newer_id = _make_release(
        registry,
        version_manager,
        promotion_engine,
        release_manager,
        "svc-b",
        "1.0.0",
        "release-b",
        BASE_TIME + timedelta(hours=1),
    )
    analytics_service.record_release(older_id)
    analytics_service.record_release(newer_id)

    overview = dashboard.overview()

    assert overview["recent_activity"][0]["release_id"] == newer_id


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    app.include_router(release_manager_router)
    app.include_router(release_verification_router)
    app.include_router(release_analytics_router)
    app.include_router(release_dashboard_router)
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


def test_api_dashboard_overview(client: TestClient):
    _publish_promote_release_via_api(client, "svc-dash-a", "1.0.0", "release-dash-api-1")

    response = client.get("/governance/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "releases",
        "artifacts",
        "verifications",
        "analytics",
        "recent_activity",
    }


def test_api_dashboard_releases(client: TestClient):
    _publish_promote_release_via_api(client, "svc-dash-b", "1.0.0", "release-dash-api-2")

    response = client.get("/governance/dashboard/releases")

    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_api_dashboard_artifacts(client: TestClient):
    _publish_promote_release_via_api(client, "svc-dash-c", "1.0.0", "release-dash-api-3")

    response = client.get("/governance/dashboard/artifacts")

    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_api_dashboard_verification(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-dash-d", "1.0.0", "release-dash-api-4")
    client.post(f"/governance/releases/{release_id}/verify", json={})

    response = client.get("/governance/dashboard/verification")

    assert response.status_code == 200
    assert response.json()["verified"] >= 1


def test_api_dashboard_analytics(client: TestClient):
    response = client.get("/governance/dashboard/analytics")

    assert response.status_code == 200
    assert "releases_created" in response.json()
