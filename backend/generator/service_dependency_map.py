from dataclasses import dataclass


@dataclass
class ServiceDependency:

    source: str

    target: str

    dependency_type: str


@dataclass
class ServiceDependencyMap:

    dependencies: list[ServiceDependency]

    dependency_count: int


class ServiceDependencyMapEngine:

    def generate(
        self
    ):

        dependencies = [

            ServiceDependency(

                source=
                    "api",

                target=
                    "database",

                dependency_type=
                    "storage"
            ),

            ServiceDependency(

                source=
                    "api",

                target=
                    "llm_provider",

                dependency_type=
                    "external_api"
            )
        ]

        return ServiceDependencyMap(

            dependencies=
                dependencies,

            dependency_count=
                len(dependencies)
        )
