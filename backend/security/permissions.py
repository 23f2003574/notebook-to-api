from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .rbac import RoleBasedAccessControl, UnknownRoleError, get_rbac

PERMISSION_TYPES = ("Read", "Write", "Execute", "Manage", "Admin")
_ACTION_RANK = {action: rank for rank, action in enumerate(PERMISSION_TYPES)}
_WILDCARD_RESOURCE = "*"


def _new_id() -> str:
    return uuid.uuid4().hex


def _permission_id(resource: str, action: str) -> str:
    return f"{resource}:{action}"


class InvalidPermissionTypeError(ValueError):
    pass


class PermissionNotGrantedError(KeyError):
    pass


@dataclass(frozen=True)
class Permission:
    """A resource + action pair that can be granted to roles."""

    permission_id: str
    resource: str
    action: str
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "permission_id": self.permission_id,
            "resource": self.resource,
            "action": self.action,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class PermissionAssignment:
    """A record of a permission granted to a role."""

    assignment_id: str
    role: str
    permission_id: str
    granted_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "role": self.role,
            "permission_id": self.permission_id,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
        }


class PermissionEngine:
    """Maps resource-level permissions onto roles and evaluates access for users."""

    def __init__(self, rbac: Optional[RoleBasedAccessControl] = None) -> None:
        self._rbac = rbac or get_rbac()
        self._permissions: dict[str, Permission] = {}
        self._role_permissions: dict[str, dict[str, PermissionAssignment]] = {}
        self._lock = Lock()

    def _validate_action(self, action: str) -> None:
        if action not in _ACTION_RANK:
            raise InvalidPermissionTypeError(f"unknown permission type '{action}'")

    def define(
        self, resource: str, action: str, *, timestamp: Optional[datetime] = None
    ) -> Permission:
        self._validate_action(action)
        permission_id = _permission_id(resource, action)
        with self._lock:
            existing = self._permissions.get(permission_id)
            if existing is not None:
                return existing
            permission = Permission(
                permission_id=permission_id,
                resource=resource,
                action=action,
                created_at=timestamp or datetime.now(timezone.utc),
            )
            self._permissions[permission_id] = permission
        return permission

    def grant(
        self, role: str, resource: str, action: str, *, timestamp: Optional[datetime] = None
    ) -> PermissionAssignment:
        self._rbac.get_role(role)
        permission = self.define(resource, action, timestamp=timestamp)
        with self._lock:
            assignment = PermissionAssignment(
                assignment_id=_new_id(),
                role=role,
                permission_id=permission.permission_id,
                granted_at=timestamp or datetime.now(timezone.utc),
            )
            self._role_permissions.setdefault(role, {})[permission.permission_id] = assignment
        return assignment

    def revoke(self, role: str, permission_id: str) -> None:
        with self._lock:
            role_permissions = self._role_permissions.get(role, {})
            if permission_id not in role_permissions:
                raise PermissionNotGrantedError(permission_id)
            del role_permissions[permission_id]

    def permissions_for_role(self, role: str, *, include_inherited: bool = True) -> list:
        if include_inherited:
            roles = self._rbac.resolve_inheritance(role)
        else:
            self._rbac.get_role(role)
            roles = [role]
        with self._lock:
            seen_ids: set = set()
            permissions = []
            for role_name in roles:
                for permission_id, assignment in self._role_permissions.get(role_name, {}).items():
                    if permission_id in seen_ids:
                        continue
                    seen_ids.add(permission_id)
                    permissions.append(self._permissions[assignment.permission_id])
        return permissions

    def check(self, user_id: str, resource: str, action: str) -> bool:
        self._validate_action(action)
        requested_rank = _ACTION_RANK[action]
        roles = self._rbac.roles_for_user(user_id)
        with self._lock:
            for role_name in roles:
                for assignment in self._role_permissions.get(role_name, {}).values():
                    permission = self._permissions[assignment.permission_id]
                    if permission.resource not in (resource, _WILDCARD_RESOURCE):
                        continue
                    if _ACTION_RANK[permission.action] >= requested_rank:
                        return True
        return False


_permission_engine = PermissionEngine()


def get_permission_engine() -> PermissionEngine:
    return _permission_engine


router = APIRouter(prefix="/security", tags=["security-permissions"])


@router.post("/permissions")
def define_permission_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        permission = get_permission_engine().define(
            payload.get("resource", ""), payload.get("action", "")
        )
    except InvalidPermissionTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return permission.to_dict()


@router.post("/roles/{role}/permissions")
def grant_permission_endpoint(role: str, payload: dict = Body(default={})) -> dict:
    try:
        assignment = get_permission_engine().grant(
            role, payload.get("resource", ""), payload.get("action", "")
        )
    except UnknownRoleError:
        raise HTTPException(status_code=404, detail="unknown role")
    except InvalidPermissionTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return assignment.to_dict()


@router.delete("/roles/{role}/permissions/{permission}")
def revoke_permission_endpoint(role: str, permission: str) -> dict:
    try:
        get_permission_engine().revoke(role, permission)
    except PermissionNotGrantedError:
        raise HTTPException(status_code=404, detail="permission not granted to role")
    return {"success": True}


@router.get("/roles/{role}/permissions")
def list_permissions_endpoint(role: str, include_inherited: bool = Query(default=True)) -> list:
    try:
        permissions = get_permission_engine().permissions_for_role(
            role, include_inherited=include_inherited
        )
    except UnknownRoleError:
        raise HTTPException(status_code=404, detail="unknown role")
    return [permission.to_dict() for permission in permissions]
