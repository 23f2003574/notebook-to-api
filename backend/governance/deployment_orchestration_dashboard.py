from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException

from .deployment_automation import DeploymentAutomationEngine
from .deployment_pipeline import DeploymentPipelineEngine
from .deployment_pipeline_recovery import DeploymentPipelineRecoveryManager
from .deployment_scheduler import DeploymentScheduler
from .deployment_workflow import DeploymentWorkflowEngine
from .deployment_workflow_analytics import DeploymentWorkflowAnalyticsService


class DeploymentOrchestrationDashboard:
    """Read-only aggregation of every orchestration service into a single dashboard view."""

    def __init__(
        self,
        pipeline_engine: Optional[DeploymentPipelineEngine] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        scheduler: Optional[DeploymentScheduler] = None,
        automation_engine: Optional[DeploymentAutomationEngine] = None,
        pipeline_recovery_manager: Optional[DeploymentPipelineRecoveryManager] = None,
        analytics_service: Optional[DeploymentWorkflowAnalyticsService] = None,
    ) -> None:
        self._lock = Lock()
        self._cache: Optional[dict] = None
        self._pipeline_engine = pipeline_engine
        self._workflow_engine = workflow_engine
        self._scheduler = scheduler
        self._automation_engine = automation_engine
        self._pipeline_recovery_manager = pipeline_recovery_manager
        self._analytics_service = analytics_service

    def overview(self, *, refresh: bool = False, **overrides) -> dict:
        if refresh:
            return self.refresh(**overrides)
        with self._lock:
            cached = self._cache
        if cached is not None:
            return cached
        return self.refresh(**overrides)

    def refresh(
        self,
        *,
        pipeline_engine: Optional[DeploymentPipelineEngine] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        scheduler: Optional[DeploymentScheduler] = None,
        automation_engine: Optional[DeploymentAutomationEngine] = None,
        analytics_service: Optional[DeploymentWorkflowAnalyticsService] = None,
        timestamp: Optional[datetime] = None,
    ) -> dict:
        pipeline_engine = pipeline_engine or self._pipeline_engine
        workflow_engine = workflow_engine or self._workflow_engine
        scheduler = scheduler or self._scheduler
        automation_engine = automation_engine or self._automation_engine
        analytics_service = analytics_service or self._analytics_service

        pipelines = tuple(pipeline_engine.list()) if pipeline_engine is not None else ()

        workflows: list = []
        if workflow_engine is not None:
            for pipeline in pipelines:
                workflows.extend(workflow_engine.executions_for(pipeline.name))
        status_counts: dict = {}
        for execution in workflows:
            status_counts[execution.status] = status_counts.get(execution.status, 0) + 1

        schedules = tuple(scheduler.pending()) if scheduler is not None else ()
        rules = tuple(automation_engine.list_rules()) if automation_engine is not None else ()
        analytics_summary = (
            analytics_service.summarize_all() if analytics_service is not None else None
        )

        snapshot = {
            "generated_at": (timestamp or datetime.now(timezone.utc)).isoformat(),
            "pipelines": {
                "total": len(pipelines),
                "items": [pipeline.to_dict() for pipeline in pipelines],
            },
            "workflows": {
                "total": len(workflows),
                "by_status": status_counts,
            },
            "schedules": {
                "pending": len(schedules),
                "items": [schedule.to_dict() for schedule in schedules],
            },
            "automation": {
                "total_rules": len(rules),
                "enabled_rules": sum(1 for rule in rules if rule.enabled),
            },
            "analytics": analytics_summary.to_dict() if analytics_summary is not None else None,
        }
        with self._lock:
            self._cache = snapshot
        return snapshot

    def pipeline(
        self,
        name: Optional[str] = None,
        *,
        pipeline_engine: Optional[DeploymentPipelineEngine] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        analytics_service: Optional[DeploymentWorkflowAnalyticsService] = None,
    ):
        engine = pipeline_engine or self._pipeline_engine
        if engine is None:
            raise ValueError("pipeline_engine is required")
        wf_engine = workflow_engine or self._workflow_engine
        analytics = analytics_service or self._analytics_service

        if name is not None:
            return self._pipeline_summary(engine.get(name), wf_engine, analytics)
        return [self._pipeline_summary(item, wf_engine, analytics) for item in engine.list()]

    def workflow(
        self,
        execution_id: str,
        *,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        pipeline_recovery_manager: Optional[DeploymentPipelineRecoveryManager] = None,
        analytics_service: Optional[DeploymentWorkflowAnalyticsService] = None,
    ) -> dict:
        engine = workflow_engine or self._workflow_engine
        if engine is None:
            raise ValueError("workflow_engine is required")
        execution = engine.status(execution_id)

        recovery_manager = pipeline_recovery_manager or self._pipeline_recovery_manager
        recovery_history = (
            recovery_manager.history(execution_id) if recovery_manager is not None else ()
        )

        analytics = analytics_service or self._analytics_service
        pipeline_analytics = analytics.summarize(execution.pipeline) if analytics is not None else None

        return {
            "execution": execution.to_dict(),
            "recovery_history": [record.to_dict() for record in recovery_history],
            "analytics": pipeline_analytics.to_dict() if pipeline_analytics is not None else None,
        }

    def _pipeline_summary(
        self,
        definition,
        workflow_engine: Optional[DeploymentWorkflowEngine],
        analytics_service: Optional[DeploymentWorkflowAnalyticsService],
    ) -> dict:
        executions = (
            workflow_engine.executions_for(definition.name) if workflow_engine is not None else ()
        )
        summary = (
            analytics_service.summarize(definition.name) if analytics_service is not None else None
        )
        return {
            "pipeline": definition.to_dict(),
            "active_executions": sum(
                1 for execution in executions if execution.status in ("RUNNING", "PAUSED")
            ),
            "total_executions": len(executions),
            "analytics": summary.to_dict() if summary is not None else None,
        }


_dashboard = DeploymentOrchestrationDashboard()


def get_deployment_orchestration_dashboard() -> DeploymentOrchestrationDashboard:
    return _dashboard


router = APIRouter(prefix="/governance", tags=["governance-orchestration-dashboard"])


@router.get("/orchestration")
def get_overview() -> dict:
    from .deployment_automation import get_deployment_automation_engine
    from .deployment_pipeline import get_deployment_pipeline_engine
    from .deployment_scheduler import get_deployment_scheduler
    from .deployment_workflow import get_deployment_workflow_engine
    from .deployment_workflow_analytics import get_deployment_workflow_analytics_service

    return get_deployment_orchestration_dashboard().overview(
        pipeline_engine=get_deployment_pipeline_engine(),
        workflow_engine=get_deployment_workflow_engine(),
        scheduler=get_deployment_scheduler(),
        automation_engine=get_deployment_automation_engine(),
        analytics_service=get_deployment_workflow_analytics_service(),
    )


@router.get("/orchestration/pipelines")
def list_pipeline_summaries() -> list:
    from .deployment_pipeline import get_deployment_pipeline_engine
    from .deployment_workflow import get_deployment_workflow_engine
    from .deployment_workflow_analytics import get_deployment_workflow_analytics_service

    return get_deployment_orchestration_dashboard().pipeline(
        pipeline_engine=get_deployment_pipeline_engine(),
        workflow_engine=get_deployment_workflow_engine(),
        analytics_service=get_deployment_workflow_analytics_service(),
    )


@router.get("/orchestration/workflows/{execution_id}")
def get_workflow_summary(execution_id: str) -> dict:
    from .deployment_pipeline_recovery import get_deployment_pipeline_recovery_manager
    from .deployment_workflow import UnknownExecutionError, get_deployment_workflow_engine
    from .deployment_workflow_analytics import get_deployment_workflow_analytics_service

    try:
        return get_deployment_orchestration_dashboard().workflow(
            execution_id,
            workflow_engine=get_deployment_workflow_engine(),
            pipeline_recovery_manager=get_deployment_pipeline_recovery_manager(),
            analytics_service=get_deployment_workflow_analytics_service(),
        )
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
