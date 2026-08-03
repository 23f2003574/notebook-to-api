import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.batch_inference import (
    BatchInferenceEngine,
    BatchState,
    InvalidBatchStateError,
    UnknownBatchError,
    get_batch_inference_engine,
    router as batch_inference_router,
)
from backend.ai.inference_engine import InferenceEngine, get_inference_engine
from backend.ai.model_loader import ModelLoader, get_model_loader
from backend.ai.model_registry import ModelMetadata, ModelRegistry


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def loader() -> ModelLoader:
    return ModelLoader()


@pytest.fixture
def inference_engine() -> InferenceEngine:
    return InferenceEngine()


@pytest.fixture
def batch_engine() -> BatchInferenceEngine:
    return BatchInferenceEngine()


@pytest.fixture
def client(inference_engine: InferenceEngine, loader: ModelLoader, batch_engine: BatchInferenceEngine) -> TestClient:
    app = FastAPI()
    app.include_router(batch_inference_router)
    app.dependency_overrides[get_batch_inference_engine] = lambda: batch_engine
    app.dependency_overrides[get_inference_engine] = lambda: inference_engine
    app.dependency_overrides[get_model_loader] = lambda: loader
    return TestClient(app)


def load_model(registry: ModelRegistry, loader: ModelLoader, name: str = "gpt-embed"):
    registry.register(name, "1.0.0", ModelMetadata(provider="openai", entry_point="models.gpt_embed:Model"))
    return loader.load(name, registry=registry)


def test_submit_creates_queued_batch(batch_engine: BatchInferenceEngine):
    batch = batch_engine.submit("gpt-embed", ["a", "b", "c"])

    assert batch.state == BatchState.QUEUED
    assert len(batch.request.items) == 3


def test_submit_rejects_empty_items(batch_engine: BatchInferenceEngine):
    with pytest.raises(ValueError):
        batch_engine.submit("gpt-embed", [])


def test_submit_rejects_unsupported_mode(batch_engine: BatchInferenceEngine):
    with pytest.raises(ValueError):
        batch_engine.submit("gpt-embed", ["a"], mode="bogus")


def test_submit_rejects_mismatched_priorities(batch_engine: BatchInferenceEngine):
    with pytest.raises(ValueError):
        batch_engine.submit("gpt-embed", ["a", "b"], priorities=[1])


def test_execute_sequential_runs_all_items(
    registry: ModelRegistry, loader: ModelLoader, inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    load_model(registry, loader)
    batch = batch_engine.submit("gpt-embed", ["a", "b", "c"], mode="sequential")

    finished = batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)

    assert finished.state == BatchState.SUCCEEDED
    assert [item.index for item in finished.items] == [0, 1, 2]
    assert all(item.state == "succeeded" for item in finished.items)


def test_execute_parallel_runs_all_items(
    registry: ModelRegistry, loader: ModelLoader, inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    load_model(registry, loader)
    batch = batch_engine.submit("gpt-embed", ["a", "b", "c", "d"], mode="parallel", batch_size=2)

    finished = batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)

    assert finished.state == BatchState.SUCCEEDED
    assert {item.index for item in finished.items} == {0, 1, 2, 3}


def test_execute_priority_mode_orders_by_priority(
    registry: ModelRegistry, loader: ModelLoader, inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    load_model(registry, loader)
    batch = batch_engine.submit(
        "gpt-embed", ["low", "high", "medium"], mode="priority", priorities=[1, 10, 5]
    )

    finished = batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)

    assert [item.index for item in finished.items] == [1, 2, 0]


def test_execute_all_items_failing_marks_batch_failed(
    inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    unloaded_loader = ModelLoader()
    batch = batch_engine.submit("does-not-exist", ["a", "b"], mode="sequential")

    finished = batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=unloaded_loader)

    assert finished.state == BatchState.FAILED
    assert all(item.state == "failed" for item in finished.items)


def test_execute_mixed_success_and_failure_is_partial(
    registry: ModelRegistry, loader: ModelLoader, inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    load_model(registry, loader)

    real_infer = inference_engine.infer
    call_count = {"n": 0}

    def flaky_infer(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure")
        return real_infer(*args, **kwargs)

    inference_engine.infer = flaky_infer

    batch = batch_engine.submit("gpt-embed", ["a", "b", "c"], mode="sequential")
    finished = batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)

    assert finished.state == BatchState.PARTIAL
    states = [item.state for item in finished.items]
    assert states.count("succeeded") == 2
    assert states.count("failed") == 1


def test_execute_unknown_batch_raises(inference_engine: InferenceEngine, loader: ModelLoader, batch_engine: BatchInferenceEngine):
    with pytest.raises(UnknownBatchError):
        batch_engine.execute("does-not-exist", engine=inference_engine, loader=loader)


def test_execute_already_executed_raises(
    registry: ModelRegistry, loader: ModelLoader, inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    load_model(registry, loader)
    batch = batch_engine.submit("gpt-embed", ["a"])
    batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)

    with pytest.raises(InvalidBatchStateError):
        batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)


def test_cancel_queued_batch(batch_engine: BatchInferenceEngine):
    batch = batch_engine.submit("gpt-embed", ["a"])

    cancelled = batch_engine.cancel(batch.request.batch_id)

    assert cancelled.state == BatchState.CANCELLED


def test_cancel_unknown_batch_raises(batch_engine: BatchInferenceEngine):
    with pytest.raises(UnknownBatchError):
        batch_engine.cancel("does-not-exist")


def test_cancel_executed_batch_raises(
    registry: ModelRegistry, loader: ModelLoader, inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    load_model(registry, loader)
    batch = batch_engine.submit("gpt-embed", ["a"])
    batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)

    with pytest.raises(InvalidBatchStateError):
        batch_engine.cancel(batch.request.batch_id)


def test_results_returns_batch(
    registry: ModelRegistry, loader: ModelLoader, inference_engine: InferenceEngine, batch_engine: BatchInferenceEngine
):
    load_model(registry, loader)
    batch = batch_engine.submit("gpt-embed", ["a", "b"])
    batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)

    results = batch_engine.results(batch.request.batch_id)

    assert len(results.items) == 2


def test_results_unknown_batch_raises(batch_engine: BatchInferenceEngine):
    with pytest.raises(UnknownBatchError):
        batch_engine.results("does-not-exist")


def test_api_submit_batch(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)

    response = client.post(
        "/ai/batch", json={"model_name": "gpt-embed", "items": ["a", "b"], "mode": "sequential"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "succeeded"
    assert len(body["items"]) == 2


def test_api_submit_invalid_batch_returns_422(client: TestClient):
    response = client.post("/ai/batch", json={"model_name": "gpt-embed", "items": []})

    assert response.status_code == 422


def test_api_get_batch_status(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)
    submitted = client.post("/ai/batch", json={"model_name": "gpt-embed", "items": ["a"]})
    batch_id = submitted.json()["batch_id"]

    response = client.get(f"/ai/batch/{batch_id}")

    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"
    assert "items" not in response.json()


def test_api_get_batch_status_unknown_returns_404(client: TestClient):
    response = client.get("/ai/batch/does-not-exist")

    assert response.status_code == 404


def test_api_get_batch_results(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)
    submitted = client.post("/ai/batch", json={"model_name": "gpt-embed", "items": ["a", "b"]})
    batch_id = submitted.json()["batch_id"]

    response = client.get(f"/ai/batch/{batch_id}/results")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_cancel_executed_batch_returns_409(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)
    submitted = client.post("/ai/batch", json={"model_name": "gpt-embed", "items": ["a"]})
    batch_id = submitted.json()["batch_id"]

    response = client.delete(f"/ai/batch/{batch_id}")

    assert response.status_code == 409


def test_api_cancel_unknown_batch_returns_404(client: TestClient):
    response = client.delete("/ai/batch/does-not-exist")

    assert response.status_code == 404
