from dataclasses import dataclass


@dataclass
class BuildStep:

    name: str

    completed: bool


@dataclass
class ProjectBuild:

    project_id: str

    steps: list[BuildStep]

    artifact_path: str


class ProjectBuildSystem:

    def build(
        self,
        project_id: str
    ):

        return ProjectBuild(

            project_id=
                project_id,

            steps=[

                BuildStep(

                    name=
                        "compile",

                    completed=
                        True
                ),

                BuildStep(

                    name=
                        "package",

                    completed=
                        True
                )
            ],

            artifact_path=
                "build/output"
        )
