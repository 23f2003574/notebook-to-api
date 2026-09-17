from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_pipeline import (
    DeploymentPipeline,
    DeploymentPipelineEngine,
    PipelineAlreadyExistsError,
    PipelineStage,
)

_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class UnknownTemplateError(KeyError):
    pass


class TemplateParameterError(ValueError):
    pass


@dataclass(frozen=True)
class TemplateParameter:
    """One parameter a pipeline template exposes for instantiation."""

    name: str
    required: bool = False
    default: Optional[object] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("parameter name is required")
        if self.required and self.default is not None:
            raise ValueError(
                f"parameter '{self.name}' cannot be both required and have a default"
            )

    def to_dict(self) -> dict:
        return {"name": self.name, "required": self.required, "default": self.default}


@dataclass(frozen=True)
class PipelineTemplate:
    """An immutable, versioned, reusable blueprint for building deployment pipelines."""

    name: str
    version: str
    stages: tuple = ()
    parameters: tuple = ()
    extends: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "stages": [stage.to_dict() for stage in self.stages],
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "extends": self.extends,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _substitute(value, values: dict):
    if isinstance(value, str):
        for name, replacement in values.items():
            placeholder = "${" + name + "}"
            if placeholder in value:
                value = value.replace(placeholder, "" if replacement is None else str(replacement))
        return value
    if isinstance(value, dict):
        return {key: _substitute(item, values) for key, item in value.items()}
    return value


def _substitute_stage(stage: PipelineStage, values: dict) -> PipelineStage:
    return PipelineStage(
        name=stage.name,
        action=_substitute(stage.action, values),
        config=_substitute(stage.config, values),
    )


class DeploymentTemplateRegistry:
    """Registers versioned, parameterized pipeline templates and instantiates them into pipelines."""

    def __init__(self) -> None:
        self._templates: dict[str, dict[str, PipelineTemplate]] = {}
        self._latest: dict[str, str] = {}
        self._lock = Lock()

    def register(
        self,
        name: str,
        stages,
        *,
        version: str = "1.0.0",
        parameters=(),
        extends: Optional[str] = None,
        metadata: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> PipelineTemplate:
        if not name:
            raise ValueError("template name is required")
        if not _VERSION_PATTERN.match(version):
            raise ValueError("version must follow semantic versioning (e.g. '1.0.0')")

        stages = list(stages)
        parameters = list(parameters)

        if extends is not None:
            parent = self.get(extends)
            merged_stages = {stage.name: stage for stage in parent.stages}
            for stage in stages:
                merged_stages[stage.name] = stage
            stages = list(merged_stages.values())

            merged_parameters = {parameter.name: parameter for parameter in parent.parameters}
            for parameter in parameters:
                merged_parameters[parameter.name] = parameter
            parameters = list(merged_parameters.values())

        if not stages:
            raise ValueError("template must define at least one stage")

        stage_names = [stage.name for stage in stages]
        if len(stage_names) != len(set(stage_names)):
            raise ValueError("stage names must be unique within a template")

        parameter_names = [parameter.name for parameter in parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameter names must be unique within a template")

        template = PipelineTemplate(
            name=name,
            version=version,
            stages=tuple(stages),
            parameters=tuple(parameters),
            extends=extends,
            metadata=dict(metadata or {}),
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._templates.setdefault(name, {})[version] = template
            self._latest[name] = version
        return template

    def remove(self, name: str, version: Optional[str] = None) -> None:
        with self._lock:
            versions = self._templates.get(name)
            if not versions:
                raise UnknownTemplateError(name)

            if version is None:
                del self._templates[name]
                self._latest.pop(name, None)
                return

            if version not in versions:
                raise UnknownTemplateError(f"{name}@{version}")
            del versions[version]

            if not versions:
                del self._templates[name]
                self._latest.pop(name, None)
            elif self._latest.get(name) == version:
                self._latest[name] = max(
                    versions, key=lambda v: tuple(int(part) for part in v.split("."))
                )

    def get(self, name: str, version: Optional[str] = None) -> PipelineTemplate:
        with self._lock:
            versions = self._templates.get(name)
            if not versions:
                raise UnknownTemplateError(name)
            resolved_version = version or self._latest.get(name)
            template = versions.get(resolved_version)
        if template is None:
            raise UnknownTemplateError(f"{name}@{version}")
        return template

    def list(self) -> list:
        with self._lock:
            return [self._templates[name][self._latest[name]] for name in sorted(self._templates)]

    def instantiate(
        self,
        name: str,
        *,
        version: Optional[str] = None,
        parameters: Optional[dict] = None,
        pipeline_name: Optional[str] = None,
        pipeline_engine: Optional[DeploymentPipelineEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> DeploymentPipeline:
        template = self.get(name, version)
        supplied = dict(parameters or {})
        declared = {parameter.name for parameter in template.parameters}

        errors = []
        unknown = set(supplied) - declared
        if unknown:
            errors.append(f"unknown parameters: {sorted(unknown)}")

        resolved_values: dict = {}
        for parameter in template.parameters:
            if parameter.name in supplied:
                resolved_values[parameter.name] = supplied[parameter.name]
            elif parameter.default is not None:
                resolved_values[parameter.name] = parameter.default
            elif parameter.required:
                errors.append(f"missing required parameter '{parameter.name}'")
            else:
                resolved_values[parameter.name] = None

        if errors:
            raise TemplateParameterError("; ".join(errors))

        if pipeline_engine is None:
            raise ValueError("pipeline_engine is required to instantiate a template")

        stages = tuple(_substitute_stage(stage, resolved_values) for stage in template.stages)
        return pipeline_engine.register(
            pipeline_name or template.name,
            stages,
            version=template.version,
            metadata={"template": template.name, "template_version": template.version},
            timestamp=timestamp,
        )


_registry = DeploymentTemplateRegistry()


def get_deployment_template_registry() -> DeploymentTemplateRegistry:
    return _registry


router = APIRouter(prefix="/governance", tags=["governance-templates"])


@router.post("/templates")
def register_template(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    stages_payload = payload.get("stages")
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
        parameters = [
            TemplateParameter(
                name=parameter["name"],
                required=parameter.get("required", False),
                default=parameter.get("default"),
            )
            for parameter in payload.get("parameters", [])
        ]
        template = get_deployment_template_registry().register(
            name,
            stages,
            version=payload.get("version", "1.0.0"),
            parameters=parameters,
            extends=payload.get("extends"),
            metadata=payload.get("metadata"),
        )
    except UnknownTemplateError:
        raise HTTPException(status_code=404, detail="unknown parent template")
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return template.to_dict()


@router.get("/templates")
def list_templates() -> list:
    return [template.to_dict() for template in get_deployment_template_registry().list()]


@router.post("/templates/{template}/instantiate")
def instantiate_template(template: str, payload: dict = Body(default={})) -> dict:
    from .deployment_pipeline import get_deployment_pipeline_engine

    try:
        pipeline = get_deployment_template_registry().instantiate(
            template,
            version=payload.get("version"),
            parameters=payload.get("parameters"),
            pipeline_name=payload.get("pipeline_name"),
            pipeline_engine=get_deployment_pipeline_engine(),
        )
    except UnknownTemplateError:
        raise HTTPException(status_code=404, detail="unknown template")
    except TemplateParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PipelineAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return pipeline.to_dict()
