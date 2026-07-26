from __future__ import annotations

import heapq
from dataclasses import dataclass
from threading import Lock

from fastapi import APIRouter, Body, HTTPException, Query


class UnknownNodeError(KeyError):
    pass


class UnknownDependencyError(KeyError):
    pass


class CycleDetectedError(RuntimeError):
    pass


@dataclass(frozen=True)
class DependencyNode:
    """A stage participating in the dependency graph."""

    name: str

    def to_dict(self) -> dict:
        return {"name": self.name}


@dataclass(frozen=True)
class DependencyEdge:
    """A directed edge: `stage` depends on `depends_on` running first."""

    stage: str
    depends_on: str

    def to_dict(self) -> dict:
        return {"stage": self.stage, "depends_on": self.depends_on}


class DeploymentDependencyGraph:
    """Tracks stage dependency edges and computes a valid execution order."""

    def __init__(self) -> None:
        self._nodes: dict[str, DependencyNode] = {}
        self._edges: dict[str, set] = {}
        self._lock = Lock()

    def add_dependency(self, stage: str, depends_on: str) -> DependencyEdge:
        if not stage or not depends_on:
            raise ValueError("stage and depends_on are required")
        if stage == depends_on:
            raise ValueError("a stage cannot depend on itself")

        with self._lock:
            self._nodes.setdefault(stage, DependencyNode(name=stage))
            self._nodes.setdefault(depends_on, DependencyNode(name=depends_on))
            self._edges.setdefault(stage, set()).add(depends_on)
        return DependencyEdge(stage=stage, depends_on=depends_on)

    def remove_dependency(self, stage: str, depends_on: str) -> None:
        with self._lock:
            edges = self._edges.get(stage)
            if not edges or depends_on not in edges:
                raise UnknownDependencyError(f"{stage} -> {depends_on}")
            edges.discard(depends_on)

    def dependencies_of(self, stage: str) -> tuple:
        with self._lock:
            if stage not in self._nodes:
                raise UnknownNodeError(stage)
            return tuple(sorted(self._edges.get(stage, ())))

    def dependents_of(self, stage: str) -> tuple:
        with self._lock:
            if stage not in self._nodes:
                raise UnknownNodeError(stage)
            return tuple(
                sorted(node for node, deps in self._edges.items() if stage in deps)
            )

    def nodes(self) -> tuple:
        with self._lock:
            return tuple(self._nodes[name] for name in sorted(self._nodes))

    def validate(self) -> dict:
        with self._lock:
            node_names = set(self._nodes)
            edges = {stage: set(deps) for stage, deps in self._edges.items()}

        cycles = self._detect_cycles(node_names, edges)
        if cycles:
            return {"valid": False, "order": (), "cycles": cycles}
        return {"valid": True, "order": self._topological_order(node_names, edges), "cycles": ()}

    def execution_order(self) -> tuple:
        result = self.validate()
        if not result["valid"]:
            raise CycleDetectedError(f"dependency graph has cycles: {result['cycles']}")
        return result["order"]

    def _detect_cycles(self, node_names: set, edges: dict) -> tuple:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {name: WHITE for name in node_names}
        cycles: list = []
        path: list = []

        def visit(name: str) -> None:
            color[name] = GRAY
            path.append(name)
            for dependency in sorted(edges.get(name, ())):
                if color[dependency] == GRAY:
                    start = path.index(dependency)
                    cycles.append(tuple(path[start:] + [dependency]))
                elif color[dependency] == WHITE:
                    visit(dependency)
            path.pop()
            color[name] = BLACK

        for name in sorted(node_names):
            if color[name] == WHITE:
                visit(name)
        return tuple(cycles)

    def _topological_order(self, node_names: set, edges: dict) -> tuple:
        in_degree = {name: len(edges.get(name, ())) for name in node_names}
        successors: dict = {name: [] for name in node_names}
        for stage, deps in edges.items():
            for dependency in deps:
                successors[dependency].append(stage)

        ready = sorted(name for name, degree in in_degree.items() if degree == 0)
        heapq.heapify(ready)

        order: list = []
        while ready:
            name = heapq.heappop(ready)
            order.append(name)
            for successor in sorted(successors[name]):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    heapq.heappush(ready, successor)

        return tuple(order)


_graph = DeploymentDependencyGraph()


def get_deployment_dependency_graph() -> DeploymentDependencyGraph:
    return _graph


router = APIRouter(prefix="/governance", tags=["governance-dependency-graph"])


@router.post("/dependencies")
def add_dependency_endpoint(payload: dict = Body(...)) -> dict:
    stage = payload.get("stage")
    depends_on = payload.get("depends_on")
    if not stage or not depends_on:
        raise HTTPException(status_code=422, detail="stage and depends_on are required")

    try:
        edge = get_deployment_dependency_graph().add_dependency(stage, depends_on)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return edge.to_dict()


@router.delete("/dependencies")
def remove_dependency_endpoint(
    stage: str = Query(...), depends_on: str = Query(...)
) -> dict:
    try:
        get_deployment_dependency_graph().remove_dependency(stage, depends_on)
    except UnknownDependencyError:
        raise HTTPException(status_code=404, detail="dependency edge not found")
    return {"stage": stage, "depends_on": depends_on, "removed": True}


@router.get("/dependencies/order")
def get_execution_order_endpoint() -> dict:
    try:
        order = get_deployment_dependency_graph().execution_order()
    except CycleDetectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"order": list(order)}
