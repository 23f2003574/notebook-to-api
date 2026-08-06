import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.blob_upload import (
    BlobUploadService,
    UploadMode,
    UploadPart,
    UploadSession,
    UploadStatus,
    get_blob_upload_service,
    router as blob_upload_router,
)
from backend.storage.object_storage import ObjectStorageEngine


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def service(object_storage: ObjectStorageEngine) -> BlobUploadService:
    return BlobUploadService(object_storage=object_storage)


@pytest.fixture
def client(service: BlobUploadService) -> TestClient:
    app = FastAPI()
    app.include_router(blob_upload_router)
    app.dependency_overrides[get_blob_upload_service] = lambda: service
    return TestClient(app)


def test_create_upload_creates_session(service: BlobUploadService):
    session = service.create_upload("artifacts/big-file.bin")

    assert isinstance(session, UploadSession)
    assert session.key == "artifacts/big-file.bin"
    assert session.mode == UploadMode.MULTIPART
    assert session.status == UploadStatus.PENDING


def test_create_upload_rejects_empty_key(service: BlobUploadService):
    with pytest.raises(ValueError):
        service.create_upload("")


def test_upload_part_multipart(service: BlobUploadService):
    session = service.create_upload("big-file.bin", mode=UploadMode.MULTIPART)

    part = service.upload_part(session.upload_id, 1, b"chunk-one")

    assert isinstance(part, UploadPart)
    assert part.part_number == 1
    assert part.size == len(b"chunk-one")

    refreshed = service.get_upload(session.upload_id)
    assert refreshed.status == UploadStatus.IN_PROGRESS
    assert len(refreshed.parts) == 1


def test_upload_part_rejects_empty_data(service: BlobUploadService):
    session = service.create_upload("big-file.bin")

    with pytest.raises(ValueError):
        service.upload_part(session.upload_id, 1, b"")


def test_upload_part_rejects_invalid_part_number(service: BlobUploadService):
    session = service.create_upload("big-file.bin")

    with pytest.raises(ValueError):
        service.upload_part(session.upload_id, 0, b"data")


def test_upload_part_raises_for_missing_session(service: BlobUploadService):
    with pytest.raises(KeyError):
        service.upload_part("missing", 1, b"data")


def test_upload_part_validates_checksum(service: BlobUploadService):
    session = service.create_upload("big-file.bin")

    with pytest.raises(ValueError):
        service.upload_part(session.upload_id, 1, b"data", expected_checksum="wrong")

    service.upload_part(session.upload_id, 1, b"data", expected_checksum=checksum(b"data"))


def test_resume_upload_after_partial_progress(service: BlobUploadService, object_storage: ObjectStorageEngine):
    session = service.create_upload("resumable.bin", mode=UploadMode.RESUMABLE)
    service.upload_part(session.upload_id, 1, b"part-one-")

    in_progress = service.get_upload(session.upload_id)
    assert len(in_progress.parts) == 1

    service.upload_part(session.upload_id, 2, b"part-two")
    stored = service.complete_upload(session.upload_id)

    assert stored.data == b"part-one-part-two"
    assert object_storage.get("resumable.bin").data == b"part-one-part-two"


def test_complete_upload_assembles_parts_in_order(service: BlobUploadService):
    session = service.create_upload("ordered.bin")
    service.upload_part(session.upload_id, 2, b"world")
    service.upload_part(session.upload_id, 1, b"hello ")

    stored = service.complete_upload(session.upload_id)

    assert stored.data == b"hello world"


def test_complete_upload_marks_session_completed(service: BlobUploadService):
    session = service.create_upload("ordered.bin")
    service.upload_part(session.upload_id, 1, b"data")

    service.complete_upload(session.upload_id)

    completed = service.get_upload(session.upload_id)
    assert completed.status == UploadStatus.COMPLETED


def test_complete_upload_raises_without_parts(service: BlobUploadService):
    session = service.create_upload("empty.bin")

    with pytest.raises(ValueError):
        service.complete_upload(session.upload_id)


def test_complete_upload_raises_for_missing_session(service: BlobUploadService):
    with pytest.raises(KeyError):
        service.complete_upload("missing")


def test_complete_upload_twice_raises(service: BlobUploadService):
    session = service.create_upload("ordered.bin")
    service.upload_part(session.upload_id, 1, b"data")
    service.complete_upload(session.upload_id)

    with pytest.raises(ValueError):
        service.complete_upload(session.upload_id)


def test_abort_upload_workflow(service: BlobUploadService):
    session = service.create_upload("aborted.bin")
    service.upload_part(session.upload_id, 1, b"data")

    aborted = service.abort_upload(session.upload_id)

    assert aborted is True
    session_state = service.get_upload(session.upload_id)
    assert session_state.status == UploadStatus.ABORTED


def test_abort_upload_returns_false_for_missing_session(service: BlobUploadService):
    assert service.abort_upload("missing") is False


def test_abort_upload_prevents_further_parts(service: BlobUploadService):
    session = service.create_upload("aborted.bin")
    service.abort_upload(session.upload_id)

    with pytest.raises(ValueError):
        service.upload_part(session.upload_id, 1, b"data")


def test_abort_completed_upload_raises(service: BlobUploadService):
    session = service.create_upload("ordered.bin")
    service.upload_part(session.upload_id, 1, b"data")
    service.complete_upload(session.upload_id)

    with pytest.raises(ValueError):
        service.abort_upload(session.upload_id)


def test_api_multipart_upload_workflow(client: TestClient):
    create_response = client.post("/storage/uploads", params={"key": "big.bin"})
    assert create_response.status_code == 200
    upload_id = create_response.json()["upload_id"]

    part1_response = client.put(f"/storage/uploads/{upload_id}/parts/1", content=b"hello ")
    assert part1_response.status_code == 200

    part2_response = client.put(f"/storage/uploads/{upload_id}/parts/2", content=b"world")
    assert part2_response.status_code == 200

    complete_response = client.post(f"/storage/uploads/{upload_id}/complete")
    assert complete_response.status_code == 200
    assert complete_response.json()["key"] == "big.bin"


def test_api_upload_part_missing_session_returns_404(client: TestClient):
    response = client.put("/storage/uploads/missing/parts/1", content=b"data")
    assert response.status_code == 404


def test_api_abort_upload(client: TestClient):
    create_response = client.post("/storage/uploads", params={"key": "big.bin"})
    upload_id = create_response.json()["upload_id"]

    abort_response = client.delete(f"/storage/uploads/{upload_id}")
    assert abort_response.status_code == 200
    assert abort_response.json() == {"upload_id": upload_id, "aborted": True}

    repeat_response = client.delete(f"/storage/uploads/{upload_id}")
    assert repeat_response.status_code == 200

    missing_response = client.delete("/storage/uploads/never-existed")
    assert missing_response.status_code == 404
