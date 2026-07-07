from dataclasses import dataclass


@dataclass
class ProjectLifecycle:

    project_id: str

    initialized: bool

    build_ready: bool

    release_ready: bool


class ProjectLifecycleOrchestrator:

    def initialize(
        self,
        project_id: str
    ):

        return ProjectLifecycle(

            project_id=
                project_id,

            initialized=
                True,

            build_ready=
                True,

            release_ready=
                False
        )
