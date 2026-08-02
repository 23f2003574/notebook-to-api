from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .export_service import ExportFormat, PipelineExportService, get_pipeline_export_service
from .pipeline_analytics import PipelineAnalyticsService, get_pipeline_analytics_service
from .pipeline_executor import PipelineExecutionEngine, get_pipeline_execution_engine
from .pipeline_scheduler import PipelineScheduler, get_pipeline_scheduler
from .schema_registry import SchemaRegistry, get_schema_registry


def _generated_at(timestamp: Optional[datetime] = None) -> str:
    return (timestamp or datetime.now(timezone.utc)).isoformat()


class PipelineDashboardAPI:
    """Read-only aggregation over pipeline execution, scheduling, validation, and analytics."""

    def __init__(
        self,
        executor: Optional[PipelineExecutionEngine] = None,
        scheduler: Optional[PipelineScheduler] = None,
        analytics: Optional[PipelineAnalyticsService] = None,
        schemas: Optional[SchemaRegistry] = None,
    ) -> None:
        self._executor = executor if executor is not None else get_pipeline_execution_engine()
        self._scheduler = scheduler if scheduler is not None else get_pipeline_scheduler()
        self._analytics = analytics if analytics is not None else get_pipeline_analytics_service()
        self._schemas = schemas if schemas is not None else get_schema_registry()

    def executions(self) -> dict:
        runs = self._executor.list_runs()
        by_state: dict = {}
        for run in runs:
            by_state[run.state.value] = by_state.get(run.state.value, 0) + 1
        return {
            "total_runs": len(runs),
            "by_state": by_state,
            "recent_runs": [run.to_dict() for run in runs[:10]],
            "generated_at": _generated_at(),
        }

    def schedules(self) -> dict:
        all_schedules = self._scheduler.list_schedules()
        active = [schedule for schedule in all_schedules if schedule.status == "active"]
        return {
            "total_schedules": len(all_schedules),
            "active_count": len(active),
            "upcoming": [schedule.to_dict() for schedule in self._scheduler.upcoming(limit=10)],
            "generated_at": _generated_at(),
        }

    def analytics(self) -> dict:
        return {
            **self._analytics.summary(),
            "recent_activity": [record.to_dict() for record in self._analytics.recent(limit=10)],
            "generated_at": _generated_at(),
        }

    def _validation(self) -> dict:
        schemas = self._schemas.list_schemas()
        total_versions = sum(len(schema.versions) for schema in schemas)
        return {
            "schemas_registered": len(schemas),
            "total_versions": total_versions,
            "generated_at": _generated_at(),
        }

    def overview(self) -> dict:
        return {
            "executions": self.executions(),
            "schedules": self.schedules(),
            "analytics": self.analytics(),
            "validation": self._validation(),
            "generated_at": _generated_at(),
        }


_pipeline_dashboard_api = PipelineDashboardAPI()


def get_pipeline_dashboard_api() -> PipelineDashboardAPI:
    return _pipeline_dashboard_api


router = APIRouter(prefix="/pipelines/dashboard", tags=["pipeline-dashboard"])


@router.get("")
def get_dashboard_overview(api: PipelineDashboardAPI = Depends(get_pipeline_dashboard_api)) -> dict:
    return api.overview()


@router.get("/executions")
def get_dashboard_executions(api: PipelineDashboardAPI = Depends(get_pipeline_dashboard_api)) -> dict:
    return api.executions()


@router.get("/schedules")
def get_dashboard_schedules(api: PipelineDashboardAPI = Depends(get_pipeline_dashboard_api)) -> dict:
    return api.schedules()


@router.get("/analytics")
def get_dashboard_analytics(api: PipelineDashboardAPI = Depends(get_pipeline_dashboard_api)) -> dict:
    return api.analytics()


export_router = APIRouter(prefix="/pipelines/export", tags=["pipeline-export"])


def _parse_format(value: str) -> ExportFormat:
    try:
        return ExportFormat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown export format")


@export_router.get("/definitions")
def export_definitions_endpoint(
    format: str = "json",
    names: Optional[str] = None,
    service: PipelineExportService = Depends(get_pipeline_export_service),
) -> dict:
    fmt = _parse_format(format)
    name_list = [name.strip() for name in names.split(",")] if names else None
    export = service.export_pipeline(fmt=fmt, names=name_list)
    return export.to_dict()


@export_router.get("/runs")
def export_runs_endpoint(
    format: str = "json",
    workflow_name: Optional[str] = None,
    service: PipelineExportService = Depends(get_pipeline_export_service),
) -> dict:
    fmt = _parse_format(format)
    export = service.export_runs(fmt=fmt, workflow_name=workflow_name)
    return export.to_dict()


@export_router.get("/schemas")
def export_schemas_endpoint(
    format: str = "json",
    service: PipelineExportService = Depends(get_pipeline_export_service),
) -> dict:
    fmt = _parse_format(format)
    export = service.export_schemas(fmt=fmt)
    return export.to_dict()


@export_router.get("/all")
def export_all_endpoint(
    format: str = "json",
    service: PipelineExportService = Depends(get_pipeline_export_service),
) -> dict:
    fmt = _parse_format(format)
    manifest = service.export_all(fmt=fmt)
    return manifest.to_dict()
