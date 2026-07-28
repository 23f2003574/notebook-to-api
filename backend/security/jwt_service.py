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
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .authentication import AuthenticationManager, UnknownUserError, get_authentication_manager

_ALGORITHM = "HS256"
_DEFAULT_ACCESS_TTL = timedelta(minutes=15)
_DEFAULT_REFRESH_TTL = timedelta(days=7)


def _new_id() -> str:
    return uuid.uuid4().hex


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class InvalidTokenError(ValueError):
    pass


class TokenExpiredError(ValueError):
    pass


class TokenRevokedError(ValueError):
    pass


class UnknownRefreshTokenError(KeyError):
    pass


@dataclass(frozen=True)
class TokenClaims:
    """The decoded, verified claims of a JWT access token."""

    subject: str
    token_id: str
    issued_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict:
        return {
            "sub": self.subject,
            "jti": self.token_id,
            "iat": int(self.issued_at.timestamp()),
            "exp": int(self.expires_at.timestamp()),
        }


@dataclass(frozen=True)
class JWTToken:
    """An issued access/refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class JWTTokenService:
    """Issues, validates, refreshes, and revokes signed JWT access tokens."""

    def __init__(
        self,
        authentication_manager: Optional[AuthenticationManager] = None,
        *,
        secret_key: Optional[str] = None,
        access_ttl: timedelta = _DEFAULT_ACCESS_TTL,
        refresh_ttl: timedelta = _DEFAULT_REFRESH_TTL,
    ) -> None:
        self._authentication_manager = authentication_manager or get_authentication_manager()
        self._secret_key = (secret_key or secrets.token_hex(32)).encode("utf-8")
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl
        self._revoked: set = set()
        self._refresh_tokens: dict = {}
        self._lock = Lock()

    def _sign(self, header: dict, payload: dict) -> str:
        header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = hmac.new(self._secret_key, signing_input, hashlib.sha256).digest()
        return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"

    def _issue_access_token(self, subject: str, *, timestamp: datetime) -> tuple:
        expires_at = timestamp + self._access_ttl
        claims = TokenClaims(
            subject=subject, token_id=_new_id(), issued_at=timestamp, expires_at=expires_at
        )
        token = self._sign({"alg": _ALGORITHM, "typ": "JWT"}, claims.to_dict())
        return token, claims

    def issue(self, user_id: str, *, timestamp: Optional[datetime] = None) -> JWTToken:
        self._authentication_manager.get_user(user_id)
        now = timestamp or datetime.now(timezone.utc)
        access_token, claims = self._issue_access_token(user_id, timestamp=now)
        refresh_token = _new_id()
        with self._lock:
            self._refresh_tokens[refresh_token] = {
                "subject": user_id,
                "expires_at": now + self._refresh_ttl,
            }
        return JWTToken(
            access_token=access_token, refresh_token=refresh_token, expires_at=claims.expires_at
        )

    def _decode(
        self, token: str, *, check_expiration: bool, timestamp: Optional[datetime] = None
    ) -> TokenClaims:
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
            raise InvalidTokenError("malformed token")

        if not hmac.compare_digest(expected_signature, actual_signature):
            raise InvalidTokenError("invalid signature")

        try:
            token_id = payload["jti"]
            expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
            issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
            subject = payload["sub"]
        except (KeyError, TypeError, OSError, OverflowError):
            raise InvalidTokenError("malformed token")

        with self._lock:
            revoked = token_id in self._revoked
        if revoked:
            raise TokenRevokedError(f"token '{token_id}' has been revoked")
        if check_expiration and now >= expires_at:
            raise TokenExpiredError("token has expired")

        return TokenClaims(
            subject=subject, token_id=token_id, issued_at=issued_at, expires_at=expires_at
        )

    def validate(self, token: str, *, timestamp: Optional[datetime] = None) -> TokenClaims:
        return self._decode(token, check_expiration=True, timestamp=timestamp)

    def refresh(self, refresh_token: str, *, timestamp: Optional[datetime] = None) -> JWTToken:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            record = self._refresh_tokens.get(refresh_token)
        if record is None:
            raise UnknownRefreshTokenError(refresh_token)
        if now >= record["expires_at"]:
            with self._lock:
                self._refresh_tokens.pop(refresh_token, None)
            raise TokenExpiredError("refresh token has expired")

        subject = record["subject"]
        access_token, claims = self._issue_access_token(subject, timestamp=now)
        new_refresh_token = _new_id()
        with self._lock:
            self._refresh_tokens.pop(refresh_token, None)
            self._refresh_tokens[new_refresh_token] = {
                "subject": subject,
                "expires_at": now + self._refresh_ttl,
            }
        return JWTToken(
            access_token=access_token, refresh_token=new_refresh_token, expires_at=claims.expires_at
        )

    def revoke(self, token: str) -> None:
        claims = self._decode(token, check_expiration=False)
        with self._lock:
            self._revoked.add(claims.token_id)


_jwt_token_service = JWTTokenService()


def get_jwt_token_service() -> JWTTokenService:
    return _jwt_token_service


router = APIRouter(prefix="/security", tags=["security-jwt"])


@router.post("/token")
def issue_token(payload: dict = Body(default={})) -> dict:
    auth_manager = get_authentication_manager()
    username = payload.get("username", "")
    password = payload.get("password", "")
    if not auth_manager.authenticate(username, password):
        raise HTTPException(status_code=401, detail="invalid username or password")
    user_id = auth_manager.get_user_id(username)
    token = get_jwt_token_service().issue(user_id)
    return token.to_dict()


@router.post("/token/refresh")
def refresh_token(payload: dict = Body(default={})) -> dict:
    try:
        token = get_jwt_token_service().refresh(payload.get("refresh_token", ""))
    except UnknownRefreshTokenError:
        raise HTTPException(status_code=404, detail="unknown refresh token")
    except TokenExpiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return token.to_dict()


@router.post("/token/revoke")
def revoke_token(payload: dict = Body(default={})) -> dict:
    try:
        get_jwt_token_service().revoke(payload.get("access_token", ""))
    except InvalidTokenError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except TokenRevokedError:
        pass
    return {"success": True}
