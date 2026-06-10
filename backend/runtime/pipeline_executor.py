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

        retries = 0

        while True:

            try:

                result = stage.execute(
                    runtime
                )

                return (
                    result,
                    retries
                )

            except Exception:

                retries += 1

                if (
                    retries
                    > stage.max_retries
                ):
                    raise

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

            retries = 0

            try:

                result, retries = (
                    self.execute_stage(
                        stage_name,
                        runtime
                    )
                )

                results[
                    stage_name
                ] = result

                stage_reports.append(
                    StageExecutionResult(
                        stage_name=stage_name,
                        success=True,
                        retry_count=retries
                    )
                )

            except Exception as e:

                stage_reports.append(
                    StageExecutionResult(
                        stage_name=stage_name,
                        success=False,
                        retry_count=retries,
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