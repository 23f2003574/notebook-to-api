from dataclasses import dataclass


@dataclass
class ProjectWorkspace:

    project_id: str

    name: str

    notebooks: list[str]

    created: str


class ProjectWorkspaceEngine:

    def create(
        self,
        name: str
    ):

        return ProjectWorkspace(

            project_id=
                "project-001",

            name=
                name,

            notebooks=[],

            created=
                "2026-07-07"
        )
