from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .export_service import ExportFormat, ModelExportService, get_model_export_service
from .inference_analytics import InferenceAnalyticsService, get_inference_analytics_service
from .model_benchmark import ModelBenchmarkService, get_model_benchmark_service
from .model_deployment import ModelDeploymentManager, get_model_deployment_manager
from .model_registry import ModelRegistry, get_model_registry


def _generated_at(timestamp: Optional[datetime] = None) -> str:
    return (timestamp or datetime.now(timezone.utc)).isoformat()


class ModelDashboardAPI:
    """Read-only aggregation over model inventory, deployments, benchmarks, and inference analytics."""

    def __init__(
        self,
        registry: Optional[ModelRegistry] = None,
        deployments: Optional[ModelDeploymentManager] = None,
        benchmarks: Optional[ModelBenchmarkService] = None,
        analytics: Optional[InferenceAnalyticsService] = None,
    ) -> None:
        self._registry = registry if registry is not None else get_model_registry()
        self._deployments = deployments if deployments is not None else get_model_deployment_manager()
        self._benchmarks = benchmarks if benchmarks is not None else get_model_benchmark_service()
        self._analytics = analytics if analytics is not None else get_inference_analytics_service()

    def models(self) -> dict:
        registered = self._registry.list_models()
        return {
            "total_models": len(registered),
            "models": [model.to_dict() for model in registered],
            "generated_at": _generated_at(),
        }

    def deployments(self) -> dict:
        all_deployments = self._deployments.list_deployments()
        by_target: dict = {}
        for deployment in all_deployments:
            by_target[deployment.target.value] = by_target.get(deployment.target.value, 0) + 1
        return {
            "total_deployments": len(all_deployments),
            "by_target": by_target,
            "recent_deployments": [deployment.to_dict() for deployment in all_deployments[-10:]],
            "generated_at": _generated_at(),
        }

    def _benchmarks_section(self) -> dict:
        results = self._benchmarks.list_results()
        return {
            "total_benchmarks": len(results),
            "recent_benchmarks": [result.to_dict() for result in results[-10:]],
            "generated_at": _generated_at(),
        }

    def analytics(self) -> dict:
        return {
            **self._analytics.summary(),
            "recent_activity": [record.to_dict() for record in self._analytics.recent(limit=10)],
            "generated_at": _generated_at(),
        }

    def overview(self) -> dict:
        return {
            "models": self.models(),
            "deployments": self.deployments(),
            "benchmarks": self._benchmarks_section(),
            "analytics": self.analytics(),
            "generated_at": _generated_at(),
        }


_model_dashboard_api = ModelDashboardAPI()


def get_model_dashboard_api() -> ModelDashboardAPI:
    return _model_dashboard_api


router = APIRouter(prefix="/ai/dashboard", tags=["ai-dashboard"])


@router.get("")
def get_dashboard_overview(api: ModelDashboardAPI = Depends(get_model_dashboard_api)) -> dict:
    return api.overview()


@router.get("/models")
def get_dashboard_models(api: ModelDashboardAPI = Depends(get_model_dashboard_api)) -> dict:
    return api.models()


@router.get("/deployments")
def get_dashboard_deployments(api: ModelDashboardAPI = Depends(get_model_dashboard_api)) -> dict:
    return api.deployments()


@router.get("/analytics")
def get_dashboard_analytics(api: ModelDashboardAPI = Depends(get_model_dashboard_api)) -> dict:
    return api.analytics()


export_router = APIRouter(prefix="/ai/export", tags=["ai-export"])


def _parse_format(value: str) -> ExportFormat:
    try:
        return ExportFormat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown export format")


@export_router.get("/models")
def export_models_endpoint(
    format: str = "json",
    service: ModelExportService = Depends(get_model_export_service),
) -> dict:
    fmt = _parse_format(format)
    export = service.export_models(fmt=fmt)
    return export.to_dict()


@export_router.get("/deployments")
def export_deployments_endpoint(
    format: str = "json",
    service: ModelExportService = Depends(get_model_export_service),
) -> dict:
    fmt = _parse_format(format)
    export = service.export_deployments(fmt=fmt)
    return export.to_dict()


@export_router.get("/benchmarks")
def export_benchmarks_endpoint(
    format: str = "json",
    service: ModelExportService = Depends(get_model_export_service),
) -> dict:
    fmt = _parse_format(format)
    export = service.export_benchmarks(fmt=fmt)
    return export.to_dict()


@export_router.get("/all")
def export_all_endpoint(
    format: str = "json",
    service: ModelExportService = Depends(get_model_export_service),
) -> dict:
    fmt = _parse_format(format)
    manifest = service.export_all(fmt=fmt)
    return manifest.to_dict()
