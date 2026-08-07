from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .jwt_service import TokenExpiredError
from .session_manager import (
    SessionExpiredError,
    SessionTerminatedError,
    UnknownSessionError as UnknownActiveSessionError,
    get_session_manager,
)

ROLE_TYPES = ("Administrator", "Developer", "Viewer", "Service Account")

_ROLE_TYPE_HIERARCHY = {
    "Administrator": ("Developer",),
    "Developer": ("Viewer",),
    "Viewer": (),
    "Service Account": (),
}

_BASE_PERMISSIONS = {
    "Administrator": frozenset({"manage_roles", "manage_resources", "delete_resource"}),
    "Developer": frozenset({"write_resource"}),
    "Viewer": frozenset({"read_resource"}),
    "Service Account": frozenset({"read_resource", "invoke_service"}),
}


def _new_id() -> str:
    return uuid.uuid4().hex


def _resolve_permissions(role_type: str, *, _seen: Optional[set] = None) -> frozenset:
    seen = _seen if _seen is not None else set()
    if role_type in seen:
        return frozenset()
    seen.add(role_type)
    permissions = set(_BASE_PERMISSIONS.get(role_type, ()))
    for parent in _ROLE_TYPE_HIERARCHY.get(role_type, ()):
        permissions |= _resolve_permissions(parent, _seen=seen)
    return frozenset(permissions)


class RoleAlreadyExistsError(ValueError):
    pass


class UnknownRoleError(KeyError):
    pass


class RoleNotAssignedError(KeyError):
    pass


class InvalidRoleTypeError(ValueError):
    pass


@dataclass(frozen=True)
class Role:
    """A named role backed by a built-in role type, with hierarchy-resolved permissions."""

    name: str
    role_type: str
    permissions: frozenset
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role_type": self.role_type,
            "permissions": sorted(self.permissions),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class RoleAssignment:
    """A record of a role granted to a subject (user or service)."""

    assignment_id: str
    subject: str
    role_name: str
    assigned_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "subject": self.subject,
            "role_name": self.role_name,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
        }


class RBACEngine:
    """Registers roles, tracks assignments, and authorizes subjects against permissions."""

    def __init__(self, session_manager=None, *, seed_default_roles: bool = True) -> None:
        self._session_manager = session_manager or get_session_manager()
        self._roles: dict[str, Role] = {}
        self._assignments: dict[str, dict[str, RoleAssignment]] = {}
        self._lock = Lock()
        if seed_default_roles:
            for role_type in ROLE_TYPES:
                self.create_role(role_type, role_type)

    def create_role(
        self, name: str, role_type: str, *, timestamp: Optional[datetime] = None
    ) -> Role:
        if role_type not in ROLE_TYPES:
            raise InvalidRoleTypeError(f"'{role_type}' is not a known role type")
        with self._lock:
            if name in self._roles:
                raise RoleAlreadyExistsError(f"role '{name}' already exists")
            role = Role(
                name=name,
                role_type=role_type,
                permissions=_resolve_permissions(role_type),
                created_at=timestamp or datetime.now(timezone.utc),
            )
            self._roles[name] = role
        return role

    def get_role(self, name: str) -> Role:
        with self._lock:
            role = self._roles.get(name)
        if role is None:
            raise UnknownRoleError(name)
        return role

    def assign_role(
        self, subject: str, role_name: str, *, timestamp: Optional[datetime] = None
    ) -> RoleAssignment:
        with self._lock:
            if role_name not in self._roles:
                raise UnknownRoleError(role_name)
            assignment = RoleAssignment(
                assignment_id=_new_id(),
                subject=subject,
                role_name=role_name,
                assigned_at=timestamp or datetime.now(timezone.utc),
            )
            self._assignments.setdefault(subject, {})[role_name] = assignment
        return assignment

    def revoke_role(self, subject: str, role_name: str) -> None:
        with self._lock:
            subject_assignments = self._assignments.get(subject, {})
            if role_name not in subject_assignments:
                raise RoleNotAssignedError(role_name)
            del subject_assignments[role_name]

    def roles_for_subject(self, subject: str) -> list:
        with self._lock:
            return list(self._assignments.get(subject, {}).keys())

    def permissions_for_subject(self, subject: str) -> frozenset:
        with self._lock:
            role_names = list(self._assignments.get(subject, {}).keys())
            roles = [self._roles[name] for name in role_names if name in self._roles]
        permissions: set = set()
        for role in roles:
            permissions |= role.permissions
        return frozenset(permissions)

    def authorize(
        self,
        subject: str,
        permission: str,
        *,
        session_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        permissions = set(self.permissions_for_subject(subject))
        if session_id is not None:
            try:
                session_role_names = self._session_manager.session_roles(
                    session_id, timestamp=timestamp
                )
            except (
                UnknownActiveSessionError,
                SessionExpiredError,
                SessionTerminatedError,
                TokenExpiredError,
            ):
                session_role_names = ()
            for role_name in session_role_names:
                if role_name in ROLE_TYPES:
                    permissions |= _resolve_permissions(role_name)
                elif role_name in self._roles:
                    permissions |= self._roles[role_name].permissions
        return permission in permissions


_rbac_engine = RBACEngine()


def get_rbac_engine() -> RBACEngine:
    return _rbac_engine


router = APIRouter(prefix="/security", tags=["security-rbac-engine"])


@router.post("/roles")
def create_role_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        role = get_rbac_engine().create_role(
            payload.get("name", ""), payload.get("role_type", "")
        )
    except InvalidRoleTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RoleAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return role.to_dict()


@router.post("/roles/assign")
def assign_role_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        assignment = get_rbac_engine().assign_role(
            payload.get("subject", ""), payload.get("role_name", "")
        )
    except UnknownRoleError:
        raise HTTPException(status_code=404, detail="unknown role")
    return assignment.to_dict()


@router.delete("/roles/assign")
def revoke_role_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        get_rbac_engine().revoke_role(payload.get("subject", ""), payload.get("role_name", ""))
    except RoleNotAssignedError:
        raise HTTPException(status_code=404, detail="role not assigned")
    return {"success": True}


@router.post("/authorize")
def authorize_endpoint(payload: dict = Body(default={})) -> dict:
    allowed = get_rbac_engine().authorize(
        payload.get("subject", ""),
        payload.get("permission", ""),
        session_id=payload.get("session_id"),
    )
    return {"allowed": allowed}
