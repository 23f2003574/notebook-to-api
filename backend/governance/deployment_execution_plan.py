from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_dependency_graph import CycleDetectedError, DeploymentDependencyGraph
from .deployment_pipeline import DeploymentPipelineEngine


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownPlanError(KeyError):
    pass


class PlanValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionStep:
    """One stage of an execution plan and the parallel wave it belongs to."""

    stage: str
    group: int
    depends_on: tuple = ()

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "group": self.group,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class ExecutionPlan:
    """An immutable, validated plan compiled from a pipeline and its dependency graph."""

    plan_id: str
    pipeline: str
    version: str
    steps: tuple = ()
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "pipeline": self.pipeline,
            "version": self.version,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DeploymentExecutionPlanBuilder:
    """Compiles registered pipelines into validated, parallel-grouped execution plans."""

    def __init__(
        self,
        pipeline_engine: Optional[DeploymentPipelineEngine] = None,
        dependency_graph: Optional[DeploymentDependencyGraph] = None,
    ) -> None:
        self._plans: dict[str, ExecutionPlan] = {}
        self._lock = Lock()
        self._pipeline_engine = pipeline_engine
        self._dependency_graph = dependency_graph

    def build(
        self,
        pipeline: str,
        *,
        pipeline_engine: Optional[DeploymentPipelineEngine] = None,
        dependency_graph: Optional[DeploymentDependencyGraph] = None,
        timestamp: Optional[datetime] = None,
    ) -> ExecutionPlan:
        engine = pipeline_engine or self._pipeline_engine
        if engine is None:
            raise ValueError("pipeline_engine is required to build a plan")
        graph = dependency_graph or self._dependency_graph

        pipeline_def = engine.get(pipeline)
        stage_names = tuple(stage.name for stage in pipeline_def.stages)
        grouped = self._group_stages(stage_names, graph)

        dependency_lookup: dict = {}
        if graph is not None:
            graph_node_names = {node.name for node in graph.nodes()}
            for stage in stage_names:
                if stage in graph_node_names:
                    dependency_lookup[stage] = graph.dependencies_of(stage)

        steps = tuple(
            ExecutionStep(
                stage=stage, group=group_index, depends_on=dependency_lookup.get(stage, ())
            )
            for group_index, group in enumerate(grouped)
            for stage in group
        )

        plan = ExecutionPlan(
            plan_id=_new_id(),
            pipeline=pipeline,
            version=pipeline_def.version,
            steps=steps,
            created_at=timestamp or datetime.now(timezone.utc),
        )

        result = self.validate(plan)
        if not result["valid"]:
            raise PlanValidationError("; ".join(result["errors"]))

        with self._lock:
            self._plans[plan.plan_id] = plan
        return plan

    def validate(self, plan: ExecutionPlan) -> dict:
        errors: list = []
        seen: set = set()
        group_of: dict = {}

        for step in plan.steps:
            if step.stage in seen:
                errors.append(f"stage '{step.stage}' appears more than once in the plan")
            seen.add(step.stage)
            group_of[step.stage] = step.group
            if step.group < 0:
                errors.append(f"stage '{step.stage}' has a negative group number")

        for step in plan.steps:
            for dependency in step.depends_on:
                if dependency in group_of and group_of[dependency] >= step.group:
                    errors.append(
                        f"stage '{step.stage}' in group {step.group} depends on "
                        f"'{dependency}' in group {group_of[dependency]}"
                    )

        return {"valid": not errors, "errors": tuple(errors)}

    def optimize(self, plan_id: str) -> ExecutionPlan:
        plan = self._get(plan_id)
        used_groups = sorted({step.group for step in plan.steps})
        remap = {old: new for new, old in enumerate(used_groups)}
        steps = tuple(
            sorted(
                (replace(step, group=remap[step.group]) for step in plan.steps),
                key=lambda step: (step.group, step.stage),
            )
        )
        optimized = replace(plan, steps=steps)

        with self._lock:
            self._plans[plan_id] = optimized
        return optimized

    def preview(self, plan_id: str) -> ExecutionPlan:
        return self._get(plan_id)

    def _group_stages(
        self, stage_names: tuple, graph: Optional[DeploymentDependencyGraph]
    ) -> list:
        if graph is None:
            return [(name,) for name in stage_names]

        try:
            levels = graph.execution_levels()
        except CycleDetectedError as exc:
            raise PlanValidationError(str(exc))

        grouped = [tuple(name for name in level if name in stage_names) for level in levels]
        grouped = [group for group in grouped if group]
        placed = {name for group in grouped for name in group}
        leftover = tuple(name for name in stage_names if name not in placed)
        if leftover:
            grouped.append(leftover)
        return grouped

    def _get(self, plan_id: str) -> ExecutionPlan:
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            raise UnknownPlanError(plan_id)
        return plan


_builder = DeploymentExecutionPlanBuilder()


def get_deployment_execution_plan_builder() -> DeploymentExecutionPlanBuilder:
    return _builder


router = APIRouter(prefix="/governance", tags=["governance-execution-plan"])


@router.post("/execution-plans")
def create_execution_plan(payload: dict = Body(...)) -> dict:
    from .deployment_dependency_graph import get_deployment_dependency_graph
    from .deployment_pipeline import UnknownPipelineError, get_deployment_pipeline_engine

    pipeline = payload.get("pipeline")
    if not pipeline:
        raise HTTPException(status_code=422, detail="pipeline is required")

    try:
        plan = get_deployment_execution_plan_builder().build(
            pipeline,
            pipeline_engine=get_deployment_pipeline_engine(),
            dependency_graph=get_deployment_dependency_graph(),
        )
    except UnknownPipelineError:
        raise HTTPException(status_code=404, detail="unknown pipeline")
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return plan.to_dict()


@router.get("/execution-plans/{plan_id}")
def get_execution_plan(plan_id: str) -> dict:
    try:
        plan = get_deployment_execution_plan_builder().preview(plan_id)
    except UnknownPlanError:
        raise HTTPException(status_code=404, detail="unknown execution plan")
    return plan.to_dict()
