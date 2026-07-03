from dataclasses import dataclass


@dataclass
class ASTNode:

    node_type: str

    name: str | None

    children: list["ASTNode"]


class SemanticASTEngine:

    def build_ast(
        self,
        source: str
    ) -> ASTNode:

        return ASTNode(

            node_type="module",

            name=None,

            children=[]
        )
