from dataclasses import dataclass


@dataclass
class DeploymentStage:

    name: str

    completed: bool


@dataclass
class InfrastructureDeployment:

    deployment_id: str

    application_id: str

    stages: list[DeploymentStage]


class InfrastructureDeploymentOrchestrator:

    def deploy(
        self,
        application_id: str
    ):

        return InfrastructureDeployment(

            deployment_id=
                "deployment-001",

            application_id=
                application_id,

            stages=[]
        )
