from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .authentication import AuthenticationManager, UnknownUserError, get_authentication_manager

DEFAULT_ROLES = {
    "Viewer": (),
    "Developer": ("Viewer",),
    "Admin": ("Developer",),
    "Service": (),
}


def _new_id() -> str:
    return uuid.uuid4().hex


class RoleAlreadyExistsError(ValueError):
    pass


class UnknownRoleError(KeyError):
    pass


class RoleNotAssignedError(KeyError):
    pass


@dataclass(frozen=True)
class Role:
    """A named permission grouping that may inherit from other roles."""

    name: str
    inherits: tuple = ()
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "inherits": list(self.inherits),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class RoleAssignment:
    """A record of a role granted to a user."""

    assignment_id: str
    user_id: str
    role: str
    assigned_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "user_id": self.user_id,
            "role": self.role,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
        }


class RoleBasedAccessControl:
    """Registers roles and manages which roles are assigned to which users."""

    def __init__(
        self,
        authentication_manager: Optional[AuthenticationManager] = None,
        *,
        seed_default_roles: bool = True,
    ) -> None:
        self._authentication_manager = authentication_manager or get_authentication_manager()
        self._roles: dict[str, Role] = {}
        self._assignments: dict[str, dict[str, RoleAssignment]] = {}
        self._lock = Lock()
        if seed_default_roles:
            for name, inherits in DEFAULT_ROLES.items():
                self.create_role(name, inherits=inherits)

    def create_role(
        self,
        name: str,
        *,
        inherits: Iterable[str] = (),
        timestamp: Optional[datetime] = None,
    ) -> Role:
        inherits = tuple(inherits)
        with self._lock:
            if name in self._roles:
                raise RoleAlreadyExistsError(f"role '{name}' already exists")
            for parent in inherits:
                if parent not in self._roles:
                    raise UnknownRoleError(parent)
            role = Role(
                name=name, inherits=inherits, created_at=timestamp or datetime.now(timezone.utc)
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
        self, user_id: str, role: str, *, timestamp: Optional[datetime] = None
    ) -> RoleAssignment:
        self._authentication_manager.get_user(user_id)
        with self._lock:
            if role not in self._roles:
                raise UnknownRoleError(role)
            assignment = RoleAssignment(
                assignment_id=_new_id(),
                user_id=user_id,
                role=role,
                assigned_at=timestamp or datetime.now(timezone.utc),
            )
            self._assignments.setdefault(user_id, {})[role] = assignment
        return assignment

    def revoke_role(self, user_id: str, role: str) -> None:
        with self._lock:
            user_assignments = self._assignments.get(user_id, {})
            if role not in user_assignments:
                raise RoleNotAssignedError(role)
            del user_assignments[role]

    def _resolve_inherited(self, role_name: str, resolved: set) -> None:
        if role_name in resolved:
            return
        resolved.add(role_name)
        role = self._roles.get(role_name)
        if role is None:
            return
        for parent in role.inherits:
            self._resolve_inherited(parent, resolved)

    def roles_for_user(self, user_id: str, *, include_inherited: bool = True) -> list:
        with self._lock:
            assigned = list(self._assignments.get(user_id, {}).keys())
        if not include_inherited:
            return sorted(assigned)
        resolved: set = set()
        for role_name in assigned:
            self._resolve_inherited(role_name, resolved)
        return sorted(resolved)

    def assignments_for_user(self, user_id: str) -> list:
        with self._lock:
            return list(self._assignments.get(user_id, {}).values())

    def resolve_inheritance(self, role_name: str) -> list:
        self.get_role(role_name)
        with self._lock:
            resolved: set = set()
            self._resolve_inherited(role_name, resolved)
        return sorted(resolved)


_rbac = RoleBasedAccessControl()


def get_rbac() -> RoleBasedAccessControl:
    return _rbac


router = APIRouter(prefix="/security", tags=["security-rbac"])


@router.post("/roles")
def create_role_endpoint(payload: dict = Body(default={})) -> dict:
    name = payload.get("name", "")
    inherits = payload.get("inherits", [])
    try:
        role = get_rbac().create_role(name, inherits=inherits)
    except RoleAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UnknownRoleError as exc:
        raise HTTPException(status_code=422, detail=f"unknown parent role '{exc}'")
    return role.to_dict()


@router.post("/users/{user}/roles")
def assign_role_endpoint(user: str, payload: dict = Body(default={})) -> dict:
    role = payload.get("role", "")
    try:
        assignment = get_rbac().assign_role(user, role)
    except UnknownUserError:
        raise HTTPException(status_code=404, detail="unknown user")
    except UnknownRoleError:
        raise HTTPException(status_code=404, detail="unknown role")
    return assignment.to_dict()


@router.delete("/users/{user}/roles/{role}")
def revoke_role_endpoint(user: str, role: str) -> dict:
    try:
        get_rbac().revoke_role(user, role)
    except RoleNotAssignedError:
        raise HTTPException(status_code=404, detail="role not assigned to user")
    return {"success": True}


@router.get("/users/{user}/roles")
def list_roles_endpoint(user: str, include_inherited: bool = Query(default=True)) -> list:
    return get_rbac().roles_for_user(user, include_inherited=include_inherited)
