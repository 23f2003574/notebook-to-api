from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .object_storage import ObjectStorageEngine, StorageObject, get_object_storage_engine


class UploadMode(str, Enum):
    """The upload strategies a session can be created with."""

    SINGLE = "single"
    MULTIPART = "multipart"
    RESUMABLE = "resumable"
    STREAMING = "streaming"


class UploadStatus(str, Enum):
    """The lifecycle states of an upload session."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class UploadPart:
    """A single uploaded chunk within a multipart/resumable session."""

    part_number: int
    size: int
    checksum: str
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "part_number": self.part_number,
            "size": self.size,
            "checksum": self.checksum,
            "uploaded_at": self.uploaded_at.isoformat(),
        }


@dataclass
class UploadSession:
    """Tracks the progress of an in-flight or completed upload."""

    upload_id: str
    key: str
    content_type: str
    mode: UploadMode
    status: UploadStatus = UploadStatus.PENDING
    parts: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        ordered_parts = [self.parts[number].to_dict() for number in sorted(self.parts)]
        return {
            "upload_id": self.upload_id,
            "key": self.key,
            "content_type": self.content_type,
            "mode": self.mode.value,
            "status": self.status.value,
            "parts": ordered_parts,
            "bytes_received": sum(part.size for part in self.parts.values()),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


_TERMINAL_STATUSES = {UploadStatus.COMPLETED, UploadStatus.ABORTED}


class BlobUploadService:
    """Coordinates single, multipart, resumable, and streaming uploads."""

    def __init__(self, *, object_storage: ObjectStorageEngine) -> None:
        self._object_storage = object_storage
        self._sessions: dict = {}
        self._part_data: dict = {}
        self._lock = Lock()

    def create_upload(
        self,
        key: str,
        *,
        content_type: str = "application/octet-stream",
        mode: UploadMode = UploadMode.MULTIPART,
    ) -> UploadSession:
        if not key:
            raise ValueError("key must be non-empty")

        session = UploadSession(
            upload_id=uuid.uuid4().hex,
            key=key,
            content_type=content_type,
            mode=mode,
        )
        with self._lock:
            self._sessions[session.upload_id] = session
            self._part_data[session.upload_id] = {}
        return session

    def upload_part(
        self,
        upload_id: str,
        part_number: int,
        data: bytes,
        *,
        expected_checksum: Optional[str] = None,
    ) -> UploadPart:
        if part_number < 1:
            raise ValueError("part_number must be >= 1")

        session = self._require_session(upload_id)
        payload = bytes(data)
        if not payload:
            raise ValueError("part data must be non-empty")

        checksum = _checksum(payload)
        if expected_checksum is not None and expected_checksum != checksum:
            raise ValueError(f"checksum mismatch for part {part_number} of upload '{upload_id}'")

        part = UploadPart(part_number=part_number, size=len(payload), checksum=checksum)
        with self._lock:
            if session.status in _TERMINAL_STATUSES:
                raise ValueError(f"upload '{upload_id}' is {session.status.value} and cannot accept parts")
            session.parts[part_number] = part
            session.status = UploadStatus.IN_PROGRESS
            session.updated_at = datetime.now(timezone.utc)
            self._part_data[upload_id][part_number] = payload
        return part

    def complete_upload(self, upload_id: str) -> StorageObject:
        session = self._require_session(upload_id)
        with self._lock:
            if session.status in _TERMINAL_STATUSES:
                raise ValueError(f"upload '{upload_id}' is {session.status.value} and cannot be completed")
            if not session.parts:
                raise ValueError(f"upload '{upload_id}' has no parts to complete")
            part_numbers = sorted(session.parts)
            part_data = self._part_data[upload_id]

        assembled = b"".join(part_data[number] for number in part_numbers)
        stored = self._object_storage.put(session.key, assembled, content_type=session.content_type)

        with self._lock:
            session.status = UploadStatus.COMPLETED
            session.updated_at = datetime.now(timezone.utc)
            self._part_data.pop(upload_id, None)
        return stored

    def abort_upload(self, upload_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(upload_id)
            if session is None:
                return False
            if session.status == UploadStatus.COMPLETED:
                raise ValueError(f"upload '{upload_id}' is already completed and cannot be aborted")
            session.status = UploadStatus.ABORTED
            session.updated_at = datetime.now(timezone.utc)
            self._part_data.pop(upload_id, None)
        return True

    def get_upload(self, upload_id: str) -> Optional[UploadSession]:
        with self._lock:
            return self._sessions.get(upload_id)

    def _require_session(self, upload_id: str) -> UploadSession:
        with self._lock:
            session = self._sessions.get(upload_id)
        if session is None:
            raise KeyError(upload_id)
        return session


_blob_upload_service = BlobUploadService(object_storage=get_object_storage_engine())


def get_blob_upload_service() -> BlobUploadService:
    return _blob_upload_service


router = APIRouter(prefix="/storage/uploads", tags=["blob-upload"])


@router.post("")
def create_upload_endpoint(
    key: str,
    content_type: str = "application/octet-stream",
    mode: UploadMode = UploadMode.MULTIPART,
    service: BlobUploadService = Depends(get_blob_upload_service),
) -> dict:
    try:
        session = service.create_upload(key, content_type=content_type, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return session.to_dict()


@router.put("/{upload_id}/parts/{part_number}")
async def upload_part_endpoint(
    upload_id: str,
    part_number: int,
    request: Request,
    expected_checksum: Optional[str] = None,
    service: BlobUploadService = Depends(get_blob_upload_service),
) -> dict:
    data = await request.body()
    try:
        part = service.upload_part(upload_id, part_number, data, expected_checksum=expected_checksum)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"upload '{upload_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return part.to_dict()


@router.post("/{upload_id}/complete")
def complete_upload_endpoint(
    upload_id: str,
    service: BlobUploadService = Depends(get_blob_upload_service),
) -> dict:
    try:
        stored = service.complete_upload(upload_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"upload '{upload_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return stored.to_dict()


@router.delete("/{upload_id}")
def abort_upload_endpoint(
    upload_id: str,
    service: BlobUploadService = Depends(get_blob_upload_service),
) -> dict:
    try:
        aborted = service.abort_upload(upload_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not aborted:
        raise HTTPException(status_code=404, detail=f"upload '{upload_id}' not found")
    return {"upload_id": upload_id, "aborted": True}
