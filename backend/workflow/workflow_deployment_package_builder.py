from dataclasses import dataclass


@dataclass
class WorkflowArtifact:

    name: str

    artifact_type: str

    path: str


@dataclass
class WorkflowDeploymentPackage:

    package_name: str

    runtime: str

    artifacts: list[WorkflowArtifact]


class WorkflowDeploymentPackageBuilder:

    def build(
        self,
        compiled_workflow
    ):

        return WorkflowDeploymentPackage(

            package_name=
                f"{compiled_workflow.workflow_id}.workflow",

            runtime=
                compiled_workflow.target_runtime,

            artifacts=[

                WorkflowArtifact(

                    name=
                        "workflow.json",

                    artifact_type=
                        "definition",

                    path=
                        "workflow.json"
                ),

                WorkflowArtifact(

                    name=
                        "execution_plan.json",

                    artifact_type=
                        "execution_plan",

                    path=
                        "execution_plan.json"
                ),

                WorkflowArtifact(

                    name=
                        "metadata.json",

                    artifact_type=
                        "metadata",

                    path=
                        "metadata.json"
                )
            ]
        )
