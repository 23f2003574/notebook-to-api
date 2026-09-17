from dataclasses import dataclass
from typing import List

from .dependency_graph import (
    DependencyGraph
)

from .pipeline_endpoint_spec import (
    PipelineEndpointSpec
)


@dataclass
class ExecutionStage:
    nodes: List[str]

    def width(self):

        return len(
            self.nodes
        )


@dataclass
class ExecutionPlan:
    stages: List[ExecutionStage]

    def stage_count(self):
        return len(self.stages)

    def parallelizable_nodes(self):

        nodes = []

        for stage in self.stages:

            if stage.width() > 1:

                nodes.extend(
                    stage.nodes
                )

        return sorted(nodes)

    def sequential_nodes(self):

        nodes = []

        for stage in self.stages:

            if stage.width() == 1:

                nodes.extend(
                    stage.nodes
                )

        return nodes

    def total_nodes(self):

        return sum(
            len(stage.nodes)
            for stage
            in self.stages
        )

    def parallelism_score(self):

        total = self.total_nodes()

        if total == 0:
            return 0.0

        parallel = len(
            self.parallelizable_nodes()
        )

        return round(
            parallel / total,
            3
        )

    def largest_parallel_stage(self):

        if not self.stages:
            return 0

        return max(
            stage.width()
            for stage
            in self.stages
        )

    def bottleneck_stages(self):

        bottlenecks = []

        for idx, stage in enumerate(
            self.stages
        ):

            if stage.width() == 1:

                bottlenecks.append(
                    {
                        "stage_index": idx,
                        "nodes": stage.nodes
                    }
                )

        return bottlenecks

    def bottleneck_nodes(self):

        nodes = []

        for bottleneck in (
            self.bottleneck_stages()
        ):

            nodes.extend(
                bottleneck["nodes"]
            )

        return sorted(nodes)

    def bottleneck_ratio(self):

        total_stages = (
            self.stage_count()
        )

        if total_stages == 0:
            return 0.0

        return round(
            len(
                self.bottleneck_stages()
            )
            /
            total_stages,
            3
        )

    def execution_metrics(self):

        return {
            "stage_count":
                self.stage_count(),

            "total_nodes":
                self.total_nodes(),

            "parallelizable_nodes":
                len(
                    self.parallelizable_nodes()
                ),

            "parallelism_score":
                self.parallelism_score(),

            "largest_parallel_stage":
                self.largest_parallel_stage(),

            "bottleneck_count":
                len(
                    self.bottleneck_stages()
                ),

            "bottleneck_ratio":
                self.bottleneck_ratio()
        }


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