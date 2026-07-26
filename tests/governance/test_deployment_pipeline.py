from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_pipeline import (
    DeploymentPipeline,
    DeploymentPipelineEngine,
    PipelineStage,
    UnknownPipelineError,
    router as deployment_pipeline_router,
)

BASE_TIME = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine() -> DeploymentPipelineEngine:
    return DeploymentPipelineEngine()


def _stages() -> list:
    return [
        PipelineStage(name="build", action="build_image"),
        PipelineStage(name="deploy", action="apply_manifests"),
    ]


def test_register_creates_pipeline(engine: DeploymentPipelineEngine):
    pipeline = engine.register("svc-a", _stages(), timestamp=BASE_TIME)

    assert pipeline.name == "svc-a"
    assert pipeline.version == "1.0.0"
    assert len(pipeline.stages) == 2


def test_register_requires_name(engine: DeploymentPipelineEngine):
    with pytest.raises(ValueError):
        engine.register("", _stages())


def test_register_requires_at_least_one_stage(engine: DeploymentPipelineEngine):
    with pytest.raises(ValueError):
        engine.register("svc-a", [])


def test_register_rejects_duplicate_stage_names(engine: DeploymentPipelineEngine):
    stages = [
        PipelineStage(name="build", action="build_image"),
        PipelineStage(name="build", action="build_image_again"),
    ]

    with pytest.raises(ValueError):
        engine.register("svc-a", stages)


def test_register_rejects_invalid_version(engine: DeploymentPipelineEngine):
    with pytest.raises(ValueError):
        engine.register("svc-a", _stages(), version="not-a-version")


def test_register_rejects_non_dict_metadata(engine: DeploymentPipelineEngine):
    with pytest.raises(ValueError):
        engine.register("svc-a", _stages(), metadata="invalid")


def test_validate_does_not_register(engine: DeploymentPipelineEngine):
    engine.validate("svc-a", _stages())

    assert engine.list() == []


def test_remove_deletes_pipeline(engine: DeploymentPipelineEngine):
    engine.register("svc-a", _stages())

    engine.remove("svc-a")

    with pytest.raises(UnknownPipelineError):
        engine.get("svc-a")


def test_remove_unknown_pipeline_raises(engine: DeploymentPipelineEngine):
    with pytest.raises(UnknownPipelineError):
        engine.remove("does-not-exist")


def test_get_returns_registered_pipeline(engine: DeploymentPipelineEngine):
    engine.register("svc-a", _stages(), timestamp=BASE_TIME)

    pipeline = engine.get("svc-a")

    assert isinstance(pipeline, DeploymentPipeline)
    assert pipeline.name == "svc-a"


def test_get_unknown_pipeline_raises(engine: DeploymentPipelineEngine):
    with pytest.raises(UnknownPipelineError):
        engine.get("does-not-exist")


def test_list_returns_all_registered_pipelines(engine: DeploymentPipelineEngine):
    engine.register("svc-a", _stages())
    engine.register("svc-b", _stages())

    assert {pipeline.name for pipeline in engine.list()} == {"svc-a", "svc-b"}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    return TestClient(app)


def test_api_register_and_get_pipeline(client: TestClient):
    register_response = client.post(
        "/governance/pipelines",
        json={
            "name": "svc-api-1",
            "stages": [{"name": "build", "action": "build_image"}],
        },
    )
    get_response = client.get("/governance/pipelines/svc-api-1")

    assert register_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "svc-api-1"


def test_api_register_requires_name_and_stages(client: TestClient):
    response = client.post("/governance/pipelines", json={})

    assert response.status_code == 422


def test_api_register_rejects_invalid_version(client: TestClient):
    response = client.post(
        "/governance/pipelines",
        json={
            "name": "svc-api-2",
            "stages": [{"name": "build", "action": "build_image"}],
            "version": "bad",
        },
    )

    assert response.status_code == 422


def test_api_get_unknown_pipeline_returns_404(client: TestClient):
    response = client.get("/governance/pipelines/does-not-exist")

    assert response.status_code == 404


def test_api_list_pipelines(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={
            "name": "svc-api-3",
            "stages": [{"name": "build", "action": "build_image"}],
        },
    )

    response = client.get("/governance/pipelines")

    assert response.status_code == 200
    assert any(p["name"] == "svc-api-3" for p in response.json())
