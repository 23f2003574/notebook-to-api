from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .rbac import RoleBasedAccessControl, get_rbac

PERMISSION_TYPES = ("Read", "Write", "Execute", "Admin")
_ACTION_RANK = {action: rank for rank, action in enumerate(PERMISSION_TYPES)}
_WILDCARD_RESOURCE = "*"
_ADMIN_ROLE_NAME = "Admin"


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
    """A resource + action pair that can be granted to an identity."""

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
    """A record of a permission granted directly to an identity."""

    assignment_id: str
    identity: str
    permission_id: str
    granted_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "identity": self.identity,
            "permission_id": self.permission_id,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
        }


class PermissionEngine:
    """Grants and evaluates fine-grained, resource-level permissions for identities."""

    def __init__(self, rbac: Optional[RoleBasedAccessControl] = None, *, cache_enabled: bool = True) -> None:
        self._rbac = rbac or get_rbac()
        self._cache_enabled = cache_enabled
        self._permissions: dict[str, Permission] = {}
        self._identity_permissions: dict[str, dict[str, PermissionAssignment]] = {}
        self._cache: dict[tuple, tuple] = {}
        self._lock = Lock()

    def _validate_action(self, action: str) -> None:
        if action not in _ACTION_RANK:
            raise InvalidPermissionTypeError(f"unknown permission type '{action}'")

    def _define(self, resource: str, action: str, *, timestamp: Optional[datetime] = None) -> Permission:
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
        self, identity: str, resource: str, action: str, *, timestamp: Optional[datetime] = None
    ) -> PermissionAssignment:
        permission = self._define(resource, action, timestamp=timestamp)
        with self._lock:
            assignment = PermissionAssignment(
                assignment_id=_new_id(),
                identity=identity,
                permission_id=permission.permission_id,
                granted_at=timestamp or datetime.now(timezone.utc),
            )
            self._identity_permissions.setdefault(identity, {})[permission.permission_id] = assignment
            self._invalidate_cache_for(identity)
        return assignment

    def revoke(self, identity: str, resource: str, action: str) -> None:
        permission_id = _permission_id(resource, action)
        with self._lock:
            identity_permissions = self._identity_permissions.get(identity, {})
            if permission_id not in identity_permissions:
                raise PermissionNotGrantedError(permission_id)
            del identity_permissions[permission_id]
            self._invalidate_cache_for(identity)

    def revoke_all(self, identity: str) -> None:
        with self._lock:
            self._identity_permissions.pop(identity, None)
            self._invalidate_cache_for(identity)

    def _invalidate_cache_for(self, identity: str) -> None:
        for key in [key for key in self._cache if key[0] == identity]:
            del self._cache[key]

    def _resolve_effective(self, identity: str) -> tuple:
        version = self._rbac.version()
        cache_key = (identity, version)
        if self._cache_enabled:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        with self._lock:
            direct = [
                self._permissions[permission_id]
                for permission_id in self._identity_permissions.get(identity, {})
            ]

        roles = self._rbac.roles_for_user(identity)
        if _ADMIN_ROLE_NAME in roles:
            direct = direct + [
                Permission(
                    permission_id=_permission_id(_WILDCARD_RESOURCE, "Admin"),
                    resource=_WILDCARD_RESOURCE,
                    action="Admin",
                )
            ]

        effective = tuple(direct)
        if self._cache_enabled:
            with self._lock:
                self._cache[cache_key] = effective
        return effective

    def _matches_resource(self, permission_resource: str, resource: str) -> bool:
        return (
            permission_resource == _WILDCARD_RESOURCE
            or permission_resource == resource
            or resource.startswith(f"{permission_resource}/")
        )

    def check(self, identity: str, resource: str, action: str) -> bool:
        self._validate_action(action)
        requested_rank = _ACTION_RANK[action]
        for permission in self._resolve_effective(identity):
            if not self._matches_resource(permission.resource, resource):
                continue
            if _ACTION_RANK[permission.action] >= requested_rank:
                return True
        return False

    def list_permissions(self, identity: str, *, include_inherited: bool = True) -> list:
        if include_inherited:
            return list(self._resolve_effective(identity))
        with self._lock:
            return [
                self._permissions[permission_id]
                for permission_id in self._identity_permissions.get(identity, {})
            ]


_permission_engine = PermissionEngine()


def get_permission_engine() -> PermissionEngine:
    return _permission_engine


router = APIRouter(prefix="/security/permissions", tags=["security-permission-engine"])


@router.post("")
def grant_permission_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        assignment = get_permission_engine().grant(
            payload.get("identity", ""), payload.get("resource", ""), payload.get("action", "")
        )
    except InvalidPermissionTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return assignment.to_dict()


@router.delete("")
def revoke_permission_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        get_permission_engine().revoke(
            payload.get("identity", ""), payload.get("resource", ""), payload.get("action", "")
        )
    except PermissionNotGrantedError:
        raise HTTPException(status_code=404, detail="permission not granted")
    return {"success": True}


@router.post("/check")
def check_permission_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        allowed = get_permission_engine().check(
            payload.get("identity", ""), payload.get("resource", ""), payload.get("action", "")
        )
    except InvalidPermissionTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"allowed": allowed}


@router.get("/{identity}")
def list_permissions_endpoint(identity: str, include_inherited: bool = Query(default=True)) -> list:
    permissions = get_permission_engine().list_permissions(identity, include_inherited=include_inherited)
    return [permission.to_dict() for permission in permissions]
