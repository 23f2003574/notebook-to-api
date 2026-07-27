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
    UnknownReleaseError,
    router as release_manager_router,
)
from backend.governance.release_analytics import (
    ReleaseAnalyticsService,
    ReleaseMetrics,
    ReleaseTrend,
    get_release_analytics_service,
    router as release_analytics_router,
)

BASE_TIME = datetime(2026, 7, 27, 19, 0, 0, tzinfo=timezone.utc)


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
def service(release_manager: ReleaseManager) -> ReleaseAnalyticsService:
    return ReleaseAnalyticsService(release_manager=release_manager)


def _make_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    name: str,
    version: str,
    release_name: str,
    created_at: datetime,
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


def test_record_release_captures_snapshot(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    service: ReleaseAnalyticsService,
):
    release_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-1", BASE_TIME
    )

    service.record_release(release_id)

    releases = service.releases()
    assert len(releases) == 1
    assert releases[0].release_id == release_id


def test_record_release_requires_known_release(service: ReleaseAnalyticsService):
    with pytest.raises(UnknownReleaseError):
        service.record_release("does-not-exist")


def test_record_release_overwrites_with_latest_state(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    service: ReleaseAnalyticsService,
):
    release_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-1", BASE_TIME
    )
    service.record_release(release_id)
    release_manager.mark_policy_result(release_id, True)
    release_manager.publish(release_id, timestamp=BASE_TIME + timedelta(hours=2))
    service.record_release(release_id)

    assert service.releases()[0].state == "PUBLISHED"


def test_record_verification_requires_known_release(service: ReleaseAnalyticsService):
    with pytest.raises(UnknownReleaseError):
        service.record_verification("does-not-exist", True)


def test_summary_counts_publications_and_failures(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    service: ReleaseAnalyticsService,
):
    published_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-pub", BASE_TIME
    )
    release_manager.mark_policy_result(published_id, True)
    release_manager.publish(published_id, timestamp=BASE_TIME + timedelta(hours=1))
    service.record_release(published_id)

    failed_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-b", "1.0.0", "release-fail", BASE_TIME
    )
    release_manager.fail(failed_id, reason="policy violation", timestamp=BASE_TIME)
    service.record_release(failed_id)

    summary = service.summary()

    assert isinstance(summary, ReleaseMetrics)
    assert summary.releases_created == 2
    assert summary.successful_publications == 1
    assert summary.failed_releases == 1
    assert summary.average_release_duration_seconds == timedelta(hours=1).total_seconds()


def test_summary_computes_verification_pass_rate(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    service: ReleaseAnalyticsService,
):
    release_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-1", BASE_TIME
    )
    service.record_verification(release_id, True, timestamp=BASE_TIME)
    service.record_verification(release_id, False, timestamp=BASE_TIME)
    service.record_verification(release_id, True, timestamp=BASE_TIME)

    summary = service.summary()

    assert summary.verification_pass_rate == pytest.approx(2 / 3)


def test_summary_pass_rate_none_when_no_verifications(service: ReleaseAnalyticsService):
    assert service.summary().verification_pass_rate is None


def test_summary_respects_time_window(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    service: ReleaseAnalyticsService,
):
    old_id = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-old", BASE_TIME
    )
    service.record_release(old_id)
    new_id = _make_release(
        registry,
        version_manager,
        promotion_engine,
        release_manager,
        "svc-b",
        "1.0.0",
        "release-new",
        BASE_TIME + timedelta(days=10),
    )
    service.record_release(new_id)

    summary = service.summary(window_start=BASE_TIME + timedelta(days=5))

    assert summary.releases_created == 1


def test_trends_empty_when_no_releases(service: ReleaseAnalyticsService):
    assert service.trends() == []


def test_trends_buckets_by_period(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    service: ReleaseAnalyticsService,
):
    day1_a = _make_release(
        registry, version_manager, promotion_engine, release_manager, "svc-a", "1.0.0", "release-a", BASE_TIME
    )
    day1_b = _make_release(
        registry,
        version_manager,
        promotion_engine,
        release_manager,
        "svc-b",
        "1.0.0",
        "release-b",
        BASE_TIME + timedelta(hours=2),
    )
    day2 = _make_release(
        registry,
        version_manager,
        promotion_engine,
        release_manager,
        "svc-c",
        "1.0.0",
        "release-c",
        BASE_TIME + timedelta(days=1, hours=1),
    )
    for release_id in (day1_a, day1_b, day2):
        service.record_release(release_id)

    trends = service.trends(bucket_seconds=timedelta(days=1).total_seconds())

    assert len(trends) == 2
    assert all(isinstance(trend, ReleaseTrend) for trend in trends)
    assert trends[0].releases_created == 2
    assert trends[1].releases_created == 1


def test_trends_rejects_non_positive_bucket(service: ReleaseAnalyticsService):
    with pytest.raises(ValueError):
        service.trends(bucket_seconds=0)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    app.include_router(release_manager_router)
    app.include_router(release_analytics_router)
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


def test_api_list_and_summary(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-an-a", "1.0.0", "release-an-api-1")
    get_release_analytics_service().record_release(release_id)

    list_response = client.get("/governance/analytics/releases")
    summary_response = client.get("/governance/analytics/releases/summary")

    assert list_response.status_code == 200
    assert any(release["release_id"] == release_id for release in list_response.json())
    assert summary_response.status_code == 200
    assert summary_response.json()["releases_created"] >= 1


def test_api_summary_with_invalid_window_returns_422(client: TestClient):
    response = client.get(
        "/governance/analytics/releases/summary", params={"window_start": "not-a-date"}
    )

    assert response.status_code == 422


def test_api_trends_rejects_non_positive_bucket(client: TestClient):
    response = client.get(
        "/governance/analytics/releases/trends", params={"bucket_seconds": 0}
    )

    assert response.status_code == 422


def test_api_trends_returns_list(client: TestClient):
    response = client.get("/governance/analytics/releases/trends")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
