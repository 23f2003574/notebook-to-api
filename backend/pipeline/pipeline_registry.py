from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .data_sources import DataSourceManager


class PipelineAlreadyRegisteredError(ValueError):
    pass


class UnknownPipelineError(KeyError):
    pass


@dataclass(frozen=True)
class PipelineMetadata:
    """Descriptive information attached to a registered pipeline version."""

    description: str = ""
    owner: str = ""
    tags: tuple = ()
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "owner": self.owner,
            "tags": list(self.tags),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "PipelineMetadata":
        payload = payload or {}
        return cls(
            description=payload.get("description", ""),
            owner=payload.get("owner", ""),
            tags=tuple(payload.get("tags", ())),
            source=payload.get("source", ""),
        )


@dataclass(frozen=True)
class Pipeline:
    """A single registered version of a pipeline."""

    name: str
    version: str
    metadata: PipelineMetadata
    registered_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "metadata": self.metadata.to_dict(),
            "registered_at": self.registered_at.isoformat(),
        }


class PipelineRegistry:
    """Tracks registered pipelines, their versions, and discovery metadata."""

    def __init__(self) -> None:
        self._pipelines: dict = {}
        self._tag_index: dict = {}
        self._lock = Lock()

    def register(
        self,
        name: str,
        version: str,
        metadata: Optional[PipelineMetadata] = None,
        *,
        sources: Optional[DataSourceManager] = None,
        source_name: Optional[str] = None,
    ) -> Pipeline:
        if not name:
            raise ValueError("pipeline name is required")
        if not version:
            raise ValueError("pipeline version is required")
        metadata = metadata or PipelineMetadata()
        with self._lock:
            versions = self._pipelines.setdefault(name, {})
            if version in versions:
                raise PipelineAlreadyRegisteredError(f"{name}@{version} is already registered")
            pipeline = Pipeline(
                name=name,
                version=version,
                metadata=metadata,
                registered_at=datetime.now(timezone.utc),
            )
            versions[version] = pipeline
            for tag in metadata.tags:
                self._tag_index.setdefault(tag, set()).add(name)
        if sources is not None and source_name is not None:
            sources.connect(source_name)
        return pipeline

    def remove(self, name: str, version: Optional[str] = None) -> None:
        with self._lock:
            versions = self._pipelines.get(name)
            if not versions:
                raise UnknownPipelineError(name)
            if version is None:
                removed = self._pipelines.pop(name)
            else:
                if version not in versions:
                    raise UnknownPipelineError(f"{name}@{version}")
                removed = {version: versions.pop(version)}
                if not versions:
                    del self._pipelines[name]
            if name not in self._pipelines:
                for pipeline in removed.values():
                    for tag in pipeline.metadata.tags:
                        names = self._tag_index.get(tag)
                        if names is not None:
                            names.discard(name)
                            if not names:
                                del self._tag_index[tag]

    def get(self, name: str, version: Optional[str] = None) -> Pipeline:
        with self._lock:
            versions = self._pipelines.get(name)
            if not versions:
                raise UnknownPipelineError(name)
            if version is None:
                return max(versions.values(), key=lambda pipeline: pipeline.registered_at)
            pipeline = versions.get(version)
            if pipeline is None:
                raise UnknownPipelineError(f"{name}@{version}")
            return pipeline

    def is_registered(self, name: str, version: Optional[str] = None) -> bool:
        with self._lock:
            versions = self._pipelines.get(name)
            if not versions:
                return False
            if version is None:
                return True
            return version in versions

    def list_pipelines(self, tag: Optional[str] = None) -> list:
        with self._lock:
            if tag is not None:
                names = sorted(self._tag_index.get(tag, set()))
            else:
                names = sorted(self._pipelines)
            result = []
            for name in names:
                versions = self._pipelines.get(name)
                if versions:
                    result.append(max(versions.values(), key=lambda pipeline: pipeline.registered_at))
            return result


_pipeline_registry = PipelineRegistry()


def get_pipeline_registry() -> PipelineRegistry:
    return _pipeline_registry


router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.post("", status_code=201)
def register_pipeline_endpoint(
    payload: dict = Body(default={}),
    registry: PipelineRegistry = Depends(get_pipeline_registry),
) -> dict:
    try:
        pipeline = registry.register(
            payload.get("name", ""),
            payload.get("version", ""),
            PipelineMetadata.from_dict(payload.get("metadata")),
        )
    except PipelineAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return pipeline.to_dict()


@router.get("")
def list_pipelines_endpoint(
    tag: Optional[str] = Query(default=None),
    registry: PipelineRegistry = Depends(get_pipeline_registry),
) -> list:
    return [pipeline.to_dict() for pipeline in registry.list_pipelines(tag=tag)]


@router.get("/{name}")
def get_pipeline_endpoint(
    name: str,
    version: Optional[str] = Query(default=None),
    registry: PipelineRegistry = Depends(get_pipeline_registry),
) -> dict:
    try:
        pipeline = registry.get(name, version=version)
    except UnknownPipelineError:
        raise HTTPException(status_code=404, detail="unknown pipeline")
    return pipeline.to_dict()


@router.delete("/{name}", status_code=204)
def remove_pipeline_endpoint(
    name: str,
    version: Optional[str] = Query(default=None),
    registry: PipelineRegistry = Depends(get_pipeline_registry),
) -> None:
    try:
        registry.remove(name, version=version)
    except UnknownPipelineError:
        raise HTTPException(status_code=404, detail="unknown pipeline")
