from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Query

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_LEVEL_ORDER = {level: index for index, level in enumerate(LOG_LEVELS)}


def _normalize_level(level: str) -> str:
    level = level.upper()
    if level not in _LEVEL_ORDER:
        raise ValueError(
            f"invalid log level {level!r}; expected one of {LOG_LEVELS}"
        )
    return level


@dataclass(frozen=True)
class DeploymentLogEntry:
    """One immutable structured log record for a deployment."""

    deployment: str
    level: str
    message: str
    timestamp: datetime
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "deployment": self.deployment,
            "level": self.level,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "context": dict(self.context),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class DeploymentLoggingService:
    """Centralizes structured deployment logs for governance components."""

    def __init__(self) -> None:
        self._entries: list[DeploymentLogEntry] = []
        self._lock = Lock()

    def log(
        self,
        deployment: str,
        level: str,
        message: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[datetime] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> DeploymentLogEntry:
        if not deployment:
            raise ValueError("deployment correlation id is required")
        merged_context = dict(context or {})
        if trace_id is not None:
            merged_context["trace_id"] = trace_id
        if span_id is not None:
            merged_context["span_id"] = span_id
        entry = DeploymentLogEntry(
            deployment=deployment,
            level=_normalize_level(level),
            message=message,
            timestamp=timestamp or datetime.now(timezone.utc),
            context=merged_context,
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    def query(
        self,
        deployment: Optional[str] = None,
        level: Optional[str] = None,
    ) -> list[DeploymentLogEntry]:
        with self._lock:
            entries = list(self._entries)
        if deployment is not None:
            entries = [e for e in entries if e.deployment == deployment]
        if level is not None:
            floor = _LEVEL_ORDER[_normalize_level(level)]
            entries = [e for e in entries if _LEVEL_ORDER[e.level] >= floor]
        return entries

    def export(
        self,
        deployment: Optional[str] = None,
        level: Optional[str] = None,
    ) -> str:
        entries = self.query(deployment=deployment, level=level)
        return json.dumps([e.to_dict() for e in entries], sort_keys=True)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_service = DeploymentLoggingService()


def get_deployment_logging_service() -> DeploymentLoggingService:
    return _service


router = APIRouter(prefix="/governance", tags=["governance-logs"])


@router.get("/logs")
def list_logs(level: Optional[str] = Query(default=None)) -> list[dict]:
    entries = get_deployment_logging_service().query(level=level)
    return [entry.to_dict() for entry in entries]


@router.get("/logs/{deployment}")
def list_logs_for_deployment(
    deployment: str, level: Optional[str] = Query(default=None)
) -> list[dict]:
    entries = get_deployment_logging_service().query(
        deployment=deployment, level=level
    )
    return [entry.to_dict() for entry in entries]


@router.post("/logs/export")
def export_logs(
    deployment: Optional[str] = None, level: Optional[str] = None
) -> list[dict]:
    payload = get_deployment_logging_service().export(
        deployment=deployment, level=level
    )
    return json.loads(payload)
