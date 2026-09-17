from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException

SECRET_TYPES = ("API Keys", "JWT Secrets", "OAuth Credentials", "Encryption Keys")


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(output[:length])


def _encrypt(key: bytes, plaintext: bytes) -> tuple:
    nonce = os.urandom(16)
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, _keystream(key, nonce, len(plaintext))))
    return nonce, ciphertext


def _decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(ciphertext, _keystream(key, nonce, len(ciphertext))))


class InvalidSecretTypeError(ValueError):
    pass


class SecretAlreadyExistsError(ValueError):
    pass


class UnknownSecretError(KeyError):
    pass


@dataclass(frozen=True)
class SecretMetadata:
    """Descriptive, non-secret information about a vaulted secret."""

    name: str
    secret_type: str
    version: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    access_count: int = 0
    last_accessed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "secret_type": self.secret_type,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
        }


@dataclass(frozen=True)
class SecretEntry:
    """A decrypted secret value paired with its metadata."""

    metadata: SecretMetadata
    value: str

    def to_dict(self) -> dict:
        return {**self.metadata.to_dict(), "value": self.value}


class SecretVaultService:
    """Stores secrets encrypted at rest, versioned, with rotation hooks and access auditing."""

    def __init__(self, *, encryption_key: Optional[bytes] = None) -> None:
        self._encryption_key = encryption_key or os.urandom(32)
        self._secrets: dict[str, dict] = {}
        self._access_log: dict[str, list] = {}
        self._rotation_hooks: dict[str, list] = {}
        self._lock = Lock()

    def _validate_type(self, secret_type: str) -> None:
        if secret_type not in SECRET_TYPES:
            raise InvalidSecretTypeError(f"unknown secret type '{secret_type}'")

    def _record_access(self, name: str, action: str, *, timestamp: datetime) -> None:
        with self._lock:
            self._access_log.setdefault(name, []).append({"action": action, "timestamp": timestamp})

    def store(
        self, name: str, secret_type: str, value: str, *, timestamp: Optional[datetime] = None
    ) -> SecretEntry:
        self._validate_type(secret_type)
        now = timestamp or datetime.now(timezone.utc)
        nonce, ciphertext = _encrypt(self._encryption_key, value.encode("utf-8"))
        with self._lock:
            if name in self._secrets:
                raise SecretAlreadyExistsError(f"secret '{name}' already exists")
            self._secrets[name] = {
                "secret_type": secret_type,
                "versions": [{"version": 1, "nonce": nonce, "ciphertext": ciphertext, "created_at": now}],
            }
        self._record_access(name, "store", timestamp=now)
        metadata = SecretMetadata(
            name=name, secret_type=secret_type, version=1, created_at=now, updated_at=now
        )
        return SecretEntry(metadata=metadata, value=value)

    def retrieve(self, name: str, *, timestamp: Optional[datetime] = None) -> SecretEntry:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            record = self._secrets.get(name)
        if record is None:
            raise UnknownSecretError(name)

        latest = record["versions"][-1]
        plaintext = _decrypt(self._encryption_key, latest["nonce"], latest["ciphertext"]).decode("utf-8")
        self._record_access(name, "retrieve", timestamp=now)
        with self._lock:
            access_count = sum(1 for entry in self._access_log[name] if entry["action"] == "retrieve")
        metadata = SecretMetadata(
            name=name,
            secret_type=record["secret_type"],
            version=latest["version"],
            created_at=record["versions"][0]["created_at"],
            updated_at=latest["created_at"],
            access_count=access_count,
            last_accessed_at=now,
        )
        return SecretEntry(metadata=metadata, value=plaintext)

    def register_rotation_hook(self, name: str, hook: Callable[[SecretEntry], None]) -> None:
        with self._lock:
            self._rotation_hooks.setdefault(name, []).append(hook)

    def rotate(self, name: str, new_value: str, *, timestamp: Optional[datetime] = None) -> SecretEntry:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            record = self._secrets.get(name)
            if record is None:
                raise UnknownSecretError(name)
            nonce, ciphertext = _encrypt(self._encryption_key, new_value.encode("utf-8"))
            new_version = record["versions"][-1]["version"] + 1
            record["versions"].append(
                {"version": new_version, "nonce": nonce, "ciphertext": ciphertext, "created_at": now}
            )
            secret_type = record["secret_type"]
            first_created_at = record["versions"][0]["created_at"]
            hooks = list(self._rotation_hooks.get(name, ()))

        self._record_access(name, "rotate", timestamp=now)
        metadata = SecretMetadata(
            name=name,
            secret_type=secret_type,
            version=new_version,
            created_at=first_created_at,
            updated_at=now,
        )
        entry = SecretEntry(metadata=metadata, value=new_value)
        for hook in hooks:
            hook(entry)
        return entry

    def destroy(self, name: str, *, timestamp: Optional[datetime] = None) -> None:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            if name not in self._secrets:
                raise UnknownSecretError(name)
            del self._secrets[name]
            self._rotation_hooks.pop(name, None)
        self._record_access(name, "destroy", timestamp=now)

    def access_log(self, name: str) -> list:
        with self._lock:
            return list(self._access_log.get(name, []))


_secret_vault_service = SecretVaultService()


def get_secret_vault_service() -> SecretVaultService:
    return _secret_vault_service


router = APIRouter(prefix="/security/secrets", tags=["security-secret-vault"])


@router.post("")
def store_secret_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        entry = get_secret_vault_service().store(
            payload.get("name", ""), payload.get("secret_type", ""), payload.get("value", "")
        )
    except InvalidSecretTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SecretAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return entry.to_dict()


@router.get("/{secret}")
def retrieve_secret_endpoint(secret: str) -> dict:
    try:
        entry = get_secret_vault_service().retrieve(secret)
    except UnknownSecretError:
        raise HTTPException(status_code=404, detail="unknown secret")
    return entry.to_dict()


@router.post("/{secret}/rotate")
def rotate_secret_endpoint(secret: str, payload: dict = Body(default={})) -> dict:
    try:
        entry = get_secret_vault_service().rotate(secret, payload.get("value", ""))
    except UnknownSecretError:
        raise HTTPException(status_code=404, detail="unknown secret")
    return entry.to_dict()


@router.delete("/{secret}")
def destroy_secret_endpoint(secret: str) -> dict:
    try:
        get_secret_vault_service().destroy(secret)
    except UnknownSecretError:
        raise HTTPException(status_code=404, detail="unknown secret")
    return {"success": True}
