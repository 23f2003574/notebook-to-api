from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

FALLBACK_STRATEGIES = frozenset({"default", "latest", "reject"})


class VersionAlreadyRegisteredError(ValueError):
    pass


class UnknownVersionError(KeyError):
    pass


class NoDefaultVersionError(RuntimeError):
    pass


@dataclass
class APIVersion:
    """A single registered API version and its lifecycle state."""

    version: str
    is_default: bool = False
    deprecated: bool = False
    released_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deprecated_at: Optional[datetime] = None
    sunset_at: Optional[datetime] = None
    compatible_with: tuple = ()
    release_notes: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "is_default": self.is_default,
            "deprecated": self.deprecated,
            "released_at": self.released_at.isoformat(),
            "deprecated_at": self.deprecated_at.isoformat() if self.deprecated_at else None,
            "sunset_at": self.sunset_at.isoformat() if self.sunset_at else None,
            "compatible_with": list(self.compatible_with),
            "release_notes": self.release_notes,
        }


@dataclass(frozen=True)
class VersionPolicy:
    """Governs how the manager resolves requests that don't name an exact version."""

    fallback_strategy: str = "default"
    deprecation_grace_period_days: int = 0

    def to_dict(self) -> dict:
        return {
            "fallback_strategy": self.fallback_strategy,
            "deprecation_grace_period_days": self.deprecation_grace_period_days,
        }


class APIVersionManager:
    """Registers API versions and resolves requests to the correct one."""

    def __init__(self, policy: Optional[VersionPolicy] = None) -> None:
        if policy is not None and policy.fallback_strategy not in FALLBACK_STRATEGIES:
            raise ValueError(f"unsupported fallback strategy: {policy.fallback_strategy}")
        self._policy = policy or VersionPolicy()
        self._versions: dict = {}
        self._order: list = []
        self._default_version: Optional[str] = None
        self._lock = Lock()

    def register_version(
        self,
        version: str,
        *,
        is_default: bool = False,
        compatible_with: Iterable[str] = (),
        release_notes: str = "",
    ) -> APIVersion:
        if not version:
            raise ValueError("version is required")
        with self._lock:
            if version in self._versions:
                raise VersionAlreadyRegisteredError(f"{version} is already registered")
            entry = APIVersion(
                version=version,
                is_default=is_default,
                compatible_with=tuple(compatible_with),
                release_notes=release_notes,
            )
            self._versions[version] = entry
            self._order.append(version)
            if is_default:
                for other in self._versions.values():
                    other.is_default = other.version == version
                self._default_version = version
            return entry

    def _require(self, version: str) -> APIVersion:
        entry = self._versions.get(version)
        if entry is None:
            raise UnknownVersionError(version)
        return entry

    def deprecate(self, version: str, *, sunset_at: Optional[datetime] = None, message: str = "") -> APIVersion:
        with self._lock:
            entry = self._require(version)
            entry.deprecated = True
            entry.deprecated_at = datetime.now(timezone.utc)
            entry.sunset_at = sunset_at
            if message:
                entry.release_notes = message
            return entry

    def supported_versions(self, *, include_deprecated: bool = True) -> list:
        with self._lock:
            versions = [self._versions[name] for name in self._order]
            if not include_deprecated:
                versions = [entry for entry in versions if not entry.deprecated]
            return versions

    def resolve(self, requested: Optional[str] = None) -> APIVersion:
        with self._lock:
            if requested is None or requested == "default":
                if self._default_version is None:
                    raise NoDefaultVersionError("no default version configured")
                return self._versions[self._default_version]
            if requested == "latest":
                if not self._order:
                    raise UnknownVersionError(requested)
                return self._versions[self._order[-1]]
            return self._require(requested)

    def check_compatibility(self, version: str, target: str) -> bool:
        with self._lock:
            if version == target:
                return True
            entry = self._require(version)
            other = self._require(target)
            return target in entry.compatible_with or version in other.compatible_with


_version_manager = APIVersionManager()


def get_version_manager() -> APIVersionManager:
    return _version_manager


router = APIRouter(prefix="/gateway/versions", tags=["gateway-versions"])


@router.post("", status_code=201)
def register_version_endpoint(
    payload: dict = Body(default={}),
    manager: APIVersionManager = Depends(get_version_manager),
) -> dict:
    try:
        entry = manager.register_version(
            payload.get("version", ""),
            is_default=payload.get("is_default", False),
            compatible_with=payload.get("compatible_with", []),
            release_notes=payload.get("release_notes", ""),
        )
    except VersionAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return entry.to_dict()


@router.get("")
def list_versions_endpoint(manager: APIVersionManager = Depends(get_version_manager)) -> list:
    return [entry.to_dict() for entry in manager.supported_versions()]


@router.get("/{version}")
def resolve_version_endpoint(
    version: str,
    manager: APIVersionManager = Depends(get_version_manager),
) -> dict:
    try:
        entry = manager.resolve(version)
    except (UnknownVersionError, NoDefaultVersionError):
        raise HTTPException(status_code=404, detail="unknown version")
    return entry.to_dict()


@router.post("/{version}/deprecate")
def deprecate_version_endpoint(
    version: str,
    payload: dict = Body(default={}),
    manager: APIVersionManager = Depends(get_version_manager),
) -> dict:
    sunset_at = None
    raw_sunset_at = payload.get("sunset_at")
    if raw_sunset_at:
        try:
            sunset_at = datetime.fromisoformat(raw_sunset_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="sunset_at must be an ISO 8601 datetime")
    try:
        entry = manager.deprecate(version, sunset_at=sunset_at, message=payload.get("message", ""))
    except UnknownVersionError:
        raise HTTPException(status_code=404, detail="unknown version")
    return entry.to_dict()
