from dataclasses import dataclass


@dataclass
class ProjectArtifact:

    artifact_id: str

    project_id: str

    version: str

    artifact_type: str

    location: str


class ProjectArtifactRegistry:

    def register(
        self,
        artifact: ProjectArtifact
    ):

        return artifact
