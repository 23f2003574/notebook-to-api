from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from time import perf_counter
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from .data_sources import DataSourceManager, get_data_source_manager
from .data_validation import (
    DataValidationEngine,
    InvalidValidationRuleError,
    ValidationFailedError,
    ValidationRule,
    get_data_validation_engine,
)
from .transformation_engine import (
    DataTransformationEngine,
    InvalidTransformationStepError,
    TransformationStep,
    get_data_transformation_engine,
)

POST_PROCESSING_ACTIONS = {"checksum", "notify", "archive"}


class ETLStage(str, Enum):
    EXTRACT = "extract"
    TRANSFORM = "transform"
    LOAD = "load"
    POST_PROCESSING = "post_processing"


_STAGE_ORDER = [stage.value for stage in ETLStage]


class WorkflowAlreadyExistsError(ValueError):
    pass


class UnknownWorkflowError(KeyError):
    pass


class LoadTargetError(ValueError):
    pass


class UnsupportedPostProcessingActionError(ValueError):
    pass


@dataclass(frozen=True)
class ETLWorkflow:
    """A configured Extract-Transform-Load workflow definition."""

    name: str
    source_name: str
    transform_steps: tuple
    load_target: str
    post_processing: tuple
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_name": self.source_name,
            "transform_steps": [step.to_dict() for step in self.transform_steps],
            "load_target": self.load_target,
            "post_processing": list(self.post_processing),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of running a workflow's stages, successful or not."""

    execution_id: str
    workflow_name: str
    status: str
    stages_completed: tuple
    failed_stage: Optional[str]
    error: Optional[str]
    row_count: int
    transform_result_id: Optional[str]
    started_at: datetime
    finished_at: datetime
    duration_ms: float

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "workflow_name": self.workflow_name,
            "status": self.status,
            "stages_completed": list(self.stages_completed),
            "failed_stage": self.failed_stage,
            "error": self.error,
            "row_count": self.row_count,
            "transform_result_id": self.transform_result_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
        }


class ETLWorkflowEngine:
    """Orchestrates staged extract/transform/load/post-processing workflow runs."""

    def __init__(self) -> None:
        self._workflows: dict = {}
        self._executions: list = []
        self._lock = Lock()

    def register_workflow(
        self,
        name: str,
        source_name: str,
        transform_steps: list,
        load_target: str,
        post_processing: tuple = (),
    ) -> ETLWorkflow:
        if not name:
            raise ValueError("workflow name is required")
        if not source_name:
            raise ValueError("source_name is required")
        if not load_target:
            raise ValueError("load_target is required")
        for action in post_processing:
            if action not in POST_PROCESSING_ACTIONS:
                raise UnsupportedPostProcessingActionError(action)
        with self._lock:
            if name in self._workflows:
                raise WorkflowAlreadyExistsError(name)
            workflow = ETLWorkflow(
                name=name,
                source_name=source_name,
                transform_steps=tuple(transform_steps),
                load_target=load_target,
                post_processing=tuple(post_processing),
                created_at=datetime.now(timezone.utc),
            )
            self._workflows[name] = workflow
            return workflow

    def get_workflow(self, name: str) -> ETLWorkflow:
        with self._lock:
            workflow = self._workflows.get(name)
        if workflow is None:
            raise UnknownWorkflowError(name)
        return workflow

    def extract(
        self,
        rows: list,
        *,
        sources: Optional[DataSourceManager] = None,
        source_name: Optional[str] = None,
    ) -> list:
        if sources is not None and source_name is not None:
            sources.mark_read(source_name)
        return list(rows)

    def transform(self, rows: list, steps: list, transformation_engine: DataTransformationEngine):
        return transformation_engine.transform(rows, steps)

    def load(self, rows: list, target: str) -> dict:
        if not target:
            raise LoadTargetError("load target is required")
        return {"target": target, "row_count": len(rows)}

    def post_process(self, actions: tuple) -> tuple:
        for action in actions:
            if action not in POST_PROCESSING_ACTIONS:
                raise UnsupportedPostProcessingActionError(action)
        return tuple(actions)

    def _validate_stage(
        self, rows: list, validation: DataValidationEngine, rules: list, stage_label: str
    ) -> None:
        report = validation.validate(rows, rules)
        if not report.passed:
            raise ValidationFailedError(f"{stage_label} validation failed with {len(report.issues)} issue(s)")

    def execute(
        self,
        name: str,
        rows: list,
        *,
        sources: Optional[DataSourceManager] = None,
        transformation_engine: Optional[DataTransformationEngine] = None,
        validation: Optional[DataValidationEngine] = None,
        pre_transform_rules: Optional[list] = None,
        post_transform_rules: Optional[list] = None,
    ) -> ExecutionResult:
        workflow = self.get_workflow(name)
        transformation_engine = transformation_engine or DataTransformationEngine()

        started_at = datetime.now(timezone.utc)
        start = perf_counter()
        stages_completed: list = []
        failed_stage = None
        error = None
        row_count = 0
        transform_result_id = None
        status = "success"

        try:
            extracted = self.extract(rows, sources=sources, source_name=workflow.source_name)
            if validation is not None and pre_transform_rules:
                self._validate_stage(extracted, validation, pre_transform_rules, ETLStage.EXTRACT.value)
            stages_completed.append(ETLStage.EXTRACT.value)

            transform_result = self.transform(extracted, list(workflow.transform_steps), transformation_engine)
            if validation is not None and post_transform_rules:
                self._validate_stage(list(transform_result.rows), validation, post_transform_rules, ETLStage.TRANSFORM.value)
            stages_completed.append(ETLStage.TRANSFORM.value)
            transform_result_id = transform_result.result_id

            load_summary = self.load(list(transform_result.rows), workflow.load_target)
            stages_completed.append(ETLStage.LOAD.value)
            row_count = load_summary["row_count"]

            self.post_process(workflow.post_processing)
            stages_completed.append(ETLStage.POST_PROCESSING.value)
        except Exception as exc:
            status = "failed"
            failed_stage = _STAGE_ORDER[len(stages_completed)]
            error = str(exc)

        finished_at = datetime.now(timezone.utc)
        duration_ms = (perf_counter() - start) * 1000

        result = ExecutionResult(
            execution_id=uuid4().hex,
            workflow_name=name,
            status=status,
            stages_completed=tuple(stages_completed),
            failed_stage=failed_stage,
            error=error,
            row_count=row_count,
            transform_result_id=transform_result_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        with self._lock:
            self._executions.append(result)
        return result

    def list_executions(self, workflow_name: Optional[str] = None) -> list:
        with self._lock:
            items = list(reversed(self._executions))
        if workflow_name is not None:
            items = [item for item in items if item.workflow_name == workflow_name]
        return items


_etl_workflow_engine = ETLWorkflowEngine()


def get_etl_workflow_engine() -> ETLWorkflowEngine:
    return _etl_workflow_engine


router = APIRouter(prefix="/pipelines/etl", tags=["pipeline-etl"])


@router.post("", status_code=201)
def register_workflow_endpoint(
    payload: dict = Body(default={}),
    engine: ETLWorkflowEngine = Depends(get_etl_workflow_engine),
) -> dict:
    try:
        steps = [TransformationStep.from_dict(step) for step in payload.get("transform_steps", [])]
    except (ValueError, InvalidTransformationStepError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        workflow = engine.register_workflow(
            payload.get("name", ""),
            payload.get("source_name", ""),
            steps,
            payload.get("load_target", ""),
            tuple(payload.get("post_processing", ())),
        )
    except WorkflowAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, UnsupportedPostProcessingActionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return workflow.to_dict()


@router.post("/execute")
def execute_workflow_endpoint(
    payload: dict = Body(default={}),
    engine: ETLWorkflowEngine = Depends(get_etl_workflow_engine),
    transformation_engine: DataTransformationEngine = Depends(get_data_transformation_engine),
    sources: DataSourceManager = Depends(get_data_source_manager),
    validation: DataValidationEngine = Depends(get_data_validation_engine),
) -> dict:
    try:
        pre_transform_rules = [ValidationRule.from_dict(rule) for rule in payload.get("pre_transform_rules", [])]
        post_transform_rules = [ValidationRule.from_dict(rule) for rule in payload.get("post_transform_rules", [])]
    except (ValueError, InvalidValidationRuleError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        result = engine.execute(
            payload.get("name", ""),
            payload.get("rows", []),
            sources=sources,
            transformation_engine=transformation_engine,
            validation=validation,
            pre_transform_rules=pre_transform_rules,
            post_transform_rules=post_transform_rules,
        )
    except UnknownWorkflowError:
        raise HTTPException(status_code=404, detail="unknown workflow")
    return result.to_dict()


@router.get("/{workflow}")
def get_workflow_endpoint(
    workflow: str,
    engine: ETLWorkflowEngine = Depends(get_etl_workflow_engine),
) -> dict:
    try:
        return engine.get_workflow(workflow).to_dict()
    except UnknownWorkflowError:
        raise HTTPException(status_code=404, detail="unknown workflow")
