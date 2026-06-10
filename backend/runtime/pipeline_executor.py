import time

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

from .execution_hooks import (
    ExecutionHooks
)

from .event_bus import (
    EventBus
)

from .execution_metrics import (
    ExecutionMetrics
)


class PipelineExecutor:

    def __init__(
        self,
        registry: StageRegistry,
        hooks: ExecutionHooks | None = None,
        event_bus: EventBus | None = None
    ):

        self.registry = registry

        self.hooks = (
            hooks
            or ExecutionHooks()
        )

        self.event_bus = (
            event_bus
            or EventBus()
        )

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

        self.hooks.before_pipeline(
            runtime
        )

        self.event_bus.publish(
            "pipeline_started"
        )

        results = {}

        stage_reports = []

        stage_durations = {}

        pipeline_start_time = (
            time.perf_counter()
        )

        for stage_name in stage_names:

            retries = 0

            try:

                stage_start_time = (
                    time.perf_counter()
                )

                self.hooks.before_stage(
                    stage_name,
                    runtime
                )

                self.event_bus.publish(
                    "stage_started",
                    {
                        "stage":
                            stage_name
                    }
                )

                result, retries = (
                    self.execute_stage(
                        stage_name,
                        runtime
                    )
                )

                results[
                    stage_name
                ] = result

                stage_durations[
                    stage_name
                ] = (
                    time.perf_counter()
                    -
                    stage_start_time
                )

                self.hooks.after_stage(
                    stage_name,
                    runtime,
                    result
                )

                self.event_bus.publish(
                    "stage_completed",
                    {
                        "stage":
                            stage_name
                    }
                )

                stage_reports.append(
                    StageExecutionResult(
                        stage_name=stage_name,
                        success=True,
                        retry_count=retries
                    )
                )

            except Exception as e:

                self.hooks.on_stage_failure(
                    stage_name,
                    runtime,
                    e
                )

                self.event_bus.publish(
                    "stage_failed",
                    {
                        "stage":
                            stage_name,
                        "error":
                            str(e)
                    }
                )

                stage_reports.append(
                    StageExecutionResult(
                        stage_name=stage_name,
                        success=False,
                        retry_count=retries,
                        error=str(e)
                    )
                )

                raise

        self.hooks.after_pipeline(
            runtime
        )

        self.event_bus.publish(
            "pipeline_completed"
        )

        if expected_outputs:

            self.contract_validator\
                .validate_outputs(
                    runtime,
                    expected_outputs
                )

        metrics = (
            ExecutionMetrics(
                stage_durations=
                    stage_durations,

                total_duration=
                    (
                        time.perf_counter()
                        -
                        pipeline_start_time
                    )
            )
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

            "execution_metrics":
                metrics,
        }