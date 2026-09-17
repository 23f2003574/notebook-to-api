from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import ArtifactManager, ArtifactType
from backend.storage.lifecycle_policy import (
    LifecyclePolicy,
    LifecyclePolicyManager,
    PolicyExecution,
    PolicyType,
    RetentionRule,
    get_lifecycle_policy_manager,
    router as lifecycle_policy_router,
)
from backend.storage.object_storage import ObjectStorageEngine


def age_artifact(artifact_manager: ArtifactManager, artifact_id: str, seconds: int) -> None:
    artifact = artifact_manager.fetch(artifact_id)
    artifact.created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def artifact_manager(object_storage: ObjectStorageEngine) -> ArtifactManager:
    return ArtifactManager(object_storage=object_storage)


@pytest.fixture
def manager(artifact_manager: ArtifactManager) -> LifecyclePolicyManager:
    return LifecyclePolicyManager(artifact_manager=artifact_manager)


@pytest.fixture
def client(manager: LifecyclePolicyManager) -> TestClient:
    app = FastAPI()
    app.include_router(lifecycle_policy_router)
    app.dependency_overrides[get_lifecycle_policy_manager] = lambda: manager
    return TestClient(app)


def test_create_policy_creates_record(manager: LifecyclePolicyManager):
    policy = manager.create_policy(
        "expire-old-logs",
        PolicyType.EXPIRATION,
        RetentionRule(max_age_seconds=3600, artifact_type=ArtifactType.LOG_BUNDLE),
    )

    assert isinstance(policy, LifecyclePolicy)
    assert policy.policy_type == PolicyType.EXPIRATION
    assert policy.enabled is True


def test_create_policy_rejects_empty_name(manager: LifecyclePolicyManager):
    with pytest.raises(ValueError):
        manager.create_policy("", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=10))


def test_create_policy_rejects_negative_max_age(manager: LifecyclePolicyManager):
    with pytest.raises(ValueError):
        manager.create_policy("bad", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=-1))


def test_evaluate_returns_empty_for_fresh_artifact(manager: LifecyclePolicyManager, artifact_manager: ArtifactManager):
    artifact = artifact_manager.create("a.log", ArtifactType.LOG_BUNDLE, b"data")
    manager.create_policy("expire-logs", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=3600))

    triggered = manager.evaluate(artifact.artifact_id)

    assert triggered == []


def test_evaluate_returns_triggered_policy_for_aged_artifact(
    manager: LifecyclePolicyManager, artifact_manager: ArtifactManager
):
    artifact = artifact_manager.create("a.log", ArtifactType.LOG_BUNDLE, b"data")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    policy = manager.create_policy("expire-logs", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=3600))

    triggered = manager.evaluate(artifact.artifact_id)

    assert [p.policy_id for p in triggered] == [policy.policy_id]


def test_evaluate_respects_namespace_filter(manager: LifecyclePolicyManager, artifact_manager: ArtifactManager):
    artifact = artifact_manager.create("a.log", ArtifactType.LOG_BUNDLE, b"data", namespace="team-a")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    manager.create_policy(
        "expire-team-b-logs",
        PolicyType.EXPIRATION,
        RetentionRule(max_age_seconds=3600, namespace="team-b"),
    )

    assert manager.evaluate(artifact.artifact_id) == []


def test_evaluate_ignores_disabled_policies(manager: LifecyclePolicyManager, artifact_manager: ArtifactManager):
    artifact = artifact_manager.create("a.log", ArtifactType.LOG_BUNDLE, b"data")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    manager.create_policy("expire-logs", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=3600), enabled=False)

    assert manager.evaluate(artifact.artifact_id) == []


def test_evaluate_raises_for_missing_artifact(manager: LifecyclePolicyManager):
    with pytest.raises(KeyError):
        manager.evaluate("missing")


def test_expire_deletes_artifact_and_records_history(
    manager: LifecyclePolicyManager, artifact_manager: ArtifactManager
):
    artifact = artifact_manager.create("a.log", ArtifactType.LOG_BUNDLE, b"data")

    execution = manager.expire(artifact.artifact_id)

    assert isinstance(execution, PolicyExecution)
    assert execution.action == "expired"
    assert artifact_manager.fetch(artifact.artifact_id) is None
    assert manager.list_history() == [execution]


def test_expire_raises_for_missing_artifact(manager: LifecyclePolicyManager):
    with pytest.raises(KeyError):
        manager.expire("missing")


def test_apply_expires_aged_artifacts(manager: LifecyclePolicyManager, artifact_manager: ArtifactManager):
    fresh = artifact_manager.create("fresh.log", ArtifactType.LOG_BUNDLE, b"data")
    stale = artifact_manager.create("stale.log", ArtifactType.LOG_BUNDLE, b"data")
    age_artifact(artifact_manager, stale.artifact_id, seconds=7200)
    manager.create_policy("expire-logs", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=3600))

    executions = manager.apply()

    assert [e.artifact_id for e in executions] == [stale.artifact_id]
    assert artifact_manager.fetch(stale.artifact_id) is None
    assert artifact_manager.fetch(fresh.artifact_id) is not None


def test_apply_archives_aged_artifacts_without_deleting(
    manager: LifecyclePolicyManager, artifact_manager: ArtifactManager
):
    artifact = artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    manager.create_policy(
        "archive-old-models",
        PolicyType.ARCHIVE,
        RetentionRule(max_age_seconds=3600, tier="cold"),
    )

    executions = manager.apply()

    assert executions[0].action == "archive"
    refreshed = artifact_manager.fetch(artifact.artifact_id)
    assert refreshed is not None
    assert refreshed.tier == "cold"


def test_apply_records_retention_matches_without_side_effect(
    manager: LifecyclePolicyManager, artifact_manager: ArtifactManager
):
    artifact = artifact_manager.create("dataset.csv", ArtifactType.DATASET, b"data")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    manager.create_policy("retain-datasets", PolicyType.RETENTION, RetentionRule(max_age_seconds=3600))

    executions = manager.apply()

    assert executions[0].action == "retained"
    assert artifact_manager.fetch(artifact.artifact_id) is not None


def test_apply_stops_processing_artifact_after_expiration(
    manager: LifecyclePolicyManager, artifact_manager: ArtifactManager
):
    artifact = artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    manager.create_policy("expire-models", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=3600))
    manager.create_policy("archive-models", PolicyType.ARCHIVE, RetentionRule(max_age_seconds=3600))

    executions = manager.apply()

    assert len(executions) == 1
    assert executions[0].action == "expired"


def test_list_history_accumulates_across_applies(manager: LifecyclePolicyManager, artifact_manager: ArtifactManager):
    artifact = artifact_manager.create("a.log", ArtifactType.LOG_BUNDLE, b"data")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    manager.create_policy("expire-logs", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=3600))

    manager.apply()
    manager.apply()

    assert len(manager.list_history()) == 1


def test_api_create_and_list_policies(client: TestClient):
    create_response = client.post(
        "/storage/lifecycle",
        params={"name": "expire-logs", "policy_type": "expiration", "max_age_seconds": 3600},
    )
    assert create_response.status_code == 200

    list_response = client.get("/storage/lifecycle")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_api_apply_and_history(client: TestClient, artifact_manager: ArtifactManager):
    artifact = artifact_manager.create("a.log", ArtifactType.LOG_BUNDLE, b"data")
    age_artifact(artifact_manager, artifact.artifact_id, seconds=7200)
    client.post(
        "/storage/lifecycle",
        params={"name": "expire-logs", "policy_type": "expiration", "max_age_seconds": 3600},
    )

    apply_response = client.post("/storage/lifecycle/apply")
    assert apply_response.status_code == 200
    assert len(apply_response.json()) == 1

    history_response = client.get("/storage/lifecycle/history")
    assert history_response.status_code == 200
    assert len(history_response.json()) == 1
