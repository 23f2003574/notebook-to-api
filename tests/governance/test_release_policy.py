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
from backend.governance.release_policy import (
    NoEvaluationError,
    PolicyAlreadyExistsError,
    PolicyResult,
    ReleasePolicy,
    ReleasePolicyEngine,
    UnknownPolicyError,
    router as release_policy_router,
)

BASE_TIME = datetime(2026, 7, 27, 15, 0, 0, tzinfo=timezone.utc)


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
def engine(
    release_manager: ReleaseManager, promotion_engine: ArtifactPromotionEngine
) -> ReleasePolicyEngine:
    return ReleasePolicyEngine(release_manager=release_manager, promotion_engine=promotion_engine)


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


def test_builtin_policies_are_registered(engine: ReleasePolicyEngine):
    with pytest.raises(PolicyAlreadyExistsError):
        engine.register_policy("Approval Required", lambda release: True)


def test_register_custom_policy(engine: ReleasePolicyEngine):
    policy = engine.register_policy(
        "Min One Artifact", lambda release: len(release.artifacts) > 0, timestamp=BASE_TIME
    )

    assert isinstance(policy, ReleasePolicy)
    assert policy.name == "Min One Artifact"


def test_register_requires_name(engine: ReleasePolicyEngine):
    with pytest.raises(ValueError):
        engine.register_policy("", lambda release: True)


def test_register_requires_callable(engine: ReleasePolicyEngine):
    with pytest.raises(ValueError):
        engine.register_policy("Bad Policy", "not-callable")


def test_register_duplicate_name_raises(engine: ReleasePolicyEngine):
    engine.register_policy("Custom", lambda release: True)

    with pytest.raises(PolicyAlreadyExistsError):
        engine.register_policy("Custom", lambda release: False)


def test_remove_policy(engine: ReleasePolicyEngine):
    engine.register_policy("Custom", lambda release: True)

    engine.remove_policy("Custom")

    with pytest.raises(UnknownPolicyError):
        engine.remove_policy("Custom")


def test_remove_unknown_raises(engine: ReleasePolicyEngine):
    with pytest.raises(UnknownPolicyError):
        engine.remove_policy("does-not-exist")


def test_evaluate_fails_without_notes_channel_or_approval(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleasePolicyEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    results = engine.evaluate(release_id)

    by_name = {result.name: result for result in results}
    assert all(isinstance(result, PolicyResult) for result in results)
    assert by_name["Approval Required"].passed is False
    assert by_name["Release Notes Present"].passed is False
    assert by_name["Channel Assigned"].passed is False
    assert by_name["Artifact Verified"].passed is True


def test_evaluate_fails_artifact_verified_when_demoted_after_release_creation(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleasePolicyEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    promotion_engine.rollback("svc-a", "1.0.0", timestamp=BASE_TIME)

    results = engine.evaluate(release_id)

    by_name = {result.name: result for result in results}
    assert by_name["Artifact Verified"].passed is False


def test_evaluate_requires_known_release(engine: ReleasePolicyEngine):
    with pytest.raises(UnknownReleaseError):
        engine.evaluate("does-not-exist")


def test_evaluate_with_overrides_passes_all(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleasePolicyEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    results = engine.evaluate(
        release_id,
        overrides={
            "Approval Required": True,
            "Release Notes Present": True,
            "Channel Assigned": True,
        },
    )

    assert all(result.passed for result in results)


def test_evaluate_records_policy_result_on_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleasePolicyEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    engine.evaluate(release_id)
    assert release_manager.get(release_id).policy_passed is False

    engine.evaluate(
        release_id,
        overrides={
            "Approval Required": True,
            "Release Notes Present": True,
            "Channel Assigned": True,
        },
    )
    assert release_manager.get(release_id).policy_passed is True


def test_summary_reflects_last_evaluation(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    engine: ReleasePolicyEngine,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    engine.evaluate(release_id)

    summary = engine.summary(release_id)

    assert summary["release_id"] == release_id
    assert summary["passed"] is False
    assert len(summary["results"]) == 4


def test_summary_without_evaluation_raises(engine: ReleasePolicyEngine):
    with pytest.raises(NoEvaluationError):
        engine.summary("does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    app.include_router(release_manager_router)
    app.include_router(release_policy_router)
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


def test_api_register_custom_policy(client: TestClient):
    response = client.post(
        "/governance/release-policies", json={"name": "Custom Gate API", "description": "manual"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Custom Gate API"


def test_api_register_missing_name_returns_422(client: TestClient):
    response = client.post("/governance/release-policies", json={})

    assert response.status_code == 422


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/governance/release-policies", json={"name": "Dup Gate API"})
    response = client.post("/governance/release-policies", json={"name": "Dup Gate API"})

    assert response.status_code == 409


def test_api_validate_release_fails_by_default(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-pol-a", "1.0.0", "release-pol-api-1")

    response = client.post(f"/governance/releases/{release_id}/validate", json={})

    assert response.status_code == 200
    assert response.json()["passed"] is False


def test_api_validate_release_with_overrides_passes(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-pol-b", "1.0.0", "release-pol-api-2")

    response = client.post(
        f"/governance/releases/{release_id}/validate",
        json={
            "overrides": {
                "Approval Required": True,
                "Release Notes Present": True,
                "Channel Assigned": True,
            }
        },
    )

    # Overridden built-ins and the artifact-verified check must all pass; the
    # aggregate is not asserted here since other tests may have registered
    # additional always-failing custom policies against the shared engine.
    results = {result["name"]: result["passed"] for result in response.json()["results"]}
    assert response.status_code == 200
    assert results["Approval Required"] is True
    assert results["Release Notes Present"] is True
    assert results["Channel Assigned"] is True
    assert results["Artifact Verified"] is True


def test_api_validate_unknown_release_returns_404(client: TestClient):
    response = client.post("/governance/releases/does-not-exist/validate", json={})

    assert response.status_code == 404


def test_api_get_policy_summary(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-pol-c", "1.0.0", "release-pol-api-3")
    client.post(f"/governance/releases/{release_id}/validate", json={})

    response = client.get(f"/governance/releases/{release_id}/policy")

    assert response.status_code == 200
    assert response.json()["release_id"] == release_id


def test_api_get_policy_summary_before_validation_returns_404(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-pol-d", "1.0.0", "release-pol-api-4")

    response = client.get(f"/governance/releases/{release_id}/policy")

    assert response.status_code == 404
