import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.model_benchmark import (
    BenchmarkResult,
    ModelBenchmarkService,
    UnknownBenchmarkError,
    UnknownSuiteError,
    get_model_benchmark_service,
    router as model_benchmark_router,
)
from backend.ai.model_registry import ModelMetadata, ModelRegistry, UnknownModelError, get_model_registry


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def service() -> ModelBenchmarkService:
    return ModelBenchmarkService()


@pytest.fixture
def client(registry: ModelRegistry, service: ModelBenchmarkService) -> TestClient:
    app = FastAPI()
    app.include_router(model_benchmark_router)
    app.dependency_overrides[get_model_registry] = lambda: registry
    app.dependency_overrides[get_model_benchmark_service] = lambda: service
    return TestClient(app)


def test_run_executes_benchmark_and_derives_metrics(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-fast", "1.0.0", ModelMetadata(latency_ms=50, weight=3.0, capabilities=("chat",)))

    result = service.run("standard-suite", "gpt-fast", registry=registry)

    assert isinstance(result, BenchmarkResult)
    assert result.metrics["latency_ms"] == 50
    assert result.metrics["throughput_rps"] == pytest.approx(20.0)
    assert result.metrics["accuracy"] == pytest.approx(0.8)
    assert result.metrics["memory_mb"] == 320.0


def test_run_unknown_model_raises(registry: ModelRegistry, service: ModelBenchmarkService):
    with pytest.raises(UnknownModelError):
        service.run("standard-suite", "does-not-exist", registry=registry)


def test_run_requires_suite_name(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-fast", "1.0.0")

    with pytest.raises(ValueError):
        service.run("", "gpt-fast", registry=registry)


def test_compare_runs_benchmark_for_each_model(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-fast", "1.0.0", ModelMetadata(latency_ms=50))
    registry.register("gpt-slow", "1.0.0", ModelMetadata(latency_ms=200))

    results = service.compare("standard-suite", ["gpt-fast", "gpt-slow"], registry=registry)

    assert [result.model_name for result in results] == ["gpt-fast", "gpt-slow"]


def test_compare_requires_at_least_one_model(registry: ModelRegistry, service: ModelBenchmarkService):
    with pytest.raises(ValueError):
        service.compare("standard-suite", [], registry=registry)


def test_leaderboard_ranks_by_latency_ascending(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-fast", "1.0.0", ModelMetadata(latency_ms=50))
    registry.register("gpt-slow", "1.0.0", ModelMetadata(latency_ms=200))
    service.run("standard-suite", "gpt-fast", registry=registry)
    service.run("standard-suite", "gpt-slow", registry=registry)

    ranked = service.leaderboard("standard-suite")

    assert [result.model_name for result in ranked] == ["gpt-fast", "gpt-slow"]


def test_leaderboard_ranks_by_accuracy_descending(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0", ModelMetadata(weight=1.0))
    registry.register("gpt-b", "1.0.0", ModelMetadata(weight=5.0))
    service.run("standard-suite", "gpt-a", registry=registry)
    service.run("standard-suite", "gpt-b", registry=registry)

    ranked = service.leaderboard("standard-suite", metric="accuracy")

    assert [result.model_name for result in ranked] == ["gpt-b", "gpt-a"]


def test_leaderboard_uses_latest_result_per_model(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0", ModelMetadata(latency_ms=200))
    service.run("standard-suite", "gpt-a", registry=registry)

    metadata_2 = ModelMetadata(latency_ms=10)
    registry.register("gpt-a", "2.0.0", metadata_2)
    latest = service.run("standard-suite", "gpt-a", registry=registry)

    ranked = service.leaderboard("standard-suite")

    assert ranked[0].benchmark_id == latest.benchmark_id
    assert ranked[0].metrics["latency_ms"] == 10


def test_leaderboard_unknown_suite_raises(service: ModelBenchmarkService):
    with pytest.raises(UnknownSuiteError):
        service.leaderboard("does-not-exist")


def test_history_returns_records_for_model(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0")
    service.run("standard-suite", "gpt-a", registry=registry)
    service.run("standard-suite", "gpt-a", registry=registry)

    records = service.history("standard-suite", "gpt-a")

    assert len(records) == 2


def test_history_returns_all_models_for_suite(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0")
    registry.register("gpt-b", "1.0.0")
    service.run("standard-suite", "gpt-a", registry=registry)
    service.run("standard-suite", "gpt-b", registry=registry)

    records = service.history("standard-suite")

    assert len(records) == 2


def test_history_unknown_model_raises(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0")
    service.run("standard-suite", "gpt-a", registry=registry)

    with pytest.raises(UnknownBenchmarkError):
        service.history("standard-suite", "does-not-exist")


def test_history_unknown_suite_raises(service: ModelBenchmarkService):
    with pytest.raises(UnknownSuiteError):
        service.history("does-not-exist")


def test_get_result_returns_benchmark(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0")
    result = service.run("standard-suite", "gpt-a", registry=registry)

    fetched = service.get_result(result.benchmark_id)

    assert fetched.benchmark_id == result.benchmark_id


def test_get_result_unknown_raises(service: ModelBenchmarkService):
    with pytest.raises(UnknownBenchmarkError):
        service.get_result("does-not-exist")


def test_list_results_returns_all(registry: ModelRegistry, service: ModelBenchmarkService):
    registry.register("gpt-a", "1.0.0")
    registry.register("gpt-b", "1.0.0")
    service.run("standard-suite", "gpt-a", registry=registry)
    service.run("standard-suite", "gpt-b", registry=registry)

    assert len(service.list_results()) == 2


def test_api_run_benchmark(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0", ModelMetadata(latency_ms=50))

    response = client.post("/ai/benchmarks", json={"suite_name": "standard-suite", "model_name": "gpt-a"})

    assert response.status_code == 201
    assert response.json()["model_name"] == "gpt-a"


def test_api_run_benchmark_unknown_model_returns_404(client: TestClient):
    response = client.post("/ai/benchmarks", json={"suite_name": "standard-suite", "model_name": "does-not-exist"})

    assert response.status_code == 404


def test_api_compare_models(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    registry.register("gpt-b", "1.0.0")

    response = client.post(
        "/ai/benchmarks", json={"suite_name": "standard-suite", "model_names": ["gpt-a", "gpt-b"]}
    )

    assert response.status_code == 201
    assert len(response.json()) == 2


def test_api_list_benchmarks(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    client.post("/ai/benchmarks", json={"suite_name": "standard-suite", "model_name": "gpt-a"})

    response = client.get("/ai/benchmarks")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_benchmark(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    submitted = client.post("/ai/benchmarks", json={"suite_name": "standard-suite", "model_name": "gpt-a"})
    benchmark_id = submitted.json()["benchmark_id"]

    response = client.get(f"/ai/benchmarks/{benchmark_id}")

    assert response.status_code == 200
    assert response.json()["benchmark_id"] == benchmark_id


def test_api_get_benchmark_unknown_returns_404(client: TestClient):
    response = client.get("/ai/benchmarks/does-not-exist")

    assert response.status_code == 404


def test_api_leaderboard(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-fast", "1.0.0", ModelMetadata(latency_ms=50))
    registry.register("gpt-slow", "1.0.0", ModelMetadata(latency_ms=200))
    client.post("/ai/benchmarks", json={"suite_name": "standard-suite", "model_name": "gpt-fast"})
    client.post("/ai/benchmarks", json={"suite_name": "standard-suite", "model_name": "gpt-slow"})

    response = client.get("/ai/benchmarks/leaderboard", params={"suite": "standard-suite"})

    assert response.status_code == 200
    assert [result["model_name"] for result in response.json()] == ["gpt-fast", "gpt-slow"]


def test_api_leaderboard_unknown_suite_returns_404(client: TestClient):
    response = client.get("/ai/benchmarks/leaderboard", params={"suite": "does-not-exist"})

    assert response.status_code == 404
