from __future__ import annotations

import heapq
from dataclasses import dataclass


@dataclass(frozen=True)
class GovernanceComponent:
    """
    A registered governance component and the names of the
    components it depends on (must start before it does).
    """

    name: str

    dependencies: "tuple[str, ...]"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "dependencies": list(self.dependencies),
        }


@dataclass(frozen=True)
class DependencyValidationResult:
    """
    The immutable outcome of validating a GovernanceDependencyGraph.

    startup_order, cycles, and missing are tuples (not lists) even
    though callers typically want list-shaped JSON: a frozen
    dataclass only blocks reassigning its fields, not mutating a list
    stored in one, so tuples are what actually make "immutable
    validation results" true rather than merely documented.
    """

    valid: bool

    startup_order: "tuple[str, ...]"

    cycles: "tuple[tuple[str, ...], ...]"

    missing: "tuple[str, ...]"

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "startup_order": list(self.startup_order),
            "cycles": [list(cycle) for cycle in self.cycles],
            "missing": list(self.missing),
        }


class GovernanceDependencyGraph:
    """
    A directed graph of governance component dependencies, used to
    validate startup order and detect configuration issues (missing
    dependencies, circular dependencies) before the governance
    runtime actually starts.
    """

    def __init__(self) -> None:
        self._components: dict[str, GovernanceComponent] = {}

    def register(
        self,
        name: str,
        *,
        dependencies: "tuple[str, ...]" = (),
    ) -> None:
        """
        Register a component and the names of the components it
        depends on.

        Raises ValueError if name is already registered. A dependency
        does not need to be registered yet: registration order need
        not match dependency order, and any dependency that is still
        missing by the time validate() runs is reported there instead
        of being rejected eagerly here.
        """

        if name in self._components:
            raise ValueError(
                f"component '{name}' is already registered"
            )

        self._components[name] = GovernanceComponent(
            name=name,
            dependencies=tuple(dependencies),
        )

    def remove(self, name: str) -> None:
        """
        Remove a registered component.

        Raises KeyError if name is not registered. Does not touch any
        other component's reference to name as a dependency: a
        dangling reference surfaces as a missing dependency the next
        time validate() runs.
        """

        if name not in self._components:
            raise KeyError(f"component '{name}' is not registered")

        del self._components[name]

    def dependencies(self, name: str) -> "tuple[str, ...]":
        """
        Return the names name directly depends on.

        Raises KeyError if name is not registered.
        """

        if name not in self._components:
            raise KeyError(f"component '{name}' is not registered")

        return self._components[name].dependencies

    def components(self) -> "tuple[GovernanceComponent, ...]":
        """
        Return every registered component, ordered by name for
        deterministic output.
        """

        return tuple(
            self._components[name] for name in sorted(self._components)
        )

    def dependents(self, name: str) -> "tuple[str, ...]":
        """
        Return the names of every registered component that directly
        depends on name, ordered by name for deterministic output.

        Unlike dependencies(), this does not require name itself to
        be registered: a component can be depended upon before (or
        without ever) being registered, which is exactly the
        situation validate() reports as a missing dependency.
        """

        return tuple(
            sorted(
                component.name
                for component in self._components.values()
                if name in component.dependencies
            )
        )

    def validate(self) -> DependencyValidationResult:
        """
        Validate the graph: detect missing dependencies and circular
        dependencies, and compute a deterministic topological startup
        order if the graph is valid.

        A graph with missing dependencies or cycles is invalid, and
        its startup_order is empty — a total order cannot be
        meaningfully determined when a component cannot actually be
        constructed.
        """

        missing = self._missing_dependencies()
        cycles = self._detect_cycles()

        if missing or cycles:
            return DependencyValidationResult(
                valid=False,
                startup_order=(),
                cycles=cycles,
                missing=missing,
            )

        return DependencyValidationResult(
            valid=True,
            startup_order=self._topological_order(),
            cycles=(),
            missing=(),
        )

    def startup_order(self) -> "tuple[str, ...]":
        """
        Return the deterministic startup order for the currently
        registered components.

        Raises ValueError if the graph is not valid (a missing
        dependency or a cycle). Callers that need the full picture
        even when invalid should call validate() directly instead.
        """

        result = self.validate()

        if not result.valid:
            raise ValueError(
                "dependency graph is not valid; call validate() for "
                "details"
            )

        return result.startup_order

    def shutdown_order(self) -> "tuple[str, ...]":
        """
        Return the deterministic shutdown order: the exact reverse of
        startup_order(), so a component is only torn down after
        everything that depends on it has already stopped.

        Raises ValueError if the graph is not valid, matching
        startup_order().
        """

        return tuple(reversed(self.startup_order()))

    def _missing_dependencies(self) -> "tuple[str, ...]":
        missing = {
            dependency
            for component in self._components.values()
            for dependency in component.dependencies
            if dependency not in self._components
        }

        return tuple(sorted(missing))

    def _detect_cycles(self) -> "tuple[tuple[str, ...], ...]":
        """
        Depth-first search with white/gray/black coloring. Every
        distinct back-edge found is reported as one cycle, expressed
        as the path from the repeated node back to itself.
        """

        WHITE, GRAY, BLACK = 0, 1, 2

        color = {name: WHITE for name in self._components}

        cycles: "list[tuple[str, ...]]" = []

        path: "list[str]" = []

        def visit(name: str) -> None:
            color[name] = GRAY
            path.append(name)

            for dependency in self._components[name].dependencies:
                if dependency not in self._components:
                    # Missing dependency: reported separately by
                    # _missing_dependencies, not a cycle.
                    continue

                if color[dependency] == GRAY:
                    start = path.index(dependency)
                    cycles.append(tuple(path[start:] + [dependency]))

                elif color[dependency] == WHITE:
                    visit(dependency)

            path.pop()
            color[name] = BLACK

        for name in sorted(self._components):
            if color[name] == WHITE:
                visit(name)

        return tuple(cycles)

    def _topological_order(self) -> "tuple[str, ...]":
        """
        Kahn's algorithm using a min-heap keyed by component name, so
        that whenever more than one component is ready to start, the
        lexicographically smallest name is always chosen next. This
        makes the resulting order deterministic across runs
        regardless of registration order, rather than merely valid.
        """

        in_degree = {
            name: len(component.dependencies)
            for name, component in self._components.items()
        }

        dependents: "dict[str, list[str]]" = {
            name: [] for name in self._components
        }

        for component in self._components.values():
            for dependency in component.dependencies:
                dependents[dependency].append(component.name)

        ready = sorted(
            name for name, degree in in_degree.items() if degree == 0
        )

        heapq.heapify(ready)

        order: "list[str]" = []

        while ready:
            name = heapq.heappop(ready)
            order.append(name)

            for dependent in sorted(dependents[name]):
                in_degree[dependent] -= 1

                if in_degree[dependent] == 0:
                    heapq.heappush(ready, dependent)

        return tuple(order)
