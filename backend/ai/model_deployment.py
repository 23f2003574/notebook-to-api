from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from .model_registry import ModelRegistry, UnknownModelError, get_model_registry
from .model_versioning import ModelVersionManager, get_model_version_manager


class DeploymentTarget(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    CANARY = "canary"
    PRODUCTION = "production"


_PROMOTION_ORDER = (
    DeploymentTarget.DEVELOPMENT,
    DeploymentTarget.STAGING,
    DeploymentTarget.CANARY,
    DeploymentTarget.PRODUCTION,
)


class UnknownDeploymentError(KeyError):
    pass


class InvalidDeploymentStateError(ValueError):
    pass


@dataclass(frozen=True)
class Deployment:
    """A model version published to a rollout environment, with its promotion history."""

    deployment_id: str
    model_name: str
    version: str
    target: DeploymentTarget
    last_action: str
    promotion_path: tuple
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "deployment_id": self.deployment_id,
            "model_name": self.model_name,
            "version": self.version,
            "target": self.target.value,
            "last_action": self.last_action,
            "promotion_path": [target.value for target in self.promotion_path],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ModelDeploymentManager:
    """Publishes model versions into rollout environments and tracks their promotion state."""

    def __init__(self) -> None:
        self._deployments: dict = {}
        self._lock = Lock()

    def deploy(
        self,
        model_name: str,
        version: Optional[str] = None,
        *,
        registry: ModelRegistry,
        versions: Optional[ModelVersionManager] = None,
        target: str = DeploymentTarget.DEVELOPMENT.value,
    ) -> Deployment:
        resolved_target = DeploymentTarget(target)

        if version is None:
            if versions is None:
                raise ValueError("version is required when no version manager is provided")
            version = versions.active_version(model_name)

        if not registry.is_registered(model_name, version=version):
            raise UnknownModelError(f"{model_name}@{version}")

        now = datetime.now(timezone.utc)
        deployment = Deployment(
            deployment_id=uuid4().hex,
            model_name=model_name,
            version=version,
            target=resolved_target,
            last_action="deployed",
            promotion_path=(resolved_target,),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._deployments[deployment.deployment_id] = deployment
        return deployment

    def promote(self, deployment_id: str) -> Deployment:
        with self._lock:
            deployment = self._deployments.get(deployment_id)
            if deployment is None:
                raise UnknownDeploymentError(deployment_id)
            current_index = _PROMOTION_ORDER.index(deployment.target)
            if current_index + 1 >= len(_PROMOTION_ORDER):
                raise InvalidDeploymentStateError(
                    f"deployment '{deployment_id}' is already at the final stage "
                    f"'{_PROMOTION_ORDER[-1].value}'"
                )
            next_target = _PROMOTION_ORDER[current_index + 1]
            promoted = replace(
                deployment,
                target=next_target,
                last_action="promoted",
                promotion_path=deployment.promotion_path + (next_target,),
                updated_at=datetime.now(timezone.utc),
            )
            self._deployments[deployment_id] = promoted
            return promoted

    def rollback(self, deployment_id: str) -> Deployment:
        with self._lock:
            deployment = self._deployments.get(deployment_id)
            if deployment is None:
                raise UnknownDeploymentError(deployment_id)
            if len(deployment.promotion_path) < 2:
                raise InvalidDeploymentStateError(
                    f"no earlier stage to roll back to for deployment '{deployment_id}'"
                )
            previous_target = deployment.promotion_path[-2]
            rolled_back = replace(
                deployment,
                target=previous_target,
                last_action="rolled_back",
                promotion_path=deployment.promotion_path[:-1],
                updated_at=datetime.now(timezone.utc),
            )
            self._deployments[deployment_id] = rolled_back
            return rolled_back

    def status(self, deployment_id: str) -> Deployment:
        with self._lock:
            deployment = self._deployments.get(deployment_id)
        if deployment is None:
            raise UnknownDeploymentError(deployment_id)
        return deployment

    def list_deployments(self) -> list:
        with self._lock:
            return sorted(self._deployments.values(), key=lambda deployment: deployment.created_at)


_model_deployment_manager = ModelDeploymentManager()


def get_model_deployment_manager() -> ModelDeploymentManager:
    return _model_deployment_manager


router = APIRouter(prefix="/ai/deployments", tags=["model-deployments"])


@router.post("", status_code=201)
def deploy_endpoint(
    payload: dict = Body(default={}),
    manager: ModelDeploymentManager = Depends(get_model_deployment_manager),
    registry: ModelRegistry = Depends(get_model_registry),
    versions: ModelVersionManager = Depends(get_model_version_manager),
) -> dict:
    try:
        deployment = manager.deploy(
            payload.get("model_name", ""),
            payload.get("version"),
            registry=registry,
            versions=versions,
            target=payload.get("target", DeploymentTarget.DEVELOPMENT.value),
        )
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return deployment.to_dict()


@router.post("/{deployment}/promote")
def promote_endpoint(
    deployment: str,
    manager: ModelDeploymentManager = Depends(get_model_deployment_manager),
) -> dict:
    try:
        promoted = manager.promote(deployment)
    except UnknownDeploymentError:
        raise HTTPException(status_code=404, detail="unknown deployment")
    except InvalidDeploymentStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return promoted.to_dict()


@router.post("/{deployment}/rollback")
def rollback_endpoint(
    deployment: str,
    manager: ModelDeploymentManager = Depends(get_model_deployment_manager),
) -> dict:
    try:
        rolled_back = manager.rollback(deployment)
    except UnknownDeploymentError:
        raise HTTPException(status_code=404, detail="unknown deployment")
    except InvalidDeploymentStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return rolled_back.to_dict()


@router.get("/{deployment}")
def get_deployment_endpoint(
    deployment: str,
    manager: ModelDeploymentManager = Depends(get_model_deployment_manager),
) -> dict:
    try:
        return manager.status(deployment).to_dict()
    except UnknownDeploymentError:
        raise HTTPException(status_code=404, detail="unknown deployment")
