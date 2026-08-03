import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.inference_engine import (
    InferenceEngine,
    InferenceState,
    InvalidStateTransitionError,
    UnknownRequestError,
    get_inference_engine,
    router as inference_router,
)
from backend.ai.model_loader import ModelLoader, ModelNotLoadedError, get_model_loader
from backend.ai.model_registry import ModelMetadata, ModelRegistry


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def loader() -> ModelLoader:
    return ModelLoader()


@pytest.fixture
def engine() -> InferenceEngine:
    return InferenceEngine()


@pytest.fixture
def client(engine: InferenceEngine, loader: ModelLoader) -> TestClient:
    app = FastAPI()
    app.include_router(inference_router)
    app.dependency_overrides[get_inference_engine] = lambda: engine
    app.dependency_overrides[get_model_loader] = lambda: loader
    return TestClient(app)


def load_model(registry: ModelRegistry, loader: ModelLoader, name: str = "gpt-embed"):
    registry.register(name, "1.0.0", ModelMetadata(provider="openai", entry_point="models.gpt_embed:Model"))
    return loader.load(name, registry=registry)


def test_infer_runs_synchronously(registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine):
    load_model(registry, loader)

    result = engine.infer("gpt-embed", {"prompt": "hi"}, loader=loader)

    assert result.state == InferenceState.SUCCEEDED
    assert result.output["model"] == "gpt-embed"
    assert result.output["output"] == {"prompt": "hi"}
    assert result.duration_ms is not None


def test_infer_batch_mode_processes_each_item(registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine):
    load_model(registry, loader)

    result = engine.infer("gpt-embed", [{"prompt": "a"}, {"prompt": "b"}], loader=loader, mode="batch")

    assert result.state == InferenceState.SUCCEEDED
    assert len(result.output) == 2
    assert result.output[0]["output"] == {"prompt": "a"}


def test_infer_batch_mode_requires_list_input(registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine):
    load_model(registry, loader)

    with pytest.raises(ValueError):
        engine.infer("gpt-embed", {"prompt": "hi"}, loader=loader, mode="batch")


def test_infer_rejects_unsupported_mode(registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine):
    load_model(registry, loader)

    with pytest.raises(ValueError):
        engine.infer("gpt-embed", "hi", loader=loader, mode="bogus")


def test_infer_unloaded_model_raises(loader: ModelLoader, engine: InferenceEngine):
    with pytest.raises(ModelNotLoadedError):
        engine.infer("does-not-exist", "hi", loader=loader)


def test_stream_yields_chunks_and_completes(registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine):
    load_model(registry, loader)

    request_id, chunks = engine.stream("gpt-embed", "hello world foo bar", loader=loader, chunk_size=1)
    collected = list(chunks)

    assert collected == ["hello", "world", "foo", "bar"]
    result = engine.status(request_id)
    assert result.state == InferenceState.SUCCEEDED
    assert result.output["output"] == collected


def test_stream_unloaded_model_raises(loader: ModelLoader, engine: InferenceEngine):
    with pytest.raises(ModelNotLoadedError):
        engine.stream("does-not-exist", "hi", loader=loader)


def test_cancel_stops_streaming_and_marks_cancelled(
    registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine
):
    load_model(registry, loader)

    request_id, chunks = engine.stream("gpt-embed", "hello world foo bar", loader=loader, chunk_size=1)
    first = next(chunks)
    engine.cancel(request_id)
    remaining = list(chunks)

    assert first == "hello"
    assert remaining == []
    assert engine.status(request_id).state == InferenceState.CANCELLED


def test_cancel_unknown_request_raises(engine: InferenceEngine):
    with pytest.raises(UnknownRequestError):
        engine.cancel("does-not-exist")


def test_cancel_finished_request_raises(registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine):
    load_model(registry, loader)
    result = engine.infer("gpt-embed", "hi", loader=loader)

    with pytest.raises(InvalidStateTransitionError):
        engine.cancel(result.request.request_id)


def test_status_tracks_request(registry: ModelRegistry, loader: ModelLoader, engine: InferenceEngine):
    load_model(registry, loader)
    result = engine.infer("gpt-embed", "hi", loader=loader)

    tracked = engine.status(result.request.request_id)

    assert tracked.state == InferenceState.SUCCEEDED


def test_status_unknown_request_raises(engine: InferenceEngine):
    with pytest.raises(UnknownRequestError):
        engine.status("does-not-exist")


def test_api_infer_endpoint(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)

    response = client.post("/ai/inference", json={"model_name": "gpt-embed", "input": {"prompt": "hi"}})

    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"


def test_api_infer_unloaded_model_returns_404(client: TestClient):
    response = client.post("/ai/inference", json={"model_name": "does-not-exist", "input": "hi"})

    assert response.status_code == 404


def test_api_stream_endpoint(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)

    with client.stream(
        "POST", "/ai/inference/stream", json={"model_name": "gpt-embed", "input": "hello world"}
    ) as response:
        assert response.status_code == 200
        body = b"".join(response.iter_bytes()).decode()

    assert "hello" in body
    assert "world" in body


def test_api_status_endpoint(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)
    submitted = client.post("/ai/inference", json={"model_name": "gpt-embed", "input": "hi"})
    request_id = submitted.json()["request_id"]

    response = client.get(f"/ai/inference/{request_id}")

    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"


def test_api_status_unknown_request_returns_404(client: TestClient):
    response = client.get("/ai/inference/does-not-exist")

    assert response.status_code == 404


def test_api_cancel_finished_request_returns_409(client: TestClient, registry: ModelRegistry, loader: ModelLoader):
    load_model(registry, loader)
    submitted = client.post("/ai/inference", json={"model_name": "gpt-embed", "input": "hi"})
    request_id = submitted.json()["request_id"]

    response = client.delete(f"/ai/inference/{request_id}")

    assert response.status_code == 409


def test_api_cancel_unknown_request_returns_404(client: TestClient):
    response = client.delete("/ai/inference/does-not-exist")

    assert response.status_code == 404
