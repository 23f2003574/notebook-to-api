from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, HTTPException

SCHEMA_VERSION = "1.0"
SUPPORTED_FORMATS = ("json", "msgpack", "binary")


@dataclass(frozen=True)
class SerializationMetadata:
    """Describes how a task was encoded and how to verify it wasn't corrupted in transit."""

    format: str
    schema_version: str
    checksum: str
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SerializedTask:
    """A job packaged for transport: a routable envelope plus its encoded, checksummed body."""

    job_id: str
    capability: str
    priority: int
    policy: str
    payload: dict
    metadata: SerializationMetadata
    data: bytes

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "capability": self.capability,
            "priority": self.priority,
            "policy": self.policy,
            "payload": self.payload,
            "metadata": self.metadata.to_dict(),
            "data": base64.b64encode(self.data).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SerializedTask":
        metadata = raw["metadata"]
        return cls(
            job_id=raw["job_id"],
            capability=raw["capability"],
            priority=raw.get("priority", 0),
            policy=raw.get("policy", "least_loaded"),
            payload=raw.get("payload", {}),
            metadata=SerializationMetadata(
                format=metadata["format"],
                schema_version=metadata["schema_version"],
                checksum=metadata["checksum"],
                size_bytes=metadata["size_bytes"],
            ),
            data=base64.b64decode(raw["data"]),
        )


class TaskSerializationEngine:
    """Packages dispatch jobs into a transportable envelope and reconstructs them on the other end."""

    def encode(self, format: str, body: dict) -> bytes:
        if format == "json":
            return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if format == "msgpack":
            return _msgpack_encode(body)
        if format == "binary":
            return zlib.compress(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        raise ValueError(f"unsupported serialization format '{format}'; expected one of {SUPPORTED_FORMATS}")

    def decode(self, format: str, data: bytes) -> dict:
        if format == "json":
            return json.loads(data.decode("utf-8"))
        if format == "msgpack":
            return _msgpack_decode(data)
        if format == "binary":
            return json.loads(zlib.decompress(data).decode("utf-8"))
        raise ValueError(f"unsupported serialization format '{format}'; expected one of {SUPPORTED_FORMATS}")

    def serialize(
        self,
        job_id: str,
        capability: str,
        payload: Optional[dict] = None,
        *,
        priority: int = 0,
        policy: str = "least_loaded",
        format: str = "json",
    ) -> SerializedTask:
        if format not in SUPPORTED_FORMATS:
            raise ValueError(f"unsupported serialization format '{format}'; expected one of {SUPPORTED_FORMATS}")

        body = {
            "job_id": job_id,
            "capability": capability,
            "priority": priority,
            "policy": policy,
            "payload": payload or {},
        }
        data = self.encode(format, body)
        metadata = SerializationMetadata(
            format=format,
            schema_version=SCHEMA_VERSION,
            checksum=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        return SerializedTask(
            job_id=job_id,
            capability=capability,
            priority=priority,
            policy=policy,
            payload=payload or {},
            metadata=metadata,
            data=data,
        )

    def deserialize(self, task: SerializedTask) -> dict:
        if task.metadata.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version '{task.metadata.schema_version}'")

        actual_checksum = hashlib.sha256(task.data).hexdigest()
        if actual_checksum != task.metadata.checksum:
            raise ValueError("checksum mismatch: serialized task data may be corrupted")

        body = self.decode(task.metadata.format, task.data)
        for attr in ("job_id", "capability", "priority", "policy"):
            if body.get(attr) != getattr(task, attr):
                raise ValueError(f"envelope field '{attr}' does not match encoded payload")
        return body


# --- minimal MessagePack codec (subset: nil, bool, int, float, str, bytes, list, dict) ---


def _msgpack_encode(value) -> bytes:
    if value is None:
        return b"\xc0"
    if value is False:
        return b"\xc2"
    if value is True:
        return b"\xc3"
    if isinstance(value, int):
        return _msgpack_encode_int(value)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, str):
        return _msgpack_encode_str(value)
    if isinstance(value, bytes):
        return _msgpack_encode_bytes(value)
    if isinstance(value, (list, tuple)):
        return _msgpack_encode_array(value)
    if isinstance(value, dict):
        return _msgpack_encode_map(value)
    raise TypeError(f"unsupported type for msgpack encoding: {type(value)!r}")


def _msgpack_encode_int(n: int) -> bytes:
    if 0 <= n <= 127:
        return struct.pack("B", n)
    if -32 <= n < 0:
        return struct.pack("b", n)
    return b"\xd3" + struct.pack(">q", n)


def _msgpack_encode_str(value: str) -> bytes:
    encoded = value.encode("utf-8")
    length = len(encoded)
    if length < 32:
        return struct.pack("B", 0xA0 | length) + encoded
    if length < 256:
        return b"\xd9" + struct.pack("B", length) + encoded
    if length < 65536:
        return b"\xda" + struct.pack(">H", length) + encoded
    return b"\xdb" + struct.pack(">I", length) + encoded


def _msgpack_encode_bytes(value: bytes) -> bytes:
    length = len(value)
    if length < 256:
        return b"\xc4" + struct.pack("B", length) + value
    if length < 65536:
        return b"\xc5" + struct.pack(">H", length) + value
    return b"\xc6" + struct.pack(">I", length) + value


def _msgpack_encode_array(items) -> bytes:
    length = len(items)
    if length < 16:
        header = struct.pack("B", 0x90 | length)
    elif length < 65536:
        header = b"\xdc" + struct.pack(">H", length)
    else:
        header = b"\xdd" + struct.pack(">I", length)
    return header + b"".join(_msgpack_encode(item) for item in items)


def _msgpack_encode_map(mapping: dict) -> bytes:
    items = sorted(mapping.items(), key=lambda pair: pair[0])
    length = len(items)
    if length < 16:
        header = struct.pack("B", 0x80 | length)
    elif length < 65536:
        header = b"\xde" + struct.pack(">H", length)
    else:
        header = b"\xdf" + struct.pack(">I", length)
    body = b"".join(_msgpack_encode(key) + _msgpack_encode(val) for key, val in items)
    return header + body


def _msgpack_decode(data: bytes):
    value, offset = _msgpack_decode_at(data, 0)
    if offset != len(data):
        raise ValueError("trailing bytes after msgpack value")
    return value


def _msgpack_decode_at(data: bytes, offset: int):
    prefix = data[offset]
    offset += 1

    if prefix == 0xC0:
        return None, offset
    if prefix == 0xC2:
        return False, offset
    if prefix == 0xC3:
        return True, offset
    if prefix <= 0x7F:
        return prefix, offset
    if prefix >= 0xE0:
        return prefix - 256, offset
    if prefix == 0xD3:
        (value,) = struct.unpack_from(">q", data, offset)
        return value, offset + 8
    if prefix == 0xCB:
        (value,) = struct.unpack_from(">d", data, offset)
        return value, offset + 8
    if 0xA0 <= prefix <= 0xBF:
        length = prefix & 0x1F
        return data[offset : offset + length].decode("utf-8"), offset + length
    if prefix in (0xD9, 0xDA, 0xDB):
        length, offset = _read_length(data, offset, prefix, {0xD9: "B", 0xDA: ">H", 0xDB: ">I"})
        return data[offset : offset + length].decode("utf-8"), offset + length
    if prefix in (0xC4, 0xC5, 0xC6):
        length, offset = _read_length(data, offset, prefix, {0xC4: "B", 0xC5: ">H", 0xC6: ">I"})
        return data[offset : offset + length], offset + length
    if 0x90 <= prefix <= 0x9F:
        return _msgpack_decode_array(data, offset, prefix & 0x0F)
    if prefix in (0xDC, 0xDD):
        length, offset = _read_length(data, offset, prefix, {0xDC: ">H", 0xDD: ">I"})
        return _msgpack_decode_array(data, offset, length)
    if 0x80 <= prefix <= 0x8F:
        return _msgpack_decode_map(data, offset, prefix & 0x0F)
    if prefix in (0xDE, 0xDF):
        length, offset = _read_length(data, offset, prefix, {0xDE: ">H", 0xDF: ">I"})
        return _msgpack_decode_map(data, offset, length)
    raise ValueError(f"unsupported msgpack prefix byte: {prefix:#x}")


def _read_length(data: bytes, offset: int, prefix: int, formats: dict) -> tuple:
    fmt = formats[prefix]
    size = struct.calcsize(fmt)
    (length,) = struct.unpack_from(fmt, data, offset)
    return length, offset + size


def _msgpack_decode_array(data: bytes, offset: int, length: int):
    items = []
    for _ in range(length):
        value, offset = _msgpack_decode_at(data, offset)
        items.append(value)
    return items, offset


def _msgpack_decode_map(data: bytes, offset: int, length: int):
    result = {}
    for _ in range(length):
        key, offset = _msgpack_decode_at(data, offset)
        value, offset = _msgpack_decode_at(data, offset)
        result[key] = value
    return result, offset


_task_serialization_engine = TaskSerializationEngine()


def get_task_serialization_engine() -> TaskSerializationEngine:
    return _task_serialization_engine


router = APIRouter(prefix="/cluster/tasks", tags=["task-serializer"])


@router.post("/serialize")
def serialize_endpoint(payload: dict) -> dict:
    try:
        task = _task_serialization_engine.serialize(
            job_id=payload["job_id"],
            capability=payload["capability"],
            payload=payload.get("payload", {}),
            priority=payload.get("priority", 0),
            policy=payload.get("policy", "least_loaded"),
            format=payload.get("format", "json"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return task.to_dict()


@router.post("/deserialize")
def deserialize_endpoint(payload: dict) -> dict:
    try:
        task = SerializedTask.from_dict(payload)
        body = _task_serialization_engine.deserialize(task)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return body


@router.get("/formats")
def list_formats_endpoint() -> list:
    return list(SUPPORTED_FORMATS)
