from dataclasses import dataclass


@dataclass
class AiApplicationLifecycle:

    application_id: str

    initialized: bool

    evaluation_ready: bool

    deployment_ready: bool


class AiApplicationLifecycleOrchestrator:

    def initialize(
        self,
        application_id: str
    ):

        return AiApplicationLifecycle(

            application_id=
                application_id,

            initialized=
                True,

            evaluation_ready=
                True,

            deployment_ready=
                False
        )
