from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .artifact_versioning import (
    ArtifactVersionManager,
    UnknownVersionError,
    get_artifact_version_manager,
)

ENVIRONMENTS = ("Development", "Staging", "Production")


class InvalidPromotionError(ValueError):
    pass


class PromotionPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromotionRequest:
    """A request to move an artifact version to a target environment."""

    name: str
    version: str
    target_environment: str
    requested_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "target_environment": self.target_environment,
            "requested_by": self.requested_by,
        }


@dataclass(frozen=True)
class PromotionRecord:
    """An immutable record of one promotion or rollback between environments."""

    name: str
    version: str
    from_environment: str
    to_environment: str
    action: str = "PROMOTE"
    requested_by: Optional[str] = None
    promoted_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "from_environment": self.from_environment,
            "to_environment": self.to_environment,
            "action": self.action,
            "requested_by": self.requested_by,
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
        }


class ArtifactPromotionEngine:
    """Moves artifact versions through Development -> Staging -> Production."""

    def __init__(self, version_manager: Optional[ArtifactVersionManager] = None) -> None:
        self._version_manager = version_manager or get_artifact_version_manager()
        self._current_environment: dict[tuple, str] = {}
        self._history: dict[str, list[PromotionRecord]] = {}
        self._lock = Lock()

    def _current(self, name: str, version: str) -> str:
        return self._current_environment.get((name, version), ENVIRONMENTS[0])

    def current_environment(self, name: str, version: str) -> str:
        return self._current(name, version)

    def validate(self, name: str, version: str, target_environment: str) -> None:
        if target_environment not in ENVIRONMENTS:
            raise InvalidPromotionError(
                f"unknown environment '{target_environment}'"
            )

        entry = self._version_manager.get(name, version)
        if entry.state != "ACTIVE":
            raise InvalidPromotionError(
                f"version '{version}' is not active (state={entry.state})"
            )

        current = self._current(name, version)
        current_idx = ENVIRONMENTS.index(current)
        target_idx = ENVIRONMENTS.index(target_environment)
        if target_idx != current_idx + 1:
            raise PromotionPolicyError(
                f"cannot promote '{name}@{version}' from '{current}' directly to "
                f"'{target_environment}'"
            )

    def promote(
        self,
        name: str,
        version: str,
        target_environment: str,
        *,
        requested_by: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> PromotionRecord:
        self.validate(name, version, target_environment)
        current = self._current(name, version)

        record = PromotionRecord(
            name=name,
            version=version,
            from_environment=current,
            to_environment=target_environment,
            action="PROMOTE",
            requested_by=requested_by,
            promoted_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._current_environment[(name, version)] = target_environment
            self._history.setdefault(name, []).append(record)
        return record

    def rollback(
        self,
        name: str,
        version: str,
        *,
        requested_by: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> PromotionRecord:
        current = self._current(name, version)
        current_idx = ENVIRONMENTS.index(current)
        if current_idx == 0:
            raise PromotionPolicyError(
                f"'{name}@{version}' is already at '{ENVIRONMENTS[0]}'"
            )
        target_environment = ENVIRONMENTS[current_idx - 1]

        record = PromotionRecord(
            name=name,
            version=version,
            from_environment=current,
            to_environment=target_environment,
            action="ROLLBACK",
            requested_by=requested_by,
            promoted_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._current_environment[(name, version)] = target_environment
            self._history.setdefault(name, []).append(record)
        return record

    def history(self, name: str) -> list[PromotionRecord]:
        with self._lock:
            return list(self._history.get(name, []))


_promotion_engine = ArtifactPromotionEngine()


def get_artifact_promotion_engine() -> ArtifactPromotionEngine:
    return _promotion_engine


router = APIRouter(prefix="/governance", tags=["governance-artifact-promotions"])


@router.post("/artifacts/{artifact}/promote")
def promote_artifact(artifact: str, payload: dict = Body(...)) -> dict:
    version = payload.get("version")
    target_environment = payload.get("target_environment")
    if not version or not target_environment:
        raise HTTPException(
            status_code=422, detail="version and target_environment are required"
        )

    try:
        record = get_artifact_promotion_engine().promote(
            artifact,
            version,
            target_environment,
            requested_by=payload.get("requested_by"),
        )
    except UnknownVersionError:
        raise HTTPException(status_code=404, detail="unknown artifact version")
    except InvalidPromotionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PromotionPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return record.to_dict()


@router.get("/artifacts/{artifact}/promotions")
def list_promotions(artifact: str) -> list[dict]:
    return [record.to_dict() for record in get_artifact_promotion_engine().history(artifact)]
