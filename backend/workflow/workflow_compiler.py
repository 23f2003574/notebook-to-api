from dataclasses import dataclass


@dataclass
class CompiledWorkflow:

    workflow_id: str

    executable_id: str

    compiled_nodes: int

    target_runtime: str


class WorkflowCompiler:

    def compile(
        self,
        workflow,
        execution_plan
    ):

        return CompiledWorkflow(

            workflow_id=
                "workflow-001",

            executable_id=
                "exec-001",

            compiled_nodes=
                len(
                    workflow.nodes
                ),

            target_runtime=
                "notebook2api-runtime"
        )
