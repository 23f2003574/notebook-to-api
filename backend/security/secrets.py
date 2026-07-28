from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .permissions import PermissionEngine, get_permission_engine
from .security_policy import SecurityPolicyEngine, get_security_policy_engine

SECRET_TYPES = (
    "API Keys",
    "JWT Signing Keys",
    "Database Credentials",
    "OAuth Secrets",
    "Encryption Keys",
)


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


class SecretExpiredError(ValueError):
    pass


class AccessDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class SecretMetadata:
    """Descriptive, non-secret information about a stored secret."""

    name: str
    secret_type: str
    version: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    rotation_compliant: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "secret_type": self.secret_type,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "rotation_compliant": self.rotation_compliant,
        }


@dataclass(frozen=True)
class Secret:
    """A decrypted secret value paired with its metadata."""

    metadata: SecretMetadata
    value: str

    def to_dict(self) -> dict:
        return {**self.metadata.to_dict(), "value": self.value}


class SecretManagementService:
    """Stores secrets encrypted at rest and manages their lifecycle."""

    def __init__(
        self,
        permission_engine: Optional[PermissionEngine] = None,
        security_policy_engine: Optional[SecurityPolicyEngine] = None,
        *,
        encryption_key: Optional[bytes] = None,
    ) -> None:
        self._permission_engine = permission_engine or get_permission_engine()
        self._security_policy_engine = security_policy_engine or get_security_policy_engine()
        self._encryption_key = encryption_key or os.urandom(32)
        self._secrets: dict[str, dict] = {}
        self._lock = Lock()

    def _validate_type(self, secret_type: str) -> None:
        if secret_type not in SECRET_TYPES:
            raise InvalidSecretTypeError(f"unknown secret type '{secret_type}'")

    def store(
        self,
        name: str,
        secret_type: str,
        value: str,
        *,
        expires_at: Optional[datetime] = None,
        timestamp: Optional[datetime] = None,
    ) -> Secret:
        self._validate_type(secret_type)
        now = timestamp or datetime.now(timezone.utc)
        nonce, ciphertext = _encrypt(self._encryption_key, value.encode("utf-8"))
        with self._lock:
            if name in self._secrets:
                raise SecretAlreadyExistsError(f"secret '{name}' already exists")
            self._secrets[name] = {
                "secret_type": secret_type,
                "expires_at": expires_at,
                "versions": [{"version": 1, "nonce": nonce, "ciphertext": ciphertext, "created_at": now}],
            }
        metadata = SecretMetadata(
            name=name,
            secret_type=secret_type,
            version=1,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        return Secret(metadata=metadata, value=value)

    def retrieve(
        self, name: str, *, user_id: Optional[str] = None, timestamp: Optional[datetime] = None
    ) -> Secret:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            record = self._secrets.get(name)
        if record is None:
            raise UnknownSecretError(name)
        if record["expires_at"] is not None and now >= record["expires_at"]:
            raise SecretExpiredError(name)
        if user_id is not None and not self._permission_engine.check(user_id, f"secret:{name}", "Read"):
            raise AccessDeniedError(f"user '{user_id}' cannot read secret '{name}'")

        latest = record["versions"][-1]
        plaintext = _decrypt(self._encryption_key, latest["nonce"], latest["ciphertext"]).decode("utf-8")
        metadata = SecretMetadata(
            name=name,
            secret_type=record["secret_type"],
            version=latest["version"],
            created_at=record["versions"][0]["created_at"],
            updated_at=latest["created_at"],
            expires_at=record["expires_at"],
        )
        return Secret(metadata=metadata, value=plaintext)

    def rotate(self, name: str, new_value: str, *, timestamp: Optional[datetime] = None) -> Secret:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            record = self._secrets.get(name)
            if record is None:
                raise UnknownSecretError(name)
            previous = record["versions"][-1]
            age_days = (now - previous["created_at"]).days
            nonce, ciphertext = _encrypt(self._encryption_key, new_value.encode("utf-8"))
            new_version = previous["version"] + 1
            record["versions"].append(
                {"version": new_version, "nonce": nonce, "ciphertext": ciphertext, "created_at": now}
            )
            first_created_at = record["versions"][0]["created_at"]
            secret_type = record["secret_type"]
            expires_at = record["expires_at"]

        compliance = self._security_policy_engine.evaluate_if_registered(
            "API Key Rotation", {"age_days": age_days}, timestamp=now
        )
        metadata = SecretMetadata(
            name=name,
            secret_type=secret_type,
            version=new_version,
            created_at=first_created_at,
            updated_at=now,
            expires_at=expires_at,
            rotation_compliant=compliance.passed if compliance else None,
        )
        return Secret(metadata=metadata, value=new_value)

    def delete(self, name: str) -> None:
        with self._lock:
            if name not in self._secrets:
                raise UnknownSecretError(name)
            del self._secrets[name]


_secret_management_service = SecretManagementService()


def get_secret_management_service() -> SecretManagementService:
    return _secret_management_service


router = APIRouter(prefix="/security", tags=["security-secrets"])


@router.post("/secrets")
def store_secret_endpoint(payload: dict = Body(default={})) -> dict:
    expires_at = payload.get("expires_at")
    try:
        secret = get_secret_management_service().store(
            payload.get("name", ""),
            payload.get("secret_type", ""),
            payload.get("value", ""),
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        )
    except InvalidSecretTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SecretAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return secret.to_dict()


@router.get("/secrets/{secret}")
def retrieve_secret_endpoint(secret: str, user_id: Optional[str] = Query(default=None)) -> dict:
    try:
        result = get_secret_management_service().retrieve(secret, user_id=user_id)
    except UnknownSecretError:
        raise HTTPException(status_code=404, detail="unknown secret")
    except SecretExpiredError:
        raise HTTPException(status_code=410, detail="secret has expired")
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return result.to_dict()


@router.post("/secrets/{secret}/rotate")
def rotate_secret_endpoint(secret: str, payload: dict = Body(default={})) -> dict:
    try:
        result = get_secret_management_service().rotate(secret, payload.get("value", ""))
    except UnknownSecretError:
        raise HTTPException(status_code=404, detail="unknown secret")
    return result.to_dict()


@router.delete("/secrets/{secret}")
def delete_secret_endpoint(secret: str) -> dict:
    try:
        get_secret_management_service().delete(secret)
    except UnknownSecretError:
        raise HTTPException(status_code=404, detail="unknown secret")
    return {"success": True}
