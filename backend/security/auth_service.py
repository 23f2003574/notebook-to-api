from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException

from .identity_registry import Identity, IdentityRegistry, get_identity_registry

_HASH_ITERATIONS = 100_000

AUTH_TYPES = (
    "Username/Password",
    "API Client",
    "OAuth Provider",
    "Service Account",
)

_SERVICE_AUTH_TYPES = ("API Client", "Service Account")

DEFAULT_MAX_FAILED_ATTEMPTS = 5


def _new_id() -> str:
    return uuid.uuid4().hex


def _hash_secret(secret: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, _HASH_ITERATIONS).hex()


class UnknownProviderError(ValueError):
    pass


class UnknownSessionError(KeyError):
    pass


class AccountLockedError(ValueError):
    pass


class InvalidAuthenticationTypeError(ValueError):
    pass


@dataclass(frozen=True)
class AuthenticationRequest:
    """A request to authenticate a principal against a pluggable provider."""

    auth_type: str
    identifier: str
    secret: str
    metadata: dict


@dataclass(frozen=True)
class AuthenticationResult:
    """The outcome of an authentication attempt."""

    success: bool
    auth_type: Optional[str] = None
    identity_id: Optional[str] = None
    session_token: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "auth_type": self.auth_type,
            "identity_id": self.identity_id,
            "session_token": self.session_token,
            "message": self.message,
        }


class CredentialProvider:
    """Verifies a secret enrolled directly with this provider (password, API key, ...)."""

    def __init__(self, identity_type: str) -> None:
        self.identity_type = identity_type
        self._credentials: dict[str, tuple] = {}
        self._lock = Lock()

    def enroll(self, identifier: str, secret: str) -> None:
        salt = os.urandom(16)
        with self._lock:
            self._credentials[identifier] = (salt, _hash_secret(secret, salt))

    def verify(self, identifier: str, secret: str) -> bool:
        with self._lock:
            record = self._credentials.get(identifier)
        if record is None:
            return False
        salt, expected_hash = record
        return _hash_secret(secret, salt) == expected_hash


class OAuthProvider:
    """Verifies externally-issued tokens via a pluggable callback, standing in for a real IdP."""

    identity_type = "user"

    def __init__(self, token_verifier: Optional[Callable[[str, str], bool]] = None) -> None:
        self._token_verifier = token_verifier or (lambda identifier, token: bool(token))

    def enroll(self, identifier: str, secret: str) -> None:
        pass

    def verify(self, identifier: str, secret: str) -> bool:
        return self._token_verifier(identifier, secret)


class AuthenticationService:
    """Verifies user and service identity across pluggable providers and issues sessions."""

    def __init__(
        self,
        identity_registry: Optional[IdentityRegistry] = None,
        *,
        max_failed_attempts: int = DEFAULT_MAX_FAILED_ATTEMPTS,
    ) -> None:
        self._identity_registry = identity_registry or get_identity_registry()
        self._max_failed_attempts = max_failed_attempts
        self._providers: dict[str, object] = {
            "Username/Password": CredentialProvider("user"),
            "API Client": CredentialProvider("api_client"),
            "Service Account": CredentialProvider("service_account"),
            "OAuth Provider": OAuthProvider(),
        }
        self._sessions: dict[str, dict] = {}
        self._failed_attempts: dict[str, int] = {}
        self._lock = Lock()

    def register_provider(self, auth_type: str, provider: object) -> None:
        with self._lock:
            self._providers[auth_type] = provider

    def _require_provider(self, auth_type: str):
        provider = self._providers.get(auth_type)
        if provider is None:
            raise UnknownProviderError(f"unknown auth provider '{auth_type}'")
        return provider

    def enroll_credentials(self, auth_type: str, identifier: str, secret: str) -> None:
        self._require_provider(auth_type).enroll(identifier, secret)

    def providers(self) -> list:
        return [
            {"auth_type": auth_type, "identity_type": provider.identity_type}
            for auth_type, provider in sorted(self._providers.items())
        ]

    def verify_credentials(self, auth_type: str, identifier: str, secret: str) -> bool:
        return self._require_provider(auth_type).verify(identifier, secret)

    def authenticate(
        self, request: AuthenticationRequest, *, timestamp: Optional[datetime] = None
    ) -> AuthenticationResult:
        provider = self._require_provider(request.auth_type)

        with self._lock:
            if self._failed_attempts.get(request.identifier, 0) >= self._max_failed_attempts:
                raise AccountLockedError(request.identifier)

        if not provider.verify(request.identifier, request.secret):
            with self._lock:
                self._failed_attempts[request.identifier] = (
                    self._failed_attempts.get(request.identifier, 0) + 1
                )
            return AuthenticationResult(
                success=False, auth_type=request.auth_type, message="invalid credentials"
            )

        with self._lock:
            self._failed_attempts.pop(request.identifier, None)

        identity = self._identity_registry.find_by_display_name(request.identifier)
        if identity is None:
            identity = self._identity_registry.register_identity(
                request.identifier,
                provider.identity_type,
                attributes=dict(request.metadata or {}),
                timestamp=timestamp,
            )

        token = _new_id()
        with self._lock:
            self._sessions[token] = {
                "identity_id": identity.identity_id,
                "auth_type": request.auth_type,
            }
        return AuthenticationResult(
            success=True,
            auth_type=request.auth_type,
            identity_id=identity.identity_id,
            session_token=token,
        )

    def authenticate_service(
        self,
        client_id: str,
        client_secret: str,
        *,
        auth_type: str = "API Client",
        timestamp: Optional[datetime] = None,
    ) -> AuthenticationResult:
        if auth_type not in _SERVICE_AUTH_TYPES:
            raise InvalidAuthenticationTypeError(
                f"'{auth_type}' is not a service authentication type"
            )
        request = AuthenticationRequest(
            auth_type=auth_type, identifier=client_id, secret=client_secret, metadata={}
        )
        return self.authenticate(request, timestamp=timestamp)

    def failed_attempts(self, identifier: str) -> int:
        with self._lock:
            return self._failed_attempts.get(identifier, 0)

    def logout(self, session_token: str) -> None:
        with self._lock:
            if session_token not in self._sessions:
                raise UnknownSessionError(session_token)
            del self._sessions[session_token]

    def verify_session(self, session_token: str) -> dict:
        with self._lock:
            session = self._sessions.get(session_token)
        if session is None:
            raise UnknownSessionError(session_token)
        return session


_authentication_service = AuthenticationService()


def get_authentication_service() -> AuthenticationService:
    return _authentication_service


router = APIRouter(prefix="/security/auth", tags=["security-auth-service"])


@router.post("/login")
def login_endpoint(payload: dict = Body(default={})) -> dict:
    request = AuthenticationRequest(
        auth_type=payload.get("auth_type", ""),
        identifier=payload.get("identifier", ""),
        secret=payload.get("secret", ""),
        metadata=payload.get("metadata", {}),
    )
    try:
        result = get_authentication_service().authenticate(request)
    except UnknownProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except AccountLockedError as exc:
        raise HTTPException(status_code=423, detail=f"account '{exc}' is locked")
    if not result.success:
        raise HTTPException(status_code=401, detail=result.message)
    return result.to_dict()


@router.post("/logout")
def logout_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        get_authentication_service().logout(payload.get("session_token", ""))
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"success": True}


@router.post("/verify")
def verify_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        session = get_authentication_service().verify_session(payload.get("session_token", ""))
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"valid": True, **session}


@router.get("/providers")
def list_providers_endpoint() -> list:
    return get_authentication_service().providers()
