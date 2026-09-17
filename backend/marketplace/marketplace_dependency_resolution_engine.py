from dataclasses import dataclass


@dataclass
class ExtensionDependency:

    extension_id: str

    version_constraint: str


@dataclass
class DependencyResolution:

    resolved: bool

    installation_order: list[str]

    conflicts: list[str]


class MarketplaceDependencyResolutionEngine:

    def resolve(
        self,
        dependencies: list[ExtensionDependency]
    ):

        return DependencyResolution(

            resolved=
                True,

            installation_order=[],

            conflicts=[]
        )
