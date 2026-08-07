from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException

from .authentication import UnknownUserError
from .jwt_manager import JWTTokenManager, get_jwt_token_manager

SESSION_STATES = ("Active", "Idle", "Expired", "Revoked")

DEFAULT_IDLE_AFTER = timedelta(minutes=5)
DEFAULT_IDLE_TIMEOUT = timedelta(minutes=30)


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownSessionError(KeyError):
    pass


class SessionExpiredError(ValueError):
    pass


class SessionRevokedError(ValueError):
    pass


@dataclass(frozen=True)
class SessionMetadata:
    """Descriptive, non-secret information about a session's lifecycle state."""

    session_id: str
    subject: str
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    state: str = "Active"
    revoked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "subject": self.subject,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_active_at": self.last_active_at.isoformat() if self.last_active_at else None,
            "state": self.state,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
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
    """Tracks authenticated session lifecycles on top of the JWT token manager."""

    def __init__(
        self,
        jwt_manager: Optional[JWTTokenManager] = None,
        *,
        idle_after: timedelta = DEFAULT_IDLE_AFTER,
        idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
    ) -> None:
        self._jwt_manager = jwt_manager or get_jwt_token_manager()
        self._idle_after = idle_after
        self._idle_timeout = min(idle_timeout, self._jwt_manager.ttl_for("Refresh"))
        self._sessions: dict[str, SessionMetadata] = {}
        self._subject_sessions: dict[str, set] = {}
        self._active_tokens: dict[str, dict] = {}
        self._lock = Lock()

    def _compute_state(self, metadata: SessionMetadata, now: datetime) -> str:
        if metadata.state == "Revoked":
            return "Revoked"
        idle_for = now - metadata.last_active_at
        if idle_for >= self._idle_timeout:
            return "Expired"
        if idle_for >= self._idle_after:
            return "Idle"
        return "Active"

    def create(
        self, subject: str, *, roles: Iterable[str] = (), timestamp: Optional[datetime] = None
    ) -> Session:
        now = timestamp or datetime.now(timezone.utc)
        access, refresh = self._jwt_manager.issue(subject, "Access", roles=roles, timestamp=now)
        session_id = _new_id()
        metadata = SessionMetadata(
            session_id=session_id,
            subject=subject,
            created_at=now,
            last_active_at=now,
            state="Active",
        )
        with self._lock:
            self._sessions[session_id] = metadata
            self._subject_sessions.setdefault(subject, set()).add(session_id)
            self._active_tokens[session_id] = {
                "access_token": access.token,
                "refresh_token": refresh.token,
            }
        return Session(metadata=metadata, access_token=access.token, refresh_token=refresh.token)

    def validate(self, session_id: str, *, timestamp: Optional[datetime] = None) -> SessionMetadata:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            metadata = self._sessions.get(session_id)
        if metadata is None:
            raise UnknownSessionError(session_id)

        state = self._compute_state(metadata, now)
        if state == "Revoked":
            raise SessionRevokedError(session_id)
        if state == "Expired":
            with self._lock:
                self._sessions[session_id] = replace(metadata, state="Expired")
            raise SessionExpiredError(session_id)

        updated = replace(metadata, last_active_at=now, state=state)
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

        new_access, new_refresh = self._jwt_manager.refresh(tokens["refresh_token"], timestamp=now)
        with self._lock:
            self._active_tokens[session_id] = {
                "access_token": new_access.token,
                "refresh_token": new_refresh.token,
            }
        return Session(
            metadata=metadata, access_token=new_access.token, refresh_token=new_refresh.token
        )

    def terminate(self, session_id: str, *, timestamp: Optional[datetime] = None) -> None:
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            metadata = self._sessions.get(session_id)
            if metadata is None:
                raise UnknownSessionError(session_id)
            tokens = self._active_tokens.pop(session_id, None)
            self._sessions[session_id] = replace(metadata, state="Revoked", revoked_at=now)
            self._subject_sessions.get(metadata.subject, set()).discard(session_id)

        if tokens is not None:
            self._jwt_manager.revoke(
                access_token=tokens["access_token"], refresh_token=tokens["refresh_token"]
            )

    def sessions_for_subject(self, subject: str) -> list:
        with self._lock:
            session_ids = list(self._subject_sessions.get(subject, set()))
            return [self._sessions[session_id] for session_id in session_ids]


_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _session_manager


router = APIRouter(prefix="/security/sessions", tags=["security-session-lifecycle"])


@router.post("")
def create_session_endpoint(payload: dict = Body(default={})) -> dict:
    subject = payload.get("subject", "")
    roles = payload.get("roles", [])
    try:
        session = get_session_manager().create(subject, roles=roles)
    except UnknownUserError:
        raise HTTPException(status_code=404, detail="unknown user")
    return session.to_dict()


@router.get("/{session}")
def get_session_endpoint(session: str) -> dict:
    try:
        metadata = get_session_manager().validate(session)
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    except SessionRevokedError:
        raise HTTPException(status_code=410, detail="session has been revoked")
    except SessionExpiredError:
        raise HTTPException(status_code=401, detail="session has expired")
    return metadata.to_dict()


@router.post("/{session}/refresh")
def refresh_session_endpoint(session: str) -> dict:
    try:
        refreshed = get_session_manager().refresh(session)
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    except SessionRevokedError:
        raise HTTPException(status_code=410, detail="session has been revoked")
    except SessionExpiredError:
        raise HTTPException(status_code=401, detail="session has expired")
    return refreshed.to_dict()


@router.delete("/{session}")
def terminate_session_endpoint(session: str) -> dict:
    try:
        get_session_manager().terminate(session)
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"success": True}
