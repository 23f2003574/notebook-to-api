from dataclasses import dataclass


@dataclass
class InfrastructureLifecycle:

    infrastructure_id: str

    initialized: bool

    operational: bool

    deployment_ready: bool


class CloudInfrastructureLifecycleOrchestrator:

    def initialize(
        self,
        infrastructure_id: str
    ):

        return InfrastructureLifecycle(

            infrastructure_id=
                infrastructure_id,

            initialized=
                True,

            operational=
                True,

            deployment_ready=
                False
        )
