from dataclasses import dataclass


@dataclass
class WorkflowDeployment:

    deployment_id: str

    package_name: str

    target_environment: str

    status: str


class WorkflowDeploymentManager:

    def deploy(
        self,
        deployment_package,
        environment: str
    ):

        return WorkflowDeployment(

            deployment_id=
                "deployment-001",

            package_name=
                deployment_package.package_name,

            target_environment=
                environment,

            status=
                "deployed"
        )
