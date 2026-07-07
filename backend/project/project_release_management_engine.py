from dataclasses import dataclass


@dataclass
class ProjectRelease:

    release_id: str

    version: str

    artifacts: list[str]

    release_notes: str


class ProjectReleaseManagementEngine:

    def create(
        self,
        version: str,
        artifacts: list[str]
    ):

        return ProjectRelease(

            release_id=
                "release-001",

            version=
                version,

            artifacts=
                artifacts,

            release_notes=
                ""
        )
