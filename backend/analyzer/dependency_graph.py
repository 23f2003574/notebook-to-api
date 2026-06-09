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

    dependency_reasons: Dict[
        str,
        Set[str]
    ]


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
                    dependents=set(),
                    dependency_reasons={}
                )
            )

    def add_dependency(
        self,
        source: str,
        target: str,
        reason=None
    ):
        self.add_node(source)
        self.add_node(target)

        self.nodes[source].dependencies.add(
            target
        )

        if reason:

            self.nodes[source]\
                .dependency_reasons\
                .setdefault(
                    target,
                    set()
                )\
                .add(reason)

        self.nodes[target].dependents.add(
            source
        )

    def to_dict(self):

        result = {}

        for name, node in self.nodes.items():

            dependency_details = []

            for dependency in sorted(
                node.dependencies
            ):

                dependency_details.append(
                    {
                        "cell": dependency,
                        "variables": sorted(
                            node.dependency_reasons.get(
                                dependency,
                                set()
                            )
                        )
                    }
                )

            result[name] = {
                "dependencies": dependency_details,
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
                        "target": dependency,
                        "variables": sorted(
                            node.dependency_reasons.get(
                                dependency,
                                set()
                            )
                        )
                    }
                )

        return edges

    def dependency_provenance(self):

        provenance = {}

        for node_name, node in self.nodes.items():

            provenance[node_name] = {
                dependency: sorted(
                    variables
                )
                for dependency, variables
                in node.dependency_reasons.items()
            }

        return provenance

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

    def critical_path(self):

        memo = {}

        def longest_path(node_name):

            if node_name in memo:
                return memo[node_name]

            node = self.nodes[node_name]

            if not node.dependencies:
                memo[node_name] = [node_name]
                return memo[node_name]

            best_path = []

            for dependency in node.dependencies:

                candidate = longest_path(
                    dependency
                )

                if (
                    len(candidate)
                    > len(best_path)
                ):
                    best_path = candidate

            memo[node_name] = (
                [node_name]
                + best_path
            )

            return memo[node_name]

        longest = []

        for node_name in self.nodes:

            candidate = longest_path(
                node_name
            )

            if (
                len(candidate)
                > len(longest)
            ):
                longest = candidate

        return list(
            reversed(longest)
        )

    def critical_path_length(self):

        return len(
            self.critical_path()
        )

    def graph_metrics(self):

        return {
            "nodes": self.node_count(),
            "edges": self.edge_count(),

            "critical_path_length":
                self.critical_path_length(),

            "critical_path":
                self.critical_path(),

            "orphan_count":
                len(
                    self.orphan_nodes()
                ),

            "orphans":
                self.orphan_nodes()
        }

    def orphan_nodes(self):

        orphans = []

        for (
            node_name,
            node
        ) in self.nodes.items():

            if (
                not node.dependencies
                and not node.dependents
            ):
                orphans.append(
                    node_name
                )

        return sorted(
            orphans
        )

    def has_orphans(self):

        return bool(
            self.orphan_nodes()
        )

    def diagnostics(self):

        report = self.analysis_report()

        return {
            "orphans":
                report["orphans"],

            "has_cycles":
                report["cycle_detected"],

            "critical_path":
                report["critical_path"],

            "redundant_dependencies":
                report["redundant_dependencies"]
        }

    def analysis_report(self):

        cycle = self.find_cycle()
        has_cycle = cycle is not None

        if has_cycle:
            execution_order = []
            critical_path = []
        else:
            execution_order = self.execution_order()
            critical_path = self.critical_path()

        report = {
            "graph_metrics": {
                "nodes": self.node_count(),
                "edges": self.edge_count(),
                "critical_path_length":
                    len(critical_path),
                "critical_path":
                    critical_path,
                "orphan_count":
                    len(self.orphan_nodes()),
                "orphans":
                    self.orphan_nodes(),
                "partition_count":
                    self.partition_count(),
                "largest_partition_size":
                    self.largest_partition_size(),
                "entry_point_count":
                    len(
                        self.entry_points()
                    ),
                "terminal_node_count":
                    len(
                        self.terminal_nodes()
                    ),
            },

            "execution_order":
                execution_order,

            "critical_path":
                critical_path,

            "dependency_provenance":
                self.dependency_provenance(),

            "orphans":
                self.orphan_nodes(),

            "redundant_dependencies":
                self.redundant_dependencies(),

            "adjacency":
                self.to_adjacency_list(),

            "edges":
                self.to_edge_list(),

            "cycle_detected":
                has_cycle,

            "cycle":
                cycle
        }

        return report

    def connected_components(self):

        visited = set()

        components = []

        def dfs(node_name, component):

            if node_name in visited:
                return

            visited.add(
                node_name
            )

            component.add(
                node_name
            )

            neighbors = (
                self.nodes[node_name]
                .dependencies
                |
                self.nodes[node_name]
                .dependents
            )

            for neighbor in neighbors:

                dfs(
                    neighbor,
                    component
                )

        for node_name in self.nodes:

            if node_name not in visited:

                component = set()

                dfs(
                    node_name,
                    component
                )

                components.append(
                    sorted(component)
                )

        return components

    def partition_count(self):

        return len(
            self.connected_components()
        )

    def largest_partition_size(self):

        partitions = (
            self.connected_components()
        )

        if not partitions:
            return 0

        return max(
            len(partition)
            for partition
            in partitions
        )

    def entry_points(self):

        return sorted([
            node_name
            for node_name, node
            in self.nodes.items()
            if not node.dependencies
        ])

    def terminal_nodes(self):

        return sorted([
            node_name
            for node_name, node
            in self.nodes.items()
            if not node.dependents
        ])

    def stage_roles(self):

        return {
            "entry_points":
                self.entry_points(),

            "terminal_nodes":
                self.terminal_nodes()
        }

    def redundant_dependencies(self):

        redundant = []

        for (
            source_name,
            source_node
        ) in self.nodes.items():

            for dependency in (
                source_node.dependencies
            ):

                visited = set()

                for intermediate in (
                    source_node.dependencies
                ):

                    if (
                        intermediate
                        == dependency
                    ):
                        continue

                    self._reachable_nodes(
                        intermediate,
                        visited
                    )

                if dependency in visited:

                    redundant.append(
                        {
                            "source":
                                source_name,
                            "target":
                                dependency
                        }
                    )

        return redundant

    def _reachable_nodes(
        self,
        node_name,
        visited
    ):

        if node_name in visited:
            return

        visited.add(
            node_name
        )

        for dependency in (
            self.nodes[node_name]
            .dependencies
        ):
            self._reachable_nodes(
                dependency,
                visited
            )

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