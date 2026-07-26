from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException

_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class UnknownPipelineError(KeyError):
    pass


@dataclass(frozen=True)
class PipelineStage:
    """One step in a deployment pipeline's workflow."""

    name: str
    action: str
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "action": self.action,
            "config": dict(self.config),
        }


@dataclass(frozen=True)
class DeploymentPipeline:
    """An immutable, versioned definition of a deployment workflow."""

    name: str
    version: str
    stages: tuple = ()
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "stages": [stage.to_dict() for stage in self.stages],
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DeploymentPipelineEngine:
    """Registers and validates deployment pipeline definitions."""

    def __init__(self) -> None:
        self._pipelines: dict[str, DeploymentPipeline] = {}
        self._lock = Lock()

    def validate(
        self,
        name: str,
        stages: Iterable[PipelineStage],
        *,
        version: str = "1.0.0",
        metadata: Optional[dict] = None,
    ) -> None:
        if not name:
            raise ValueError("pipeline name is required")

        stages = list(stages)
        if not stages:
            raise ValueError("pipeline must define at least one stage")

        stage_names = [stage.name for stage in stages]
        if len(stage_names) != len(set(stage_names)):
            raise ValueError("stage names must be unique within a pipeline")

        if not _VERSION_PATTERN.match(version):
            raise ValueError("version must follow semantic versioning (e.g. '1.0.0')")

        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")

    def register(
        self,
        name: str,
        stages: Iterable[PipelineStage],
        *,
        version: str = "1.0.0",
        metadata: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> DeploymentPipeline:
        stages = list(stages)
        self.validate(name, stages, version=version, metadata=metadata)

        pipeline = DeploymentPipeline(
            name=name,
            version=version,
            stages=tuple(stages),
            metadata=dict(metadata or {}),
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._pipelines[name] = pipeline
        return pipeline

    def remove(self, name: str) -> None:
        with self._lock:
            if name not in self._pipelines:
                raise UnknownPipelineError(name)
            del self._pipelines[name]

    def get(self, name: str) -> DeploymentPipeline:
        with self._lock:
            pipeline = self._pipelines.get(name)
        if pipeline is None:
            raise UnknownPipelineError(name)
        return pipeline

    def list(self) -> list[DeploymentPipeline]:
        with self._lock:
            return list(self._pipelines.values())

    def stage_names(self, name: str) -> tuple:
        return tuple(stage.name for stage in self.get(name).stages)


_engine = DeploymentPipelineEngine()


def get_deployment_pipeline_engine() -> DeploymentPipelineEngine:
    return _engine


router = APIRouter(prefix="/governance", tags=["governance-pipelines"])


@router.post("/pipelines")
def register_pipeline(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    stages_payload = payload.get("stages")
    version = payload.get("version", "1.0.0")
    metadata = payload.get("metadata")

    if not name or not stages_payload:
        raise HTTPException(status_code=422, detail="name and stages are required")

    try:
        stages = [
            PipelineStage(
                name=stage["name"],
                action=stage.get("action", ""),
                config=stage.get("config", {}),
            )
            for stage in stages_payload
        ]
        pipeline = get_deployment_pipeline_engine().register(
            name, stages, version=version, metadata=metadata
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return pipeline.to_dict()


@router.get("/pipelines")
def list_pipelines() -> list[dict]:
    return [pipeline.to_dict() for pipeline in get_deployment_pipeline_engine().list()]


@router.get("/pipelines/{pipeline}")
def get_pipeline(pipeline: str) -> dict:
    try:
        result = get_deployment_pipeline_engine().get(pipeline)
    except UnknownPipelineError:
        raise HTTPException(status_code=404, detail="unknown pipeline")
    return result.to_dict()
