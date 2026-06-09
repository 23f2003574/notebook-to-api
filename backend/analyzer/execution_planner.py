from dataclasses import dataclass
from typing import List

from .dependency_graph import (
    DependencyGraph
)


@dataclass
class ExecutionStage:
    nodes: List[str]


@dataclass
class ExecutionPlan:
    stages: List[ExecutionStage]

    def stage_count(self):
        return len(self.stages)


class ExecutionPlanner:

    def build_plan(
        self,
        graph: DependencyGraph
    ) -> ExecutionPlan:

        remaining = {
            node_name: set(
                node.dependencies
            )
            for node_name, node
            in graph.nodes.items()
        }

        stages = []

        while remaining:

            ready = sorted([
                node_name
                for node_name, deps
                in remaining.items()
                if not deps
            ])

            if not ready:
                raise ValueError(
                    "Unable to build execution plan"
                )

            stages.append(
                ExecutionStage(
                    nodes=ready
                )
            )

            for node_name in ready:
                remaining.pop(
                    node_name
                )

            for deps in (
                remaining.values()
            ):
                deps.difference_update(
                    ready
                )

        return ExecutionPlan(
            stages=stages
        )