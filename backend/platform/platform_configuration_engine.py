from dataclasses import dataclass


@dataclass
class PlatformConfiguration:

    compiler_optimization_level: str

    runtime_worker_count: int

    workflow_retry_limit: int

    deployment_environment: str


class PlatformConfigurationEngine:

    def load(
        self
    ):

        return PlatformConfiguration(

            compiler_optimization_level=
                "O2",

            runtime_worker_count=
                4,

            workflow_retry_limit=
                3,

            deployment_environment=
                "development"
        )
