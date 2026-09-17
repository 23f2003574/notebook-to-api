from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .authentication import AuthenticationManager, UnknownUserError, get_authentication_manager

_KEY_PREFIX = "ntbk"


def _new_id() -> str:
    return uuid.uuid4().hex


def _generate_secret() -> str:
    return f"{_KEY_PREFIX}_{secrets.token_hex(24)}"


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class UnknownAPIKeyError(KeyError):
    pass


class APIKeyExpiredError(ValueError):
    pass


class APIKeyRevokedError(ValueError):
    pass


@dataclass(frozen=True)
class APIKeyMetadata:
    """Descriptive, non-secret information about an API key."""

    key_id: str
    user_id: str
    name: str
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked: bool = False
    revoked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "user_id": self.user_id,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }


@dataclass(frozen=True)
class APIKey:
    """A freshly generated API key. The plaintext secret is only ever available here."""

    metadata: APIKeyMetadata
    secret: str

    def to_dict(self) -> dict:
        return {**self.metadata.to_dict(), "secret": self.secret}


class APIKeyManager:
    """Creates, validates, lists, and revokes API keys for programmatic clients."""

    def __init__(self, authentication_manager: Optional[AuthenticationManager] = None) -> None:
        self._authentication_manager = authentication_manager or get_authentication_manager()
        self._keys: dict[str, APIKeyMetadata] = {}
        self._hash_to_id: dict[str, str] = {}
        self._lock = Lock()

    def create(
        self,
        user_id: str,
        name: str,
        *,
        expires_at: Optional[datetime] = None,
        timestamp: Optional[datetime] = None,
    ) -> APIKey:
        self._authentication_manager.get_user(user_id)
        secret = _generate_secret()
        metadata = APIKeyMetadata(
            key_id=_new_id(),
            user_id=user_id,
            name=name,
            created_at=timestamp or datetime.now(timezone.utc),
            expires_at=expires_at,
        )
        with self._lock:
            self._keys[metadata.key_id] = metadata
            self._hash_to_id[_hash_secret(secret)] = metadata.key_id
        return APIKey(metadata=metadata, secret=secret)

    def validate(self, secret: str, *, timestamp: Optional[datetime] = None) -> APIKeyMetadata:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            key_id = self._hash_to_id.get(_hash_secret(secret))
            if key_id is None:
                raise UnknownAPIKeyError("unknown API key")
            metadata = self._keys[key_id]
        if metadata.revoked:
            raise APIKeyRevokedError(f"API key '{metadata.key_id}' has been revoked")
        if metadata.expires_at is not None and now >= metadata.expires_at:
            raise APIKeyExpiredError(f"API key '{metadata.key_id}' has expired")
        return metadata

    def revoke(self, key_id: str, *, timestamp: Optional[datetime] = None) -> APIKeyMetadata:
        with self._lock:
            metadata = self._keys.get(key_id)
            if metadata is None:
                raise UnknownAPIKeyError(key_id)
            updated = replace(
                metadata, revoked=True, revoked_at=timestamp or datetime.now(timezone.utc)
            )
            self._keys[key_id] = updated
        return updated

    def list_keys(self, user_id: Optional[str] = None) -> list:
        with self._lock:
            values = list(self._keys.values())
        if user_id is not None:
            values = [key for key in values if key.user_id == user_id]
        return values


_api_key_manager = APIKeyManager()


def get_api_key_manager() -> APIKeyManager:
    return _api_key_manager


router = APIRouter(prefix="/security", tags=["security-api-keys"])


@router.post("/api-keys")
def create_api_key(payload: dict = Body(default={})) -> dict:
    user_id = payload.get("user_id", "")
    name = payload.get("name", "")
    expires_at = payload.get("expires_at")
    expires_dt = datetime.fromisoformat(expires_at) if expires_at else None
    try:
        api_key = get_api_key_manager().create(user_id, name, expires_at=expires_dt)
    except UnknownUserError:
        raise HTTPException(status_code=404, detail="unknown user")
    return api_key.to_dict()


@router.get("/api-keys")
def list_api_keys(user_id: Optional[str] = Query(default=None)) -> list:
    return [key.to_dict() for key in get_api_key_manager().list_keys(user_id)]


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str) -> dict:
    try:
        metadata = get_api_key_manager().revoke(key_id)
    except UnknownAPIKeyError:
        raise HTTPException(status_code=404, detail="unknown API key")
    return metadata.to_dict()
