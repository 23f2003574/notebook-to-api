import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_integrity import (
    ArtifactIntegrityEngine,
    ChecksumAlgorithm,
    IntegrityReport,
    get_artifact_integrity_engine,
    router as artifact_integrity_router,
)
from backend.storage.object_storage import ObjectStorageEngine


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def engine(object_storage: ObjectStorageEngine) -> ArtifactIntegrityEngine:
    return ArtifactIntegrityEngine(object_storage=object_storage)


@pytest.fixture
def client(engine: ArtifactIntegrityEngine) -> TestClient:
    app = FastAPI()
    app.include_router(artifact_integrity_router)
    app.dependency_overrides[get_artifact_integrity_engine] = lambda: engine
    return TestClient(app)


def test_calculate_checksum_sha256(engine: ArtifactIntegrityEngine):
    checksum = engine.calculate_checksum(b"hello", algorithm=ChecksumAlgorithm.SHA256)

    assert checksum == hashlib.sha256(b"hello").hexdigest()


def test_calculate_checksum_sha512(engine: ArtifactIntegrityEngine):
    checksum = engine.calculate_checksum(b"hello", algorithm=ChecksumAlgorithm.SHA512)

    assert checksum == hashlib.sha512(b"hello").hexdigest()


def test_calculate_checksum_md5(engine: ArtifactIntegrityEngine):
    checksum = engine.calculate_checksum(b"hello", algorithm=ChecksumAlgorithm.MD5)

    assert checksum == hashlib.md5(b"hello").hexdigest()


def test_calculate_checksum_crc32(engine: ArtifactIntegrityEngine):
    checksum = engine.calculate_checksum(b"hello", algorithm=ChecksumAlgorithm.CRC32)

    assert len(checksum) == 8
    assert checksum == format(0x3610A686, "08x")


def test_verify_returns_missing_for_unknown_key(engine: ArtifactIntegrityEngine):
    report = engine.verify("missing.txt")

    assert isinstance(report, IntegrityReport)
    assert report.status == "missing"
    assert report.verified is False


def test_verify_returns_ok_for_untampered_object(engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    report = engine.verify("a.txt")

    assert report.status == "ok"
    assert report.verified is True
    assert report.actual_checksum == hashlib.sha256(b"hello").hexdigest()


def test_verify_detects_corruption(engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")
    stored = object_storage.get("a.txt")
    stored.data = b"tampered"

    report = engine.verify("a.txt")

    assert report.status == "corrupted"
    assert report.verified is False
    assert report.actual_checksum == hashlib.sha256(b"tampered").hexdigest()


def test_verify_with_explicit_expected_checksum(engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    report = engine.verify("a.txt", expected_checksum="deadbeef")

    assert report.status == "corrupted"
    assert report.expected_checksum == "deadbeef"


def test_verify_non_default_algorithm_establishes_baseline_on_first_check(
    engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine
):
    object_storage.put("a.txt", b"hello")

    first = engine.verify("a.txt", algorithm=ChecksumAlgorithm.MD5)
    assert first.verified is True

    stored = object_storage.get("a.txt")
    stored.data = b"tampered"

    second = engine.verify("a.txt", algorithm=ChecksumAlgorithm.MD5)
    assert second.verified is False
    assert second.status == "corrupted"


def test_repair_metadata_resyncs_baselines(engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")
    engine.verify("a.txt", algorithm=ChecksumAlgorithm.MD5)

    stored = object_storage.get("a.txt")
    stored.data = b"tampered"
    assert engine.verify("a.txt").status == "corrupted"

    repair_report = engine.repair_metadata("a.txt")

    assert repair_report.status == "ok"
    assert engine.verify("a.txt").status == "ok"
    assert engine.verify("a.txt", algorithm=ChecksumAlgorithm.MD5).status == "ok"


def test_repair_metadata_raises_for_missing_key(engine: ArtifactIntegrityEngine):
    with pytest.raises(KeyError):
        engine.repair_metadata("missing.txt")


def test_audit_covers_all_tracked_keys(engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")
    object_storage.put("b.txt", b"world")

    reports = engine.audit()

    assert {report.key for report in reports} == {"a.txt", "b.txt"}
    assert all(report.status == "ok" for report in reports)


def test_audit_detects_corruption_across_keys(engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")
    object_storage.put("b.txt", b"world")
    object_storage.get("b.txt").data = b"tampered"

    reports = engine.audit()
    by_key = {report.key: report for report in reports}

    assert by_key["a.txt"].status == "ok"
    assert by_key["b.txt"].status == "corrupted"


def test_audit_with_explicit_keys(engine: ArtifactIntegrityEngine, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    reports = engine.audit(keys=["a.txt", "missing.txt"])

    by_key = {report.key: report for report in reports}
    assert by_key["a.txt"].status == "ok"
    assert by_key["missing.txt"].status == "missing"


def test_api_verify_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    response = client.post("/storage/integrity/verify", params={"key": "a.txt"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_get_integrity_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    response = client.get("/storage/integrity/a.txt")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_get_integrity_missing_returns_404(client: TestClient):
    response = client.get("/storage/integrity/missing.txt")
    assert response.status_code == 404


def test_api_audit_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")
    object_storage.put("b.txt", b"world")

    response = client.post("/storage/integrity/audit")

    assert response.status_code == 200
    assert len(response.json()) == 2
