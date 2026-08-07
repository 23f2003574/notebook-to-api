from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException

from .permission_engine import PermissionEngine, get_permission_engine
from .secret_vault import SecretVaultService, UnknownSecretError as UnknownVaultSecretError, get_secret_vault_service

_KEY_PREFIX = "ntbkv2"

KEY_TYPES = ("User", "Service", "Read-Only", "Temporary")


def _new_id() -> str:
    return uuid.uuid4().hex


def _generate_secret() -> str:
    return f"{_KEY_PREFIX}_{secrets.token_hex(24)}"


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class InvalidKeyTypeError(ValueError):
    pass


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
    identity: str
    name: str
    key_type: str
    scopes: tuple = ()
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked: bool = False
    revoked_at: Optional[datetime] = None
    rotated_from: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "identity": self.identity,
            "name": self.name,
            "key_type": self.key_type,
            "scopes": list(self.scopes),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "rotated_from": self.rotated_from,
        }


@dataclass(frozen=True)
class APIKey:
    """A freshly generated API key. The plaintext secret is only ever available here."""

    metadata: APIKeyMetadata
    secret: str

    def to_dict(self) -> dict:
        return {**self.metadata.to_dict(), "secret": self.secret}


class APIKeyManager:
    """Issues, rotates, validates, and revokes API keys with scoped permissions."""

    def __init__(
        self,
        permission_engine: Optional[PermissionEngine] = None,
        secret_vault: Optional[SecretVaultService] = None,
    ) -> None:
        self._permission_engine = permission_engine or get_permission_engine()
        self._secret_vault = secret_vault or get_secret_vault_service()
        self._keys: dict[str, APIKeyMetadata] = {}
        self._hash_to_id: dict[str, str] = {}
        self._lock = Lock()

    def _validate_key_type(self, key_type: str) -> None:
        if key_type not in KEY_TYPES:
            raise InvalidKeyTypeError(f"'{key_type}' is not a known API key type")

    def _grant_scopes(self, key_id: str, scopes: Iterable[str], *, timestamp: datetime) -> None:
        for scope in scopes:
            resource, _, action = scope.partition(":")
            self._permission_engine.grant(key_id, resource, action, timestamp=timestamp)

    def _vault_name(self, key_id: str) -> str:
        return f"api-key:{key_id}"

    def _destroy_vault_entry(self, key_id: str) -> None:
        try:
            self._secret_vault.destroy(self._vault_name(key_id))
        except UnknownVaultSecretError:
            pass

    def create_key(
        self,
        identity: str,
        name: str,
        key_type: str = "User",
        *,
        scopes: Iterable[str] = (),
        expires_at: Optional[datetime] = None,
        timestamp: Optional[datetime] = None,
    ) -> APIKey:
        self._validate_key_type(key_type)
        now = timestamp or datetime.now(timezone.utc)
        scopes = tuple(scopes)
        secret = _generate_secret()
        metadata = APIKeyMetadata(
            key_id=_new_id(),
            identity=identity,
            name=name,
            key_type=key_type,
            scopes=scopes,
            created_at=now,
            expires_at=expires_at,
        )
        with self._lock:
            self._keys[metadata.key_id] = metadata
            self._hash_to_id[_hash_secret(secret)] = metadata.key_id
        self._grant_scopes(metadata.key_id, scopes, timestamp=now)
        self._secret_vault.store(self._vault_name(metadata.key_id), "API Keys", secret, timestamp=now)
        return APIKey(metadata=metadata, secret=secret)

    def validate_key(self, secret: str, *, timestamp: Optional[datetime] = None) -> APIKeyMetadata:
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

    def rotate_key(self, key_id: str, *, timestamp: Optional[datetime] = None) -> APIKey:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            metadata = self._keys.get(key_id)
            if metadata is None:
                raise UnknownAPIKeyError(key_id)
            if metadata.revoked:
                raise APIKeyRevokedError(f"API key '{key_id}' has been revoked")
            self._keys[key_id] = replace(metadata, revoked=True, revoked_at=now)
            for secret_hash in [h for h, kid in self._hash_to_id.items() if kid == key_id]:
                del self._hash_to_id[secret_hash]

        new_secret = _generate_secret()
        new_metadata = APIKeyMetadata(
            key_id=_new_id(),
            identity=metadata.identity,
            name=metadata.name,
            key_type=metadata.key_type,
            scopes=metadata.scopes,
            created_at=now,
            expires_at=metadata.expires_at,
            rotated_from=key_id,
        )
        with self._lock:
            self._keys[new_metadata.key_id] = new_metadata
            self._hash_to_id[_hash_secret(new_secret)] = new_metadata.key_id
        self._grant_scopes(new_metadata.key_id, metadata.scopes, timestamp=now)
        self._permission_engine.revoke_all(key_id)
        self._secret_vault.store(self._vault_name(new_metadata.key_id), "API Keys", new_secret, timestamp=now)
        self._destroy_vault_entry(key_id)
        return APIKey(metadata=new_metadata, secret=new_secret)

    def revoke_key(self, key_id: str, *, timestamp: Optional[datetime] = None) -> APIKeyMetadata:
        with self._lock:
            metadata = self._keys.get(key_id)
            if metadata is None:
                raise UnknownAPIKeyError(key_id)
            updated = replace(metadata, revoked=True, revoked_at=timestamp or datetime.now(timezone.utc))
            self._keys[key_id] = updated
        self._permission_engine.revoke_all(key_id)
        self._destroy_vault_entry(key_id)
        return updated

    def check_scope(
        self, secret: str, resource: str, action: str, *, timestamp: Optional[datetime] = None
    ) -> bool:
        metadata = self.validate_key(secret, timestamp=timestamp)
        return self._permission_engine.check(metadata.key_id, resource, action)


_api_key_manager = APIKeyManager()


def get_api_key_manager() -> APIKeyManager:
    return _api_key_manager


router = APIRouter(prefix="/security/api-keys", tags=["security-api-key-manager"])


@router.post("")
def create_key_endpoint(payload: dict = Body(default={})) -> dict:
    expires_at = payload.get("expires_at")
    try:
        api_key = get_api_key_manager().create_key(
            payload.get("identity", ""),
            payload.get("name", ""),
            payload.get("key_type", "User"),
            scopes=payload.get("scopes", []),
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        )
    except InvalidKeyTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return api_key.to_dict()


@router.post("/{key}/rotate")
def rotate_key_endpoint(key: str) -> dict:
    try:
        api_key = get_api_key_manager().rotate_key(key)
    except UnknownAPIKeyError:
        raise HTTPException(status_code=404, detail="unknown API key")
    except APIKeyRevokedError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    return api_key.to_dict()


@router.delete("/{key}")
def revoke_key_endpoint(key: str) -> dict:
    try:
        metadata = get_api_key_manager().revoke_key(key)
    except UnknownAPIKeyError:
        raise HTTPException(status_code=404, detail="unknown API key")
    return metadata.to_dict()


@router.post("/validate")
def validate_key_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        metadata = get_api_key_manager().validate_key(payload.get("secret", ""))
    except UnknownAPIKeyError:
        raise HTTPException(status_code=404, detail="unknown API key")
    except APIKeyRevokedError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    except APIKeyExpiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return metadata.to_dict()
