from dataclasses import dataclass
from typing import Dict, List, Set
from collections import deque


class DependencyCycleError(
    Exception
):
    def __init__(
        self,
        cycle_path
    ):
        self.cycle_path = cycle_path

        super().__init__(
            "Dependency cycle detected: "
            + " -> ".join(cycle_path)
        )


@dataclass
class DependencyNode:
    name: str
    dependencies: Set[str]
    dependents: Set[str]


class DependencyGraph:

    def __init__(self):
        self.nodes: Dict[
            str,
            DependencyNode
        ] = {}

    def add_node(
        self,
        name: str
    ):
        if name not in self.nodes:
            self.nodes[name] = (
                DependencyNode(
                    name=name,
                    dependencies=set(),
                    dependents=set()
                )
            )

    def add_dependency(
        self,
        source: str,
        target: str
    ):
        self.add_node(source)
        self.add_node(target)

        self.nodes[source].dependencies.add(
            target
        )

        self.nodes[target].dependents.add(
            source
        )

    def to_dict(self):

        result = {}

        for name, node in self.nodes.items():

            result[name] = {
                "dependencies": sorted(
                    node.dependencies
                ),
                "dependents": sorted(
                    node.dependents
                )
            }

        return result

    def to_edge_list(self):

        edges = []

        for node_name, node in self.nodes.items():

            for dependency in node.dependencies:

                edges.append(
                    {
                        "source": node_name,
                        "target": dependency
                    }
                )

        return edges

    def to_adjacency_list(self):

        return {
            name: sorted(
                node.dependencies
            )
            for name, node
            in self.nodes.items()
        }

    def node_count(self):

        return len(
            self.nodes
        )

    def edge_count(self):

        return sum(
            len(node.dependencies)
            for node
            in self.nodes.values()
        )

    def topological_sort(self):

        indegree = {
            node_name: len(node.dependencies)
            for node_name, node
            in self.nodes.items()
        }

        queue = deque(
            node_name
            for node_name, degree
            in indegree.items()
            if degree == 0
        )

        ordering = []

        while queue:

            current = queue.popleft()

            ordering.append(current)

            for dependent in (
                self.nodes[current]
                .dependents
            ):

                indegree[dependent] -= 1

                if (
                    indegree[dependent]
                    == 0
                ):
                    queue.append(
                        dependent
                    )

        if (
            len(ordering)
            != len(self.nodes)
        ):

            cycle = self.find_cycle()

            raise DependencyCycleError(
                cycle or []
            )

        return ordering

    def execution_order(self):

        return self.topological_sort()

    def find_cycle(self):

        visited = set()
        recursion_stack = set()

        path = []

        def dfs(node_name):

            visited.add(node_name)

            recursion_stack.add(
                node_name
            )

            path.append(
                node_name
            )

            for dependency in (
                self.nodes[node_name]
                .dependencies
            ):

                if (
                    dependency
                    not in visited
                ):

                    result = dfs(
                        dependency
                    )

                    if result:
                        return result

                elif (
                    dependency
                    in recursion_stack
                ):

                    cycle_start = (
                        path.index(
                            dependency
                        )
                    )

                    return (
                        path[
                            cycle_start:
                        ]
                        + [dependency]
                    )

            recursion_stack.remove(
                node_name
            )

            path.pop()

            return None

        for node_name in self.nodes:

            if (
                node_name
                not in visited
            ):

                cycle = dfs(
                    node_name
                )

                if cycle:
                    return cycle

        return None