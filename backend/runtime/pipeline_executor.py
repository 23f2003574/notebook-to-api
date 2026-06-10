from .pipeline_runtime import (
    PipelineRuntime
)

from .stage_registry import (
    StageRegistry
)


class PipelineExecutor:

    def __init__(
        self,
        registry: StageRegistry
    ):

        self.registry = registry

    def execute_stage(
        self,
        stage_name: str,
        runtime: PipelineRuntime
    ):

        stage = self.registry.get(
            stage_name
        )

        return stage.execute(
            runtime
        )

    def execute_pipeline(
        self,
        stage_names,
        runtime: PipelineRuntime
    ):

        results = {}

        for stage_name in stage_names:

            results[
                stage_name
            ] = self.execute_stage(
                stage_name,
                runtime
            )

        return {
            "stage_results":
                results,

            "runtime_context":
                runtime.all_values()
        }