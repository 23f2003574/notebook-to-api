from dataclasses import dataclass


@dataclass
class ProjectEnvironment:

    name: str

    python_version: str

    variables: dict[str, str]

    runtime_profile: str


class ProjectEnvironmentManager:

    def create(
        self,
        name: str
    ):

        return ProjectEnvironment(

            name=
                name,

            python_version=
                "3.12",

            variables={},

            runtime_profile=
                "development"
        )
