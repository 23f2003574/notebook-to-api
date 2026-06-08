from dataclasses import dataclass
from typing import Dict, List, Set


@dataclass
class DependencyNode:
    name: str
    dependencies: Set[str]
    dependents: Set[str]


class DependencyGraph:

    def __init__(self):
        self.nodes: Dict[
            str,
            DependencyNode
        ] = {}

    def add_node(
        self,
        name: str
    ):
        if name not in self.nodes:
            self.nodes[name] = (
                DependencyNode(
                    name=name,
                    dependencies=set(),
                    dependents=set()
                )
            )

    def add_dependency(
        self,
        source: str,
        target: str
    ):
        self.add_node(source)
        self.add_node(target)

        self.nodes[source].dependencies.add(
            target
        )

        self.nodes[target].dependents.add(
            source
        )