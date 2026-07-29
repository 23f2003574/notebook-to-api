from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .plugin_registry import PluginRegistry, UnknownPluginError, get_plugin_registry


class CircularDependencyError(ValueError):
    pass


class UnsatisfiedDependencyError(ValueError):
    pass


def _parse_version(version: str) -> tuple:
    parts = []
    for segment in version.split("."):
        digits = ""
        for char in segment:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _compare_versions(left: str, right: str) -> int:
    left_parts, right_parts = _parse_version(left), _parse_version(right)
    length = max(len(left_parts), len(right_parts))
    left_parts = left_parts + (0,) * (length - len(left_parts))
    right_parts = right_parts + (0,) * (length - len(right_parts))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


_OPERATORS = (">=", "<=", "==", "!=", ">", "<")


def satisfies(version: str, constraint: str) -> bool:
    """Check a version string against a comma-separated constraint, e.g. '>=1.0.0,<2.0.0'."""
    if not constraint or constraint.strip() == "*":
        return True
    for clause in constraint.split(","):
        clause = clause.strip()
        if not clause:
            continue
        for operator in _OPERATORS:
            if clause.startswith(operator):
                target = clause[len(operator):].strip()
                comparison = _compare_versions(version, target)
                satisfied = {
                    ">=": comparison >= 0,
                    "<=": comparison <= 0,
                    "==": comparison == 0,
                    "!=": comparison != 0,
                    ">": comparison > 0,
                    "<": comparison < 0,
                }[operator]
                if not satisfied:
                    return False
                break
        else:
            if _compare_versions(version, clause) != 0:
                return False
    return True


@dataclass(frozen=True)
class PluginDependency:
    """A directed edge: `plugin` requires `depends_on` at a compatible version."""

    plugin: str
    depends_on: str
    version_constraint: str = "*"

    def to_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "depends_on": self.depends_on,
            "version_constraint": self.version_constraint,
        }


@dataclass(frozen=True)
class DependencyGraph:
    """A snapshot of the full dependency graph."""

    nodes: tuple
    edges: tuple

    def to_dict(self) -> dict:
        return {
            "nodes": list(self.nodes),
            "edges": [edge.to_dict() for edge in self.edges],
        }


class PluginDependencyManager:
    """Tracks inter-plugin dependencies and derives install/load ordering."""

    def __init__(self, registry: Optional[PluginRegistry] = None) -> None:
        self._registry = registry if registry is not None else get_plugin_registry()
        self._edges: dict = {}
        self._lock = Lock()

    def add_dependency(self, plugin: str, depends_on: str, version_constraint: str = "*") -> PluginDependency:
        if not plugin or not depends_on:
            raise ValueError("plugin and depends_on are required")
        if plugin == depends_on:
            raise ValueError("a plugin cannot depend on itself")
        with self._lock:
            self._edges.setdefault(plugin, {})[depends_on] = version_constraint
            self._edges.setdefault(depends_on, {})
        return PluginDependency(plugin=plugin, depends_on=depends_on, version_constraint=version_constraint)

    def get_dependencies(self, plugin: str) -> list:
        with self._lock:
            deps = dict(self._edges.get(plugin, {}))
        return [
            PluginDependency(plugin=plugin, depends_on=depends_on, version_constraint=constraint)
            for depends_on, constraint in sorted(deps.items())
        ]

    def get_graph(self) -> DependencyGraph:
        with self._lock:
            snapshot = {plugin: dict(deps) for plugin, deps in self._edges.items()}
        nodes = tuple(sorted(snapshot))
        edges = tuple(
            PluginDependency(plugin=plugin, depends_on=depends_on, version_constraint=constraint)
            for plugin in nodes
            for depends_on, constraint in sorted(snapshot[plugin].items())
        )
        return DependencyGraph(nodes=nodes, edges=edges)

    def _topological_order(self, roots) -> list:
        with self._lock:
            snapshot = {plugin: dict(deps) for plugin, deps in self._edges.items()}
        visiting: set = set()
        visited: set = set()
        order: list = []

        def visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise CircularDependencyError(f"circular dependency detected involving '{node}'")
            visiting.add(node)
            for dependency in snapshot.get(node, {}):
                visit(dependency)
            visiting.discard(node)
            visited.add(node)
            order.append(node)

        for root in roots:
            visit(root)
        return order

    def resolve(self, plugin: str) -> list:
        """Return `plugin` and all of its transitive dependencies, dependencies first."""
        return self._topological_order([plugin])

    def load_order(self) -> list:
        """Return every known plugin in an order where dependencies precede dependents."""
        with self._lock:
            roots = sorted(self._edges)
        return self._topological_order(roots)

    def validate(self, plugin: Optional[str] = None) -> bool:
        """Check dependency constraints (and absence of cycles) for one plugin or the whole graph."""
        if plugin is None:
            self.load_order()
            with self._lock:
                edges_to_check = [
                    PluginDependency(plugin=p, depends_on=dep, version_constraint=constraint)
                    for p, deps in self._edges.items()
                    for dep, constraint in deps.items()
                ]
        else:
            self.resolve(plugin)
            edges_to_check = self.get_dependencies(plugin)

        for edge in edges_to_check:
            try:
                installed = self._registry.get(edge.depends_on)
            except UnknownPluginError:
                raise UnsatisfiedDependencyError(
                    f"'{edge.plugin}' requires '{edge.depends_on}' but it is not installed"
                )
            if not satisfies(installed.version, edge.version_constraint):
                raise UnsatisfiedDependencyError(
                    f"'{edge.plugin}' requires '{edge.depends_on}{edge.version_constraint}' "
                    f"but installed version is '{installed.version}'"
                )
        return True


_plugin_dependency_manager = PluginDependencyManager()


def get_plugin_dependency_manager() -> PluginDependencyManager:
    return _plugin_dependency_manager


router = APIRouter(prefix="/plugins", tags=["plugins-dependencies"])


@router.post("/dependencies", status_code=201)
def add_dependency_endpoint(
    payload: dict = Body(default={}),
    manager: PluginDependencyManager = Depends(get_plugin_dependency_manager),
) -> dict:
    try:
        dependency = manager.add_dependency(
            payload.get("plugin", ""),
            payload.get("depends_on", ""),
            payload.get("version_constraint", "*"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return dependency.to_dict()


@router.get("/dependencies/{plugin}")
def get_dependencies_endpoint(
    plugin: str,
    manager: PluginDependencyManager = Depends(get_plugin_dependency_manager),
) -> list:
    return [dependency.to_dict() for dependency in manager.get_dependencies(plugin)]


@router.get("/load-order")
def load_order_endpoint(
    manager: PluginDependencyManager = Depends(get_plugin_dependency_manager),
) -> list:
    try:
        return manager.load_order()
    except CircularDependencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
