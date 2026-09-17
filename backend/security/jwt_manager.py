from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .authentication import AuthenticationManager, UnknownUserError, get_authentication_manager

_ALGORITHM = "HS256"

ACCESS_TOKEN_TYPES = ("Access", "Service", "Temporary")
TOKEN_TYPES = ("Access", "Refresh", "Service", "Temporary")

_DEFAULT_TTLS = {
    "Access": timedelta(minutes=15),
    "Refresh": timedelta(days=7),
    "Service": timedelta(hours=1),
    "Temporary": timedelta(minutes=5),
}


def _new_id() -> str:
    return uuid.uuid4().hex


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class InvalidJWTError(ValueError):
    pass


class JWTExpiredError(ValueError):
    pass


class JWTRevokedError(ValueError):
    pass


class UnknownRefreshTokenError(KeyError):
    pass


class InvalidTokenTypeError(ValueError):
    pass


@dataclass(frozen=True)
class AccessToken:
    """A signed, short-lived bearer token (Access, Service, or Temporary)."""

    token: str
    token_type: str
    subject: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    roles: tuple = ()

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "token_type": self.token_type,
            "subject": self.subject,
            "token_id": self.token_id,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "roles": list(self.roles),
        }


@dataclass(frozen=True)
class RefreshToken:
    """An opaque, long-lived token used to mint new access tokens."""

    token: str
    subject: str
    issued_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "subject": self.subject,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class JWTTokenManager:
    """Issues, validates, rotates, and revokes JWT access and refresh tokens."""

    def __init__(
        self,
        authentication_manager: Optional[AuthenticationManager] = None,
        *,
        secret_key: Optional[str] = None,
        ttls: Optional[dict] = None,
    ) -> None:
        self._authentication_manager = authentication_manager or get_authentication_manager()
        self._secret_key = (secret_key or secrets.token_hex(32)).encode("utf-8")
        self._ttls = {**_DEFAULT_TTLS, **(ttls or {})}
        self._revoked: set = set()
        self._refresh_tokens: dict = {}
        self._lock = Lock()

    def ttl_for(self, token_type: str) -> timedelta:
        return self._ttls[token_type]

    def _sign(self, header: dict, payload: dict) -> str:
        header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = hmac.new(self._secret_key, signing_input, hashlib.sha256).digest()
        return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"

    def _issue_access_token(
        self, subject: str, token_type: str, *, timestamp: datetime, roles: Iterable[str] = ()
    ) -> AccessToken:
        if token_type not in ACCESS_TOKEN_TYPES:
            raise InvalidTokenTypeError(f"'{token_type}' is not an issuable access token type")
        expires_at = timestamp + self._ttls[token_type]
        token_id = _new_id()
        roles = tuple(roles)
        payload = {
            "sub": subject,
            "jti": token_id,
            "typ": token_type,
            "iat": int(timestamp.timestamp()),
            "exp": int(expires_at.timestamp()),
            "roles": list(roles),
        }
        token = self._sign({"alg": _ALGORITHM, "typ": "JWT"}, payload)
        return AccessToken(
            token=token,
            token_type=token_type,
            subject=subject,
            token_id=token_id,
            issued_at=timestamp,
            expires_at=expires_at,
            roles=roles,
        )

    def _issue_refresh_token(self, subject: str, *, timestamp: datetime) -> RefreshToken:
        token = _new_id()
        expires_at = timestamp + self._ttls["Refresh"]
        with self._lock:
            self._refresh_tokens[token] = {
                "subject": subject,
                "issued_at": timestamp,
                "expires_at": expires_at,
            }
        return RefreshToken(token=token, subject=subject, issued_at=timestamp, expires_at=expires_at)

    def issue(
        self,
        subject: str,
        token_type: str = "Access",
        *,
        roles: Iterable[str] = (),
        timestamp: Optional[datetime] = None,
    ) -> tuple:
        now = timestamp or datetime.now(timezone.utc)
        if token_type == "Access" and not self._authentication_manager.user_exists(subject):
            raise UnknownUserError(subject)

        access = self._issue_access_token(subject, token_type, timestamp=now, roles=roles)
        refresh = self._issue_refresh_token(subject, timestamp=now) if token_type == "Access" else None
        return access, refresh

    def _decode(
        self, token: str, *, check_expiration: bool, timestamp: Optional[datetime] = None
    ) -> AccessToken:
        now = timestamp or datetime.now(timezone.utc)
        try:
            header_segment, payload_segment, signature_segment = token.split(".")
            expected_signature = hmac.new(
                self._secret_key,
                f"{header_segment}.{payload_segment}".encode("ascii"),
                hashlib.sha256,
            ).digest()
            actual_signature = _b64url_decode(signature_segment)
            payload = json.loads(_b64url_decode(payload_segment))
        except (ValueError, KeyError):
            raise InvalidJWTError("malformed token")

        if not hmac.compare_digest(expected_signature, actual_signature):
            raise InvalidJWTError("invalid signature")

        try:
            token_id = payload["jti"]
            token_type = payload["typ"]
            subject = payload["sub"]
            issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
            expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
            roles = tuple(payload.get("roles", ()))
        except (KeyError, TypeError, OSError, OverflowError):
            raise InvalidJWTError("malformed token")

        with self._lock:
            revoked = token_id in self._revoked
        if revoked:
            raise JWTRevokedError(f"token '{token_id}' has been revoked")
        if check_expiration and now >= expires_at:
            raise JWTExpiredError("token has expired")

        return AccessToken(
            token=token,
            token_type=token_type,
            subject=subject,
            token_id=token_id,
            issued_at=issued_at,
            expires_at=expires_at,
            roles=roles,
        )

    def validate(self, token: str, *, timestamp: Optional[datetime] = None) -> AccessToken:
        return self._decode(token, check_expiration=True, timestamp=timestamp)

    def refresh(self, refresh_token: str, *, timestamp: Optional[datetime] = None) -> tuple:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            record = self._refresh_tokens.get(refresh_token)
        if record is None:
            raise UnknownRefreshTokenError(refresh_token)
        if now >= record["expires_at"]:
            with self._lock:
                self._refresh_tokens.pop(refresh_token, None)
            raise JWTExpiredError("refresh token has expired")

        subject = record["subject"]
        access = self._issue_access_token(subject, "Access", timestamp=now)
        new_refresh = self._issue_refresh_token(subject, timestamp=now)
        with self._lock:
            self._refresh_tokens.pop(refresh_token, None)
        return access, new_refresh

    def revoke(
        self, *, access_token: Optional[str] = None, refresh_token: Optional[str] = None
    ) -> None:
        if access_token is not None:
            claims = self._decode(access_token, check_expiration=False)
            with self._lock:
                self._revoked.add(claims.token_id)
        if refresh_token is not None:
            with self._lock:
                self._refresh_tokens.pop(refresh_token, None)


_jwt_token_manager = JWTTokenManager()


def get_jwt_token_manager() -> JWTTokenManager:
    return _jwt_token_manager


router = APIRouter(prefix="/security/tokens", tags=["security-jwt-manager"])


@router.post("")
def issue_token_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        access, refresh = get_jwt_token_manager().issue(
            payload.get("subject", ""),
            payload.get("token_type", "Access"),
            roles=payload.get("roles", []),
        )
    except InvalidTokenTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except UnknownUserError:
        raise HTTPException(status_code=404, detail="unknown user")
    return {
        "access_token": access.to_dict(),
        "refresh_token": refresh.to_dict() if refresh else None,
    }


@router.post("/refresh")
def refresh_token_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        access, refresh = get_jwt_token_manager().refresh(payload.get("refresh_token", ""))
    except UnknownRefreshTokenError:
        raise HTTPException(status_code=404, detail="unknown refresh token")
    except JWTExpiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return {"access_token": access.to_dict(), "refresh_token": refresh.to_dict()}


@router.post("/revoke")
def revoke_token_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        get_jwt_token_manager().revoke(
            access_token=payload.get("access_token"), refresh_token=payload.get("refresh_token")
        )
    except InvalidJWTError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"success": True}


@router.get("/validate")
def validate_token_endpoint(token: str = Query(...)) -> dict:
    try:
        claims = get_jwt_token_manager().validate(token)
    except InvalidJWTError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except JWTExpiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except JWTRevokedError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return claims.to_dict()
