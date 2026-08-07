from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownIdentityError(KeyError):
    pass


class IdentityAlreadyExistsError(ValueError):
    pass


@dataclass(frozen=True)
class IdentityMetadata:
    """Descriptive information used to index and resolve an identity."""

    display_name: str
    identity_type: str
    attributes: dict
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "display_name": self.display_name,
            "identity_type": self.identity_type,
            "attributes": self.attributes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True)
class Identity:
    """A registered identity paired with its indexing metadata."""

    identity_id: str
    metadata: IdentityMetadata

    def to_dict(self) -> dict:
        return {"identity_id": self.identity_id, **self.metadata.to_dict()}


class IdentityRegistry:
    """Registers identities and resolves them by id or indexed metadata."""

    def __init__(self) -> None:
        self._identities: dict[str, IdentityMetadata] = {}
        self._by_display_name: dict[str, str] = {}
        self._lock = Lock()

    def register_identity(
        self,
        display_name: str,
        identity_type: str,
        *,
        attributes: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> Identity:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            if display_name in self._by_display_name:
                raise IdentityAlreadyExistsError(
                    f"identity with display name '{display_name}' already exists"
                )
            identity_id = _new_id()
            metadata = IdentityMetadata(
                display_name=display_name,
                identity_type=identity_type,
                attributes=dict(attributes or {}),
                created_at=now,
                updated_at=now,
            )
            self._identities[identity_id] = metadata
            self._by_display_name[display_name] = identity_id
        return Identity(identity_id=identity_id, metadata=metadata)

    def lookup(self, identity_id: str) -> Identity:
        with self._lock:
            metadata = self._identities.get(identity_id)
        if metadata is None:
            raise UnknownIdentityError(identity_id)
        return Identity(identity_id=identity_id, metadata=metadata)

    def list_identities(self) -> list:
        with self._lock:
            return [
                Identity(identity_id=identity_id, metadata=metadata)
                for identity_id, metadata in self._identities.items()
            ]

    def update(
        self,
        identity_id: str,
        *,
        attributes: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> Identity:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            metadata = self._identities.get(identity_id)
            if metadata is None:
                raise UnknownIdentityError(identity_id)
            merged_attributes = {**metadata.attributes, **(attributes or {})}
            updated = replace(metadata, attributes=merged_attributes, updated_at=now)
            self._identities[identity_id] = updated
        return Identity(identity_id=identity_id, metadata=updated)

    def remove(self, identity_id: str) -> None:
        with self._lock:
            metadata = self._identities.pop(identity_id, None)
            if metadata is None:
                raise UnknownIdentityError(identity_id)
            self._by_display_name.pop(metadata.display_name, None)


_identity_registry = IdentityRegistry()


def get_identity_registry() -> IdentityRegistry:
    return _identity_registry


router = APIRouter(prefix="/security", tags=["security-identities"])


@router.post("/identities")
def register_identity_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        identity = get_identity_registry().register_identity(
            payload.get("display_name", ""),
            payload.get("identity_type", ""),
            attributes=payload.get("attributes"),
        )
    except IdentityAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return identity.to_dict()


@router.get("/identities")
def list_identities_endpoint() -> list:
    return [identity.to_dict() for identity in get_identity_registry().list_identities()]


@router.get("/identities/{identity_id}")
def lookup_identity_endpoint(identity_id: str) -> dict:
    try:
        identity = get_identity_registry().lookup(identity_id)
    except UnknownIdentityError:
        raise HTTPException(status_code=404, detail="unknown identity")
    return identity.to_dict()


@router.delete("/identities/{identity_id}")
def remove_identity_endpoint(identity_id: str) -> dict:
    try:
        get_identity_registry().remove(identity_id)
    except UnknownIdentityError:
        raise HTTPException(status_code=404, detail="unknown identity")
    return {"success": True}
