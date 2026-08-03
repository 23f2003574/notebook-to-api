import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.task_serializer import (
    SerializationMetadata,
    SerializedTask,
    TaskSerializationEngine,
    SUPPORTED_FORMATS,
    router as task_serializer_router,
)


@pytest.fixture
def engine() -> TaskSerializationEngine:
    return TaskSerializationEngine()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(task_serializer_router)
    return TestClient(app)


SAMPLE_PAYLOAD = {
    "notebook": "analysis.ipynb",
    "cell_count": 12,
    "timeout_seconds": 30.5,
    "retry": True,
    "tags": ["gpu", "batch"],
    "options": {"verbose": False, "seed": None},
}


@pytest.mark.parametrize("format", SUPPORTED_FORMATS)
def test_serialize_round_trips_through_deserialize(engine: TaskSerializationEngine, format: str):
    task = engine.serialize("job-1", "parse", SAMPLE_PAYLOAD, priority=5, policy="priority", format=format)

    body = engine.deserialize(task)

    assert body["job_id"] == "job-1"
    assert body["capability"] == "parse"
    assert body["priority"] == 5
    assert body["policy"] == "priority"
    assert body["payload"] == SAMPLE_PAYLOAD


def test_serialize_produces_metadata(engine: TaskSerializationEngine):
    task = engine.serialize("job-1", "parse", {"a": 1})

    assert isinstance(task.metadata, SerializationMetadata)
    assert task.metadata.format == "json"
    assert task.metadata.schema_version == "1.0"
    assert task.metadata.size_bytes == len(task.data)


def test_serialize_is_deterministic(engine: TaskSerializationEngine):
    first = engine.serialize("job-1", "parse", SAMPLE_PAYLOAD)
    second = engine.serialize("job-1", "parse", SAMPLE_PAYLOAD)

    assert first.data == second.data
    assert first.metadata.checksum == second.metadata.checksum


def test_serialize_rejects_unsupported_format(engine: TaskSerializationEngine):
    with pytest.raises(ValueError):
        engine.serialize("job-1", "parse", {}, format="xml")


def test_encode_decode_json_round_trip(engine: TaskSerializationEngine):
    data = engine.encode("json", SAMPLE_PAYLOAD)

    assert engine.decode("json", data) == SAMPLE_PAYLOAD


def test_encode_decode_msgpack_round_trip(engine: TaskSerializationEngine):
    data = engine.encode("msgpack", SAMPLE_PAYLOAD)

    assert engine.decode("msgpack", data) == SAMPLE_PAYLOAD


def test_encode_decode_binary_round_trip(engine: TaskSerializationEngine):
    data = engine.encode("binary", SAMPLE_PAYLOAD)

    assert engine.decode("binary", data) == SAMPLE_PAYLOAD


def test_binary_format_is_compressed_smaller_than_json_for_repetitive_payload(engine: TaskSerializationEngine):
    repetitive = {"chunks": ["x" * 200] * 20}

    json_data = engine.encode("json", repetitive)
    binary_data = engine.encode("binary", repetitive)

    assert len(binary_data) < len(json_data)


def test_deserialize_detects_tampered_data(engine: TaskSerializationEngine):
    task = engine.serialize("job-1", "parse", {"a": 1})
    tampered = SerializedTask(
        job_id=task.job_id,
        capability=task.capability,
        priority=task.priority,
        policy=task.policy,
        payload=task.payload,
        metadata=task.metadata,
        data=task.data + b"\x00",
    )

    with pytest.raises(ValueError):
        engine.deserialize(tampered)


def test_deserialize_detects_mismatched_envelope(engine: TaskSerializationEngine):
    task = engine.serialize("job-1", "parse", {"a": 1})
    mismatched = SerializedTask(
        job_id="job-2",
        capability=task.capability,
        priority=task.priority,
        policy=task.policy,
        payload=task.payload,
        metadata=task.metadata,
        data=task.data,
    )

    with pytest.raises(ValueError):
        engine.deserialize(mismatched)


def test_deserialize_rejects_unsupported_schema_version(engine: TaskSerializationEngine):
    task = engine.serialize("job-1", "parse", {"a": 1})
    old_schema = SerializedTask(
        job_id=task.job_id,
        capability=task.capability,
        priority=task.priority,
        policy=task.policy,
        payload=task.payload,
        metadata=SerializationMetadata(
            format=task.metadata.format,
            schema_version="0.1",
            checksum=task.metadata.checksum,
            size_bytes=task.metadata.size_bytes,
        ),
        data=task.data,
    )

    with pytest.raises(ValueError):
        engine.deserialize(old_schema)


def test_serialized_task_to_dict_and_from_dict_round_trip(engine: TaskSerializationEngine):
    task = engine.serialize("job-1", "parse", SAMPLE_PAYLOAD, format="msgpack")

    restored = SerializedTask.from_dict(task.to_dict())

    assert restored == task
    assert engine.deserialize(restored)["payload"] == SAMPLE_PAYLOAD


def test_api_serialize(client: TestClient):
    response = client.post(
        "/cluster/tasks/serialize",
        json={"job_id": "job-1", "capability": "parse", "payload": {"a": 1}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "job-1"
    assert body["metadata"]["format"] == "json"


def test_api_serialize_missing_field_returns_422(client: TestClient):
    response = client.post("/cluster/tasks/serialize", json={"capability": "parse"})

    assert response.status_code == 422


def test_api_serialize_unsupported_format_returns_422(client: TestClient):
    response = client.post(
        "/cluster/tasks/serialize",
        json={"job_id": "job-1", "capability": "parse", "format": "xml"},
    )

    assert response.status_code == 422


def test_api_deserialize_round_trips_serialize_response(client: TestClient):
    serialize_response = client.post(
        "/cluster/tasks/serialize",
        json={"job_id": "job-1", "capability": "parse", "payload": {"a": 1}, "format": "msgpack"},
    )

    response = client.post("/cluster/tasks/deserialize", json=serialize_response.json())

    assert response.status_code == 200
    assert response.json()["payload"] == {"a": 1}


def test_api_deserialize_detects_tampered_data(client: TestClient):
    serialized = client.post(
        "/cluster/tasks/serialize",
        json={"job_id": "job-1", "capability": "parse"},
    ).json()
    serialized["data"] = serialized["data"][:-2] + ("AA" if serialized["data"][-2:] != "AA" else "BB")

    response = client.post("/cluster/tasks/deserialize", json=serialized)

    assert response.status_code == 422


def test_api_formats(client: TestClient):
    response = client.get("/cluster/tasks/formats")

    assert response.status_code == 200
    assert response.json() == list(SUPPORTED_FORMATS)
