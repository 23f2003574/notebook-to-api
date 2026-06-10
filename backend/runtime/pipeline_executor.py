from .pipeline_runtime import (
    PipelineRuntime
)

from .stage_registry import (
    StageRegistry
)

from .pipeline_contract_validator import (
    PipelineContractValidator
)

from .execution_report import (
    ExecutionReport,
    StageExecutionResult
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

        stage_reports = []

        for stage_name in stage_names:

            try:

                results[
                    stage_name
                ] = self.execute_stage(
                    stage_name,
                    runtime
                )

                stage_reports.append(
                    StageExecutionResult(
                        stage_name=stage_name,
                        success=True
                    )
                )

            except Exception as e:

                stage_reports.append(
                    StageExecutionResult(
                        stage_name=stage_name,
                        success=False,
                        error=str(e)
                    )
                )

                raise

        if expected_outputs:

            self.contract_validator\
                .validate_outputs(
                    runtime,
                    expected_outputs
                )

        execution_report = (
            ExecutionReport(
                stages=stage_reports,

                total_stages=len(
                    stage_reports
                ),

                successful_stages=sum(
                    1
                    for stage
                    in stage_reports
                    if stage.success
                ),

                failed_stages=sum(
                    1
                    for stage
                    in stage_reports
                    if not stage.success
                )
            )
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
                ),

            "execution_report":
                execution_report,
        }