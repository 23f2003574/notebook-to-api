import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.performance.cache_manager import profile_router
from backend.performance.profiler import (
    PerformanceProfiler,
    PerformanceReport,
    ProfileSession,
    SessionAlreadyExistsError,
    SessionNotRunningError,
    UnknownSessionError,
    get_performance_profiler,
)


@pytest.fixture
def profiler() -> PerformanceProfiler:
    return PerformanceProfiler()


@pytest.fixture
def client(profiler: PerformanceProfiler) -> TestClient:
    app = FastAPI()
    app.include_router(profile_router)
    app.dependency_overrides[get_performance_profiler] = lambda: profiler
    return TestClient(app)


def test_start_creates_running_session(profiler: PerformanceProfiler):
    session = profiler.start("parse_notebook")

    assert isinstance(session, ProfileSession)
    assert session.name == "parse_notebook"
    assert session.status == "running"
    assert session.ended_at is None


def test_start_requires_name(profiler: PerformanceProfiler):
    with pytest.raises(ValueError):
        profiler.start("")


def test_start_rejects_duplicate_session_id(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")

    with pytest.raises(SessionAlreadyExistsError):
        profiler.start("parse_notebook", session_id="s1")


def test_stop_unknown_session_raises(profiler: PerformanceProfiler):
    with pytest.raises(UnknownSessionError):
        profiler.stop("missing")


def test_stop_already_stopped_session_raises(profiler: PerformanceProfiler):
    session = profiler.start("parse_notebook", session_id="s1")
    profiler.stop("s1")

    with pytest.raises(SessionNotRunningError):
        profiler.stop("s1")


def test_stop_computes_execution_time(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")
    time.sleep(0.01)

    session = profiler.stop("s1")

    assert session.status == "stopped"
    assert session.ended_at is not None
    assert session.execution_time_ms >= 10


def test_stop_accepts_explicit_resource_overrides(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")

    session = profiler.stop(
        "s1", cpu_usage_percent=42.5, memory_usage_bytes=2048, io_time_ms=5.0
    )

    assert session.cpu_usage_percent == 42.5
    assert session.memory_usage_bytes == 2048
    assert session.io_time_ms == 5.0


def test_stop_measures_resource_metrics_when_not_overridden(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")

    session = profiler.stop("s1")

    assert session.cpu_usage_percent >= 0.0
    assert session.memory_usage_bytes > 0
    assert session.io_time_ms >= 0.0


def test_profile_records_timeline_checkpoint(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")

    checkpoint = profiler.profile("s1", "load_file")

    assert checkpoint["label"] == "load_file"
    assert checkpoint["elapsed_ms"] >= 0


def test_profile_requires_label(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")

    with pytest.raises(ValueError):
        profiler.profile("s1", "")


def test_profile_unknown_session_raises(profiler: PerformanceProfiler):
    with pytest.raises(UnknownSessionError):
        profiler.profile("missing", "load_file")


def test_profile_after_stop_raises(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")
    profiler.stop("s1")

    with pytest.raises(SessionNotRunningError):
        profiler.profile("s1", "load_file")


def test_report_includes_timeline_and_checkpoint_count(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")
    profiler.profile("s1", "load_file")
    profiler.profile("s1", "parse_cells")
    profiler.stop("s1")

    report = profiler.report("s1")

    assert isinstance(report, PerformanceReport)
    assert report.checkpoint_count == 2
    assert [c["label"] for c in report.timeline] == ["load_file", "parse_cells"]
    assert report.status == "stopped"


def test_report_available_while_running(profiler: PerformanceProfiler):
    profiler.start("parse_notebook", session_id="s1")

    report = profiler.report("s1")

    assert report.status == "running"
    assert report.execution_time_ms is None


def test_report_unknown_session_raises(profiler: PerformanceProfiler):
    with pytest.raises(UnknownSessionError):
        profiler.report("missing")


def test_api_start_profile(client: TestClient):
    response = client.post("/performance/profile/start", json={"name": "parse_notebook"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "parse_notebook"
    assert body["status"] == "running"


def test_api_start_profile_requires_name(client: TestClient):
    response = client.post("/performance/profile/start", json={})

    assert response.status_code == 422


def test_api_start_profile_duplicate_session_id_conflicts(client: TestClient):
    client.post("/performance/profile/start", json={"name": "a", "session_id": "s1"})

    response = client.post("/performance/profile/start", json={"name": "b", "session_id": "s1"})

    assert response.status_code == 409


def test_api_stop_profile(client: TestClient):
    client.post("/performance/profile/start", json={"name": "parse_notebook", "session_id": "s1"})

    response = client.post(
        "/performance/profile/stop",
        json={"session_id": "s1", "cpu_usage_percent": 10.0, "memory_usage_bytes": 1024, "io_time_ms": 2.0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped"
    assert body["cpu_usage_percent"] == 10.0


def test_api_stop_unknown_session_returns_404(client: TestClient):
    response = client.post("/performance/profile/stop", json={"session_id": "missing"})

    assert response.status_code == 404


def test_api_stop_not_running_returns_409(client: TestClient):
    client.post("/performance/profile/start", json={"name": "parse_notebook", "session_id": "s1"})
    client.post("/performance/profile/stop", json={"session_id": "s1"})

    response = client.post("/performance/profile/stop", json={"session_id": "s1"})

    assert response.status_code == 409


def test_api_get_report(client: TestClient):
    client.post("/performance/profile/start", json={"name": "parse_notebook", "session_id": "s1"})
    client.post("/performance/profile/stop", json={"session_id": "s1"})

    response = client.get("/performance/profile/s1")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s1"
    assert body["status"] == "stopped"


def test_api_get_report_unknown_session_returns_404(client: TestClient):
    response = client.get("/performance/profile/missing")

    assert response.status_code == 404
