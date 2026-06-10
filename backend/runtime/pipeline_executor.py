from .pipeline_runtime import (
    PipelineRuntime
)

from .stage_registry import (
    StageRegistry
)

from .pipeline_contract_validator import (
    PipelineContractValidator
)


class PipelineExecutor:

    def __init__(
        self,
        registry: StageRegistry
    ):

        self.registry = registry

        self.contract_validator = (
            PipelineContractValidator()
        )

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
        runtime: PipelineRuntime,
        inputs=None,
        expected_outputs=None
    ):

        if inputs:

            runtime.load_inputs(
                inputs
            )

        results = {}

        for stage_name in stage_names:

            results[
                stage_name
            ] = self.execute_stage(
                stage_name,
                runtime
            )

        if expected_outputs:

            self.contract_validator\
                .validate_outputs(
                    runtime,
                    expected_outputs
                )

        return {
            "stage_results":
                results,

            "runtime_context":
                runtime.all_values(),

            "outputs":
                runtime.export_outputs(
                    expected_outputs
                    or []
                )
        }