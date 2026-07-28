from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException

from .authentication import UnknownUserError
from .jwt_service import JWTTokenService, get_jwt_token_service

DEFAULT_IDLE_TIMEOUT = timedelta(minutes=30)


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownSessionError(KeyError):
    pass


class SessionExpiredError(ValueError):
    pass


class SessionTerminatedError(ValueError):
    pass


@dataclass(frozen=True)
class SessionMetadata:
    """Descriptive, non-secret information about a session's lifecycle state."""

    session_id: str
    user_id: str
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    terminated: bool = False
    terminated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_active_at": self.last_active_at.isoformat() if self.last_active_at else None,
            "terminated": self.terminated,
            "terminated_at": self.terminated_at.isoformat() if self.terminated_at else None,
        }


@dataclass(frozen=True)
class Session:
    """A session backed by a JWT access/refresh token pair."""

    metadata: SessionMetadata
    access_token: str
    refresh_token: str

    def to_dict(self) -> dict:
        return {
            **self.metadata.to_dict(),
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
        }


class SessionManager:
    """Manages authenticated session lifecycles on top of the JWT token service."""

    def __init__(
        self,
        jwt_service: Optional[JWTTokenService] = None,
        *,
        idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
    ) -> None:
        self._jwt_service = jwt_service or get_jwt_token_service()
        self._idle_timeout = idle_timeout
        self._sessions: dict[str, SessionMetadata] = {}
        self._user_sessions: dict[str, set] = {}
        self._active_tokens: dict[str, dict] = {}
        self._lock = Lock()

    def create(
        self, user_id: str, *, roles: Iterable[str] = (), timestamp: Optional[datetime] = None
    ) -> Session:
        now = timestamp or datetime.now(timezone.utc)
        token = self._jwt_service.issue(user_id, roles=roles, timestamp=now)
        session_id = _new_id()
        metadata = SessionMetadata(
            session_id=session_id, user_id=user_id, created_at=now, last_active_at=now
        )
        with self._lock:
            self._sessions[session_id] = metadata
            self._user_sessions.setdefault(user_id, set()).add(session_id)
            self._active_tokens[session_id] = {
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
            }
        return Session(
            metadata=metadata, access_token=token.access_token, refresh_token=token.refresh_token
        )

    def validate(self, session_id: str, *, timestamp: Optional[datetime] = None) -> SessionMetadata:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            metadata = self._sessions.get(session_id)
        if metadata is None:
            raise UnknownSessionError(session_id)
        if metadata.terminated:
            raise SessionTerminatedError(session_id)
        if now - metadata.last_active_at >= self._idle_timeout:
            raise SessionExpiredError(session_id)

        updated = replace(metadata, last_active_at=now)
        with self._lock:
            self._sessions[session_id] = updated
        return updated

    def refresh(self, session_id: str, *, timestamp: Optional[datetime] = None) -> Session:
        now = timestamp or datetime.now(timezone.utc)
        metadata = self.validate(session_id, timestamp=now)
        with self._lock:
            tokens = self._active_tokens.get(session_id)
        if tokens is None:
            raise UnknownSessionError(session_id)

        new_token = self._jwt_service.refresh(tokens["refresh_token"], timestamp=now)
        with self._lock:
            self._active_tokens[session_id] = {
                "access_token": new_token.access_token,
                "refresh_token": new_token.refresh_token,
            }
        return Session(
            metadata=metadata,
            access_token=new_token.access_token,
            refresh_token=new_token.refresh_token,
        )

    def terminate(self, session_id: str, *, timestamp: Optional[datetime] = None) -> None:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            metadata = self._sessions.get(session_id)
            if metadata is None:
                raise UnknownSessionError(session_id)
            tokens = self._active_tokens.pop(session_id, None)
            self._sessions[session_id] = replace(metadata, terminated=True, terminated_at=now)
            self._user_sessions.get(metadata.user_id, set()).discard(session_id)

        if tokens is not None:
            self._jwt_service.revoke(tokens["access_token"])
            self._jwt_service.revoke_refresh_token(tokens["refresh_token"])

    def sessions_for_user(self, user_id: str) -> list:
        with self._lock:
            session_ids = list(self._user_sessions.get(user_id, set()))
            return [self._sessions[session_id] for session_id in session_ids]


_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _session_manager


router = APIRouter(prefix="/security", tags=["security-sessions"])


@router.post("/sessions")
def create_session_endpoint(payload: dict = Body(default={})) -> dict:
    user_id = payload.get("user_id", "")
    roles = payload.get("roles", [])
    try:
        session = get_session_manager().create(user_id, roles=roles)
    except UnknownUserError:
        raise HTTPException(status_code=404, detail="unknown user")
    return session.to_dict()


@router.get("/sessions")
def list_sessions_endpoint(user_id: str) -> list:
    return [metadata.to_dict() for metadata in get_session_manager().sessions_for_user(user_id)]


@router.post("/sessions/{session}/refresh")
def refresh_session_endpoint(session: str) -> dict:
    try:
        refreshed = get_session_manager().refresh(session)
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    except SessionTerminatedError:
        raise HTTPException(status_code=410, detail="session has been terminated")
    except SessionExpiredError:
        raise HTTPException(status_code=401, detail="session has expired")
    return refreshed.to_dict()


@router.delete("/sessions/{session}")
def terminate_session_endpoint(session: str) -> dict:
    try:
        get_session_manager().terminate(session)
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"success": True}
