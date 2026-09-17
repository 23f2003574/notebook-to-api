import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.dashboard import PerformanceDashboardAPI, export_router
from backend.performance.export_service import (
    ExportFormat,
    ExportManifest,
    PerformanceExport,
    PerformanceExportService,
    get_performance_export_service,
)
from backend.performance.cache_manager import CacheManager
from backend.performance.in_memory_cache import InMemoryCache
from backend.performance.cache_eviction import CacheEvictionEngine
from backend.performance.response_cache import ResponseCacheMiddleware
from backend.performance.distributed_cache import (
    CacheBackend,
    ConnectionConfig,
    DistributedCacheAdapter,
)
from backend.performance.resource_pool import PoolType, ResourcePoolManager
from backend.performance.profiler import PerformanceProfiler
from backend.performance.compression import CompressionEngine


@pytest.fixture
def cache_manager() -> CacheManager:
    return CacheManager()


@pytest.fixture
def profiler() -> PerformanceProfiler:
    return PerformanceProfiler()


@pytest.fixture
def dashboard(cache_manager: CacheManager, profiler: PerformanceProfiler) -> PerformanceDashboardAPI:
    distributed_adapter = DistributedCacheAdapter()
    distributed_adapter.connect(ConnectionConfig(name="primary", backend=CacheBackend.REDIS))
    pool_manager = ResourcePoolManager()
    pool_manager.create_pool("workers", pool_type=PoolType.WORKER, max_size=2)
    return PerformanceDashboardAPI(
        cache_manager=cache_manager,
        memory_cache=InMemoryCache(),
        eviction_engine=CacheEvictionEngine(),
        response_cache=ResponseCacheMiddleware(),
        distributed_adapter=distributed_adapter,
        pool_manager=pool_manager,
        profiler=profiler,
        compression_engine=CompressionEngine(),
    )


@pytest.fixture
def service(dashboard: PerformanceDashboardAPI, profiler: PerformanceProfiler) -> PerformanceExportService:
    return PerformanceExportService(dashboard=dashboard, profiler=profiler)


@pytest.fixture
def client(service: PerformanceExportService) -> TestClient:
    app = FastAPI()
    app.include_router(export_router)
    app.dependency_overrides[get_performance_export_service] = lambda: service
    return TestClient(app)


def test_export_metrics_json_contains_expected_sections(service: PerformanceExportService):
    export = service.export_metrics(fmt=ExportFormat.JSON)

    assert isinstance(export, PerformanceExport)
    assert export.section == "metrics"
    assert export.content_type == "application/json"
    data = json.loads(export.content)
    assert set(data.keys()) == {"resources", "connections", "compression", "profiler"}


def test_export_metrics_filters_by_section(service: PerformanceExportService):
    export = service.export_metrics(fmt=ExportFormat.JSON, sections=["resources"])

    data = json.loads(export.content)
    assert set(data.keys()) == {"resources"}


def test_export_metrics_csv_is_flat_key_value(service: PerformanceExportService):
    export = service.export_metrics(fmt=ExportFormat.CSV, sections=["profiler"])

    assert export.content_type == "text/csv"
    assert "key,value" in export.content
    assert "profiler.total_sessions" in export.content


def test_export_metrics_yaml_contains_nested_keys(service: PerformanceExportService):
    export = service.export_metrics(fmt=ExportFormat.YAML, sections=["profiler"])

    assert export.content_type == "application/x-yaml"
    assert "profiler:" in export.content
    assert "total_sessions:" in export.content


def test_export_cache_reports_size(service: PerformanceExportService, cache_manager: CacheManager):
    cache_manager.put("a", 1)

    export = service.export_cache(fmt=ExportFormat.JSON)

    data = json.loads(export.content)
    assert data["cache_manager"]["size"] == 1


def test_export_profiles_json_includes_each_session(
    service: PerformanceExportService, profiler: PerformanceProfiler
):
    profiler.start("parse_notebook", session_id="s1")
    profiler.stop("s1")
    profiler.start("compile_api", session_id="s2")

    export = service.export_profiles(fmt=ExportFormat.JSON)

    data = json.loads(export.content)
    assert {item["session_id"] for item in data} == {"s1", "s2"}


def test_export_profiles_filters_by_session_id(
    service: PerformanceExportService, profiler: PerformanceProfiler
):
    profiler.start("parse_notebook", session_id="s1")
    profiler.start("compile_api", session_id="s2")

    export = service.export_profiles(fmt=ExportFormat.JSON, session_ids=["s1"])

    data = json.loads(export.content)
    assert [item["session_id"] for item in data] == ["s1"]


def test_export_profiles_csv_has_one_row_per_session(
    service: PerformanceExportService, profiler: PerformanceProfiler
):
    profiler.start("parse_notebook", session_id="s1")
    profiler.start("compile_api", session_id="s2")

    export = service.export_profiles(fmt=ExportFormat.CSV)

    rows = export.content.strip().splitlines()
    assert len(rows) == 3  # header + 2 sessions


def test_export_all_bundles_every_section_with_manifest(
    service: PerformanceExportService, cache_manager: CacheManager, profiler: PerformanceProfiler
):
    cache_manager.put("a", 1)
    profiler.start("parse_notebook", session_id="s1")

    manifest = service.export_all(fmt=ExportFormat.JSON)

    assert isinstance(manifest, ExportManifest)
    assert manifest.sections == ("metrics", "cache", "profiles")
    assert manifest.size_bytes == len(manifest.export.content.encode("utf-8"))
    assert len(manifest.checksum) == 64

    bundled = json.loads(manifest.export.content)
    assert bundled["cache"]["cache_manager"]["size"] == 1
    assert bundled["profiles"][0]["session_id"] == "s1"


def test_export_all_generates_unique_ids_across_calls(service: PerformanceExportService):
    first = service.export_all()
    second = service.export_all()

    assert first.export_id != second.export_id


def test_api_export_metrics(client: TestClient):
    response = client.get("/performance/export/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["section"] == "metrics"
    assert "resources" in json.loads(body["content"])


def test_api_export_metrics_filters_sections(client: TestClient):
    response = client.get("/performance/export/metrics", params={"sections": "resources,compression"})

    body = response.json()
    data = json.loads(body["content"])
    assert set(data.keys()) == {"resources", "compression"}


def test_api_export_metrics_rejects_unknown_format(client: TestClient):
    response = client.get("/performance/export/metrics", params={"format": "xml"})

    assert response.status_code == 422


def test_api_export_cache(client: TestClient, cache_manager: CacheManager):
    cache_manager.put("a", 1)

    response = client.get("/performance/export/cache")

    assert response.status_code == 200
    data = json.loads(response.json()["content"])
    assert data["cache_manager"]["size"] == 1


def test_api_export_profiles(client: TestClient, profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")

    response = client.get("/performance/export/profiles")

    assert response.status_code == 200
    data = json.loads(response.json()["content"])
    assert data[0]["session_id"] == "s1"


def test_api_export_all(client: TestClient, cache_manager: CacheManager):
    cache_manager.put("a", 1)

    response = client.get("/performance/export/all")

    assert response.status_code == 200
    body = response.json()
    assert set(body["sections"]) == {"metrics", "cache", "profiles"}
    assert "checksum" in body
