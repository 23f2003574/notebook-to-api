from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .audit_logger import SecurityAuditLogger, get_security_audit_logger

_HASH_ITERATIONS = 100_000


def _new_id() -> str:
    return uuid.uuid4().hex


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS).hex()


class UserAlreadyExistsError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class UnknownSessionError(KeyError):
    pass


class UnknownUserError(KeyError):
    pass


@dataclass(frozen=True)
class UserCredential:
    """A registered user's stored credential material."""

    user_id: str
    username: str
    password_hash: str
    salt: str
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class AuthenticationResult:
    """The outcome of a login attempt."""

    success: bool
    user_id: Optional[str] = None
    session_token: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "user_id": self.user_id,
            "session_token": self.session_token,
            "message": self.message,
        }


class AuthenticationManager:
    """Registers users, validates credentials, and tracks authentication state."""

    def __init__(self, *, audit_logger: Optional[SecurityAuditLogger] = None) -> None:
        self._users: dict[str, UserCredential] = {}
        self._username_to_id: dict[str, str] = {}
        self._sessions: dict[str, str] = {}
        self._audit_logger = audit_logger or get_security_audit_logger()
        self._lock = Lock()

    def register(
        self, username: str, password: str, *, timestamp: Optional[datetime] = None
    ) -> UserCredential:
        if not username or not password:
            raise InvalidCredentialsError("username and password are required")
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            if username in self._username_to_id:
                raise UserAlreadyExistsError(f"user '{username}' already exists")
            salt = os.urandom(16)
            credential = UserCredential(
                user_id=_new_id(),
                username=username,
                password_hash=_hash_password(password, salt),
                salt=salt.hex(),
                created_at=now,
            )
            self._users[credential.user_id] = credential
            self._username_to_id[username] = credential.user_id
        self._audit_logger.record(
            "Authentication",
            credential.user_id,
            f"user:{username}",
            "register",
            outcome="success",
            severity="Info",
            timestamp=now,
        )
        return credential

    def authenticate(self, username: str, password: str) -> bool:
        with self._lock:
            user_id = self._username_to_id.get(username)
            if user_id is None:
                return False
            credential = self._users[user_id]
        return _hash_password(password, bytes.fromhex(credential.salt)) == credential.password_hash

    def login(
        self, username: str, password: str, *, timestamp: Optional[datetime] = None
    ) -> AuthenticationResult:
        now = timestamp or datetime.now(timezone.utc)
        if not self.authenticate(username, password):
            self._audit_logger.record(
                "Authentication",
                username,
                f"user:{username}",
                "login",
                outcome="failure",
                severity="Warning",
                timestamp=now,
            )
            return AuthenticationResult(success=False, message="invalid username or password")
        with self._lock:
            user_id = self._username_to_id[username]
            token = _new_id()
            self._sessions[token] = user_id
        self._audit_logger.record(
            "Authentication",
            user_id,
            f"user:{username}",
            "login",
            outcome="success",
            severity="Info",
            timestamp=now,
        )
        return AuthenticationResult(success=True, user_id=user_id, session_token=token)

    def logout(self, session_token: str, *, timestamp: Optional[datetime] = None) -> None:
        with self._lock:
            user_id = self._sessions.get(session_token)
            if user_id is None:
                raise UnknownSessionError(session_token)
            del self._sessions[session_token]
        self._audit_logger.record(
            "Authentication",
            user_id,
            f"session:{session_token}",
            "logout",
            outcome="success",
            severity="Info",
            timestamp=timestamp or datetime.now(timezone.utc),
        )

    def is_authenticated(self, session_token: str) -> bool:
        with self._lock:
            return session_token in self._sessions

    def get_user(self, user_id: str) -> UserCredential:
        with self._lock:
            credential = self._users.get(user_id)
        if credential is None:
            raise UnknownUserError(user_id)
        return credential

    def user_exists(self, user_id: str) -> bool:
        with self._lock:
            return user_id in self._users

    def get_user_id(self, username: str) -> str:
        with self._lock:
            user_id = self._username_to_id.get(username)
        if user_id is None:
            raise UnknownUserError(username)
        return user_id


_authentication_manager = AuthenticationManager()


def get_authentication_manager() -> AuthenticationManager:
    return _authentication_manager


router = APIRouter(prefix="/security", tags=["security-authentication"])


@router.post("/register")
def register_user(payload: dict = Body(default={})) -> dict:
    try:
        credential = get_authentication_manager().register(
            payload.get("username", ""), payload.get("password", "")
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return credential.to_dict()


@router.post("/login")
def login_user(payload: dict = Body(default={})) -> dict:
    result = get_authentication_manager().login(
        payload.get("username", ""), payload.get("password", "")
    )
    if not result.success:
        raise HTTPException(status_code=401, detail=result.message)
    return result.to_dict()


@router.post("/logout")
def logout_user(payload: dict = Body(default={})) -> dict:
    try:
        get_authentication_manager().logout(payload.get("session_token", ""))
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"success": True}
