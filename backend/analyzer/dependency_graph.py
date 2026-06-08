from dataclasses import dataclass
from typing import Dict, List, Set
from collections import deque


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
            raise ValueError(
                "Cycle detected in dependency graph"
            )

        return ordering

    def execution_order(self):

        return self.topological_sort()