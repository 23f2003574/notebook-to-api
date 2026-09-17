import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.scheduling.job_registry import (
    Job,
    JobAlreadyRegisteredError,
    JobMetadata,
    JobRegistry,
    JobStatus,
    UnknownJobError,
    get_job_registry,
    router as job_registry_router,
)


@pytest.fixture
def registry() -> JobRegistry:
    return JobRegistry()


@pytest.fixture
def client(registry: JobRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(job_registry_router)
    app.dependency_overrides[get_job_registry] = lambda: registry
    return TestClient(app)


def test_register_creates_job(registry: JobRegistry):
    job = registry.register(
        "nightly-export", "batch", JobMetadata(description="Exports data", owner="alice")
    )

    assert isinstance(job, Job)
    assert job.job_id == "nightly-export"
    assert job.job_type == "batch"
    assert job.metadata.owner == "alice"
    assert job.status == JobStatus.PENDING


def test_metadata_source_defaults_to_empty_string():
    assert JobMetadata().source == ""


def test_metadata_source_round_trips_through_dict():
    metadata = JobMetadata.from_dict({"source": "scheduler:cron"})

    assert metadata.source == "scheduler:cron"
    assert metadata.to_dict()["source"] == "scheduler:cron"


def test_register_rejects_empty_job_id(registry: JobRegistry):
    with pytest.raises(ValueError):
        registry.register("", "batch")


def test_register_rejects_empty_job_type(registry: JobRegistry):
    with pytest.raises(ValueError):
        registry.register("nightly-export", "")


def test_register_rejects_duplicate_job_id(registry: JobRegistry):
    registry.register("nightly-export", "batch")

    with pytest.raises(JobAlreadyRegisteredError):
        registry.register("nightly-export", "batch")


def test_get_returns_registered_job(registry: JobRegistry):
    registry.register("nightly-export", "batch")

    assert registry.get("nightly-export").job_id == "nightly-export"


def test_get_unknown_job_raises(registry: JobRegistry):
    with pytest.raises(UnknownJobError):
        registry.get("does-not-exist")


def test_update_status_changes_status(registry: JobRegistry):
    registry.register("nightly-export", "batch")

    updated = registry.update_status("nightly-export", JobStatus.RUNNING)

    assert updated.status == JobStatus.RUNNING
    assert registry.get("nightly-export").status == JobStatus.RUNNING


def test_update_status_unknown_job_raises(registry: JobRegistry):
    with pytest.raises(UnknownJobError):
        registry.update_status("does-not-exist", JobStatus.RUNNING)


def test_list_jobs_returns_all_registered(registry: JobRegistry):
    registry.register("nightly-export", "batch")
    registry.register("hourly-sync", "batch")

    listed = {job.job_id for job in registry.list_jobs()}

    assert listed == {"nightly-export", "hourly-sync"}


def test_list_jobs_filters_by_tag(registry: JobRegistry):
    registry.register("nightly-export", "batch", JobMetadata(tags=("export",)))
    registry.register("auth-sync", "batch", JobMetadata(tags=("security",)))

    listed = registry.list_jobs(tag="export")

    assert [job.job_id for job in listed] == ["nightly-export"]


def test_list_jobs_filters_by_status(registry: JobRegistry):
    registry.register("nightly-export", "batch")
    registry.register("hourly-sync", "batch")
    registry.update_status("hourly-sync", JobStatus.RUNNING)

    listed = registry.list_jobs(status=JobStatus.RUNNING)

    assert [job.job_id for job in listed] == ["hourly-sync"]


def test_remove_deletes_job(registry: JobRegistry):
    registry.register("nightly-export", "batch")

    registry.remove("nightly-export")

    with pytest.raises(UnknownJobError):
        registry.get("nightly-export")


def test_remove_clears_tag_index(registry: JobRegistry):
    registry.register("nightly-export", "batch", JobMetadata(tags=("export",)))

    registry.remove("nightly-export")

    assert registry.list_jobs(tag="export") == []


def test_remove_unknown_job_raises(registry: JobRegistry):
    with pytest.raises(UnknownJobError):
        registry.remove("does-not-exist")


def test_api_register_and_list(client: TestClient):
    response = client.post(
        "/jobs",
        json={"job_id": "nightly-export", "job_type": "batch", "metadata": {"owner": "alice"}},
    )
    assert response.status_code == 201
    assert response.json()["job_id"] == "nightly-export"

    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/jobs", json={"job_id": "nightly-export", "job_type": "batch"})
    response = client.post("/jobs", json={"job_id": "nightly-export", "job_type": "batch"})

    assert response.status_code == 409


def test_api_register_missing_fields_returns_422(client: TestClient):
    response = client.post("/jobs", json={})

    assert response.status_code == 422


def test_api_get_unknown_job_returns_404(client: TestClient):
    response = client.get("/jobs/does-not-exist")

    assert response.status_code == 404


def test_api_get_returns_job(client: TestClient):
    client.post("/jobs", json={"job_id": "nightly-export", "job_type": "batch"})

    response = client.get("/jobs/nightly-export")

    assert response.status_code == 200
    assert response.json()["job_id"] == "nightly-export"


def test_api_delete_removes_job(client: TestClient):
    client.post("/jobs", json={"job_id": "nightly-export", "job_type": "batch"})

    response = client.delete("/jobs/nightly-export")
    assert response.status_code == 204

    assert client.get("/jobs/nightly-export").status_code == 404
