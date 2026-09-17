import ast
from dataclasses import dataclass
from typing import Set


@dataclass
class VariableUsage:
    defined: Set[str]
    used: Set[str]


class VariableTracker(ast.NodeVisitor):

    def __init__(self):
        self.defined = set()
        self.used = set()

    def visit_Name(
        self,
        node
    ):
        if isinstance(
            node.ctx,
            ast.Store
        ):
            self.defined.add(
                node.id
            )

        elif isinstance(
            node.ctx,
            ast.Load
        ):
            self.used.add(
                node.id
            )

        self.generic_visit(node)

    def analyze(
        self,
        source_code: str
    ) -> VariableUsage:

        tree = ast.parse(
            source_code
        )

        self.visit(tree)

        return VariableUsage(
            defined=self.defined,
            used=self.used
        )