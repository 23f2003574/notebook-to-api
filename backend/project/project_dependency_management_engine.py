from dataclasses import dataclass


@dataclass
class ProjectDependency:

    name: str

    version: str

    dependency_type: str


@dataclass
class DependencyManifest:

    dependencies: list[ProjectDependency]


class ProjectDependencyManagementEngine:

    def create_manifest(
        self
    ):

        return DependencyManifest(

            dependencies=[]
        )
